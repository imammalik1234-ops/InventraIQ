"""T04 tests use fresh in-memory databases, never configured data."""
import unittest
from datetime import date, datetime
from pathlib import Path
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import mysql, sqlite
from database import db
from models import (Branch, User, UserBranch, UserHQScope, Category, SKU, Product,
                    Inventory, Sale, InventoryAudit, StockLot, BranchProduct, BranchStock,
                    LegacyProductMapping, CategoryExpiryPolicy, ProductExpiryPolicyOverride)
from branch_foundation import seed_demo_branches, backfill_legacy
from scripts.render_t04_schema import render, LEGACY_TABLES


class BranchFoundationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        db.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.ids = seed_demo_branches(self.session)
        self.session.add(Category(category_id=1, code='REVIEWED', display_name='Reviewed category'))
        self.session.flush()
        self.session.add_all([SKU(sku_id=i, code='SKU-'+str(i), name='Variant '+str(i), category_id=1)
                              for i in (1, 2)])
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def reject(self, row):
        with self.assertRaises(IntegrityError):
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()

    def lot(self, sku_id=1, code='LOT'):
        lot = StockLot(sku_id=sku_id, lot_code=code, provenance={'fixture': True})
        self.session.add(lot); self.session.flush()
        return lot

    def assortment(self, branch=None, sku=1):
        self.session.add(BranchProduct(branch_id=branch or self.ids[0], sku_id=sku))
        self.session.flush()

    def legacy(self):
        for i, expiry, category, supplier in [(1, date(2030, 1, 1), 'Dairy', 'A'),
                                               (2, date(2030, 2, 1), 'Dairy', 'B'),
                                               (3, None, None, None)]:
            self.session.add(Product(product_id=i, name='Same legacy name', category=category,
                                     supplier=supplier, expiry_date=expiry, unit_price=None))
        self.session.flush()
        self.session.add_all([Inventory(inventory_id=1, product_id=1, quantity=7),
                              Inventory(inventory_id=2, product_id=2, quantity=4),
                              Sale(sale_id=1, product_id=1, quantity=3, sale_date=date(2026, 1, 1)),
                              InventoryAudit(actor_id=99, product_id=1, action='legacy',
                                             old_value={'unchanged': True}, timestamp=datetime(2026, 1, 1))])
        self.session.commit()
        return {i: dict(sku_id=1 if i < 3 else 2,
                        expiry_timezone='Asia/Singapore' if i < 3 else None,
                        expires_at_utc=datetime(2030, i, 1, 16) if i < 3 else None)
                for i in (1, 2, 3)}

    def test_seed_repeat_exact_three_and_fourth_supported(self):
        self.assertEqual(seed_demo_branches(self.session), self.ids)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Branch)), 3)
        self.assertEqual(self.session.scalars(select(Branch.code).order_by(Branch.code)).all(),
                         ['DEMO-01', 'DEMO-02', 'DEMO-03'])
        self.session.add(Branch(code='FOUR', display_name='Fourth', timezone='UTC'))
        self.session.flush()
        seed_demo_branches(self.session)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Branch)), 4)

    def test_seed_conflict_does_not_overwrite(self):
        branch = self.session.get(Branch, self.ids[0]); branch.timezone = 'UTC'
        self.session.flush()
        with self.assertRaises(ValueError): seed_demo_branches(self.session)
        self.assertEqual(branch.timezone, 'UTC')

    def test_branch_unique_and_required(self):
        self.reject(Branch(code='DEMO-01', display_name='Duplicate', timezone='UTC'))
        self.reject(Branch(code='EMPTY', display_name='Invalid', timezone=None))
        self.reject(Branch(code='', display_name='Invalid', timezone='UTC'))

    def test_assignments_reuse_users_no_default_scope(self):
        self.session.add(User(user_id=1, email='fixture@example.invalid', password_hash='unused'))
        self.session.flush()
        self.assertEqual(self.session.scalar(select(func.count()).select_from(UserBranch)), 0)
        self.session.add_all([UserBranch(user_id=1, branch_id=b, branch_role='clerk') for b in self.ids])
        self.session.add(UserHQScope(user_id=1, hq_role='analyst'))
        self.session.flush()
        self.assertEqual(self.session.scalar(select(func.count()).select_from(User)), 1)
        self.reject(UserBranch(user_id=1, branch_id=self.ids[0], branch_role='clerk'))
        self.reject(UserBranch(user_id=999, branch_id=self.ids[0], branch_role='clerk'))
        self.reject(UserHQScope(user_id=999, hq_role='admin'))
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Branch)), 3)

    def test_sku_identity_not_expiry_or_supplier(self):
        self.assertNotIn('expiry_date', SKU.__table__.columns)
        self.assertNotIn('supplier', SKU.__table__.columns)
        self.reject(SKU(code='SKU-1', name='Other', category_id=1))
        self.reject(SKU(code='BAD', name='Other', category_id=999))
        self.lot(code='A'); self.lot(code='B')
        self.reject(StockLot(sku_id=1, lot_code='A', provenance={}))
        self.lot(sku_id=2, code='A')

    def test_stock_requires_assortment_and_matching_lot_sku(self):
        lot = self.lot(); self.assortment()
        self.reject(BranchStock(branch_id=self.ids[1], lot_id=lot.lot_id, sku_id=1, on_hand=4))
        self.assortment(sku=2)
        self.reject(BranchStock(branch_id=self.ids[0], lot_id=lot.lot_id, sku_id=2, on_hand=4))
        self.session.add(BranchStock(branch_id=self.ids[0], lot_id=lot.lot_id, sku_id=1, on_hand=4))
        self.session.flush()
        self.reject(BranchStock(branch_id=self.ids[0], lot_id=lot.lot_id, sku_id=1, on_hand=4))

    def test_stock_disposition_and_version_checks(self):
        lot=self.lot(); self.assortment()
        for values in [dict(on_hand=-1), dict(on_hand=4, reserved=5),
                       dict(on_hand=4, reserved=2, quarantined_quantity=3),
                       dict(on_hand=4, quarantined_quantity=-1), dict(on_hand=4, version=0)]:
            self.reject(BranchStock(branch_id=self.ids[0], lot_id=lot.lot_id, sku_id=1, **values))

    def test_lot_expiry_null_tuple_and_review_state(self):
        self.lot()
        self.reject(StockLot(sku_id=1, lot_code='BAD', expiry_date=date(2030, 1, 1), provenance={}))
        self.reject(StockLot(sku_id=999, lot_code='ORPHAN', provenance={}))
        self.reject(StockLot(sku_id=1, lot_code='STATE', provenance={}, validation_state='invented'))

    def test_policy_constraints_and_full_override(self):
        valid=dict(require_expiry=True, early_days=7, high_days=3, critical_days=1,
                   min_remaining_days_on_receipt=0, min_remaining_days_on_transfer_receipt=1,
                   revision=1, effective_at=datetime(2026, 1, 1))
        for change in [dict(early_days=3), dict(critical_days=-1), dict(early_days=3651),
                       dict(revision=0), dict(expected_shelf_life_days=-1)]:
            self.reject(CategoryExpiryPolicy(category_id=1, **dict(valid, **change)))
        self.reject(ProductExpiryPolicyOverride(sku_id=1, require_expiry=False))
        self.session.add(CategoryExpiryPolicy(category_id=1, **valid))
        self.session.add(ProductExpiryPolicyOverride(sku_id=1, **valid)); self.session.flush()
        self.reject(ProductExpiryPolicyOverride(sku_id=1, **valid))

    def test_legacy_mapping_one_branch_preserves_history_unknowns_and_quantity(self):
        mapping=self.legacy()
        result=backfill_legacy(self.session, legacy_branch_id=self.ids[1], approved_mapping=mapping,
                               approval_reference='fixture-reviewed-manifest-v1')
        self.session.commit()
        self.assertEqual(result, dict(products=3, quantity=11, legacy_branch_id=self.ids[1]))
        self.assertEqual(set(self.session.scalars(select(BranchStock.branch_id))), {self.ids[1]})
        self.assertEqual(self.session.scalar(select(func.sum(BranchStock.on_hand))), 11)
        self.assertEqual(self.session.scalar(select(func.sum(BranchStock.quarantined_quantity))), 11)
        self.assertEqual(self.session.scalar(select(func.sum(Inventory.quantity))), 11)
        self.assertEqual(self.session.get(Sale, 1).quantity, 3)
        self.assertEqual(self.session.scalar(select(InventoryAudit)).old_value, {'unchanged': True})
        self.assertEqual(self.session.scalar(select(func.count()).select_from(StockLot)), 3)
        self.assertIsNone(self.session.get(SKU, 1).default_price)
        for old in self.session.scalars(select(Product)):
            link=self.session.get(LegacyProductMapping, old.product_id)
            self.assertEqual(self.session.get(StockLot, link.lot_id).expiry_date, old.expiry_date)
            self.assertEqual(link.evidence['historical_sale_price'], 'unknown')
        with self.assertRaises(ValueError):
            backfill_legacy(self.session, legacy_branch_id=self.ids[0], approved_mapping=mapping, approval_reference='repeat')

    def test_failed_manifest_and_rollback_leave_source_unchanged(self):
        mapping=self.legacy(); mapping[2]['expires_at_utc']=None
        with self.assertRaises(ValueError):
            backfill_legacy(self.session, legacy_branch_id=self.ids[0], approved_mapping=mapping, approval_reference='review')
        self.session.rollback()
        self.assertEqual(self.session.scalar(select(func.count()).select_from(StockLot)), 0)
        mapping[2]['expires_at_utc']=datetime(2030, 2, 1, 16)
        backfill_legacy(self.session, legacy_branch_id=self.ids[0], approved_mapping=mapping, approval_reference='review')
        self.session.rollback()  # Pre-cutover restore rehearsal: discard expansion data, keep source.
        self.assertEqual(self.session.scalar(select(func.count()).select_from(LegacyProductMapping)), 0)
        self.assertEqual(self.session.scalar(select(func.sum(Inventory.quantity))), 11)
        self.assertEqual(self.session.get(Sale, 1).quantity, 3)

    def test_empty_mapping_and_missing_approval(self):
        self.assertEqual(backfill_legacy(self.session, legacy_branch_id=self.ids[0], approved_mapping={}, approval_reference='empty')['quantity'], 0)
        mapping=self.legacy()
        for branch, manifest, approval in [(None, mapping, 'ok'), (self.ids[0], {}, 'ok'), (self.ids[0], mapping, '')]:
            with self.assertRaises(ValueError):
                backfill_legacy(self.session, legacy_branch_id=branch, approved_mapping=manifest, approval_reference=approval)

    def test_referenced_branch_and_lot_deletion_restricted(self):
        lot=self.lot(); self.assortment()
        self.session.add(BranchStock(branch_id=self.ids[0], lot_id=lot.lot_id, sku_id=1, on_hand=4)); self.session.flush()
        for table, key, value in [('branches', 'branch_id', self.ids[0]), ('stock_lots', 'lot_id', lot.lot_id)]:
            with self.assertRaises(IntegrityError):
                with self.session.begin_nested():
                    self.session.execute(text(f'DELETE FROM {table} WHERE {key}=:value'), {'value': value})

    def test_sql_artifacts_match_metadata_and_expand_legacy_fixture(self):
        for name, dialect in [('mysql', mysql.dialect()), ('sqlite', sqlite.dialect())]:
            artifact=Path('migrations/t04_v001_expand_'+name+'.sql').read_text(encoding='utf-8')
            self.assertEqual(artifact, render(dialect))
            self.assertNotIn('DROP ', artifact)
            self.assertNotIn('ALTER ', artifact)
        import sqlite3
        connection=sqlite3.connect(':memory:')
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            for table in db.metadata.sorted_tables:
                if table.name in LEGACY_TABLES:
                    from sqlalchemy.schema import CreateTable
                    connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
            connection.execute("INSERT INTO products (product_id,name) VALUES (1,'Legacy')")
            connection.executescript(render(sqlite.dialect()))
            self.assertEqual(connection.execute('SELECT name FROM products').fetchall(), [('Legacy',)])
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally: connection.close()
