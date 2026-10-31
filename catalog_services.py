"""HQ catalog and policy services. No commits, no legacy-row rewrites."""
import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, func
from database import db
from models import (Category, SKU, SKUIdentity, CategoryExpiryPolicy, ProductExpiryPolicyOverride,
                    BranchProduct, Branch, StockLot, BranchStock, CatalogAudit, CompanyExpirySettings, LotIdentity)
from authz import resolve_scope, integer_id
from security import MutationError
from expiry import Policy, POLICY_FIELDS, validate_policy, utcnow, zone, canonical_expiry, conversion_provenance


def text(value, label, limit):
    if not isinstance(value,str) or not value.strip() or len(value.strip()) > limit or any(ord(c)<32 for c in value):
        raise MutationError(f'{label} is required and must fit {limit} characters.')
    return unicodedata.normalize('NFC',value.strip())


def code(value, limit=80):
    value=text(value,'Stable code',limit).upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9_.-]*',value):raise MutationError('Use an uppercase stable code with letters, numbers, dots, underscores or hyphens.')
    return value


def price(value):
    try:
        if isinstance(value,bool):raise InvalidOperation()
        result=Decimal(str(value))
        if not result.is_finite() or not Decimal(0) <= result <= Decimal('99999999.99') or result != result.quantize(Decimal('.01')):raise InvalidOperation()
    except (ValueError,InvalidOperation):raise MutationError('Price must be a nonnegative amount with at most two decimal places.') from None
    return result


def revision(value, current):
    from expiry import integer
    if integer(value,'Expected revision',0,2147483647) != current:
        raise MutationError('This record changed. Reload and review before retrying.',409)


def _record(scope, kind, key, version, reason, before, after):
    db.session.add(CatalogAudit(actor_id=scope.actor_id,entity_type=kind,entity_key=str(key),revision=version,
        reason=text(reason,'Reason',255),old_value=before,new_value=after,occurred_at=utcnow().replace(tzinfo=None)))


def sku_snapshot(sku):
    return dict(sku_id=sku.sku_id,code=sku.code,name=sku.name,category_id=sku.category_id,unit=sku.unit,
                variant_attributes=sku.variant_attributes or {},default_price=str(sku.default_price) if sku.default_price is not None else None,
                active=sku.active,version=sku.version)


def signature(name,unit,attrs):
    def normalized(value):return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())
    data=[normalized(name),normalized(unit),sorted((normalized(k),normalized(v)) for k,v in attrs.items())]
    return hashlib.sha256(json.dumps(data,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()


def lot_signature(sku_id, supplier_lot, actual_label):
    # Zone is deliberately excluded: a changed company setting must not allow
    # a second interpretation of the same existing supplier batch/date.
    return hashlib.sha256(json.dumps([sku_id,supplier_lot,actual_label],separators=(',',':')).encode()).hexdigest()


def _adopt_identity(sku):
    if db.session.get(SKUIdentity,sku.sku_id) is None:
        attrs=sku.variant_attributes or {}
        if not isinstance(attrs,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in attrs.items()):
            raise MutationError('Legacy variant attributes require headquarters review before adoption.',409)
        db.session.add(SKUIdentity(sku_id=sku.sku_id,signature=signature(sku.name,sku.unit,attrs)))


def _effective_policy(sku, *, lock=False, shared=False):
    override_query=select(ProductExpiryPolicyOverride).where(ProductExpiryPolicyOverride.sku_id==sku.sku_id).execution_options(populate_existing=True)
    category_query=select(CategoryExpiryPolicy).where(CategoryExpiryPolicy.category_id==sku.category_id).execution_options(populate_existing=True)
    override=db.session.scalar(override_query.with_for_update(read=shared) if lock else override_query)
    row=override or db.session.scalar(category_query.with_for_update(read=shared) if lock else category_query)
    return _policy_value(sku,row,override is not None)


def _policy_value(sku,row,overridden):
    if row is None:return None
    try: values=validate_policy({field:getattr(row,field) for field in POLICY_FIELDS})
    except MutationError:return None
    return Policy(source='sku' if overridden else 'category',key=sku.sku_id if overridden else sku.category_id,
                  revision=row.revision,effective_at=row.effective_at.isoformat()+'Z',**values)


def catalog_list(include_archived=False):
    resolve_scope('inventory.read')
    # One read for all displayed policy values. Mutation callers retain the
    # separate locking _effective_policy path; no cross-request cache is used.
    query=(select(SKU,ProductExpiryPolicyOverride,CategoryExpiryPolicy).select_from(SKU)
        .outerjoin(ProductExpiryPolicyOverride,ProductExpiryPolicyOverride.sku_id==SKU.sku_id)
        .outerjoin(CategoryExpiryPolicy,CategoryExpiryPolicy.category_id==SKU.category_id)
        .order_by(SKU.code).execution_options(populate_existing=True))
    if not include_archived:query=query.where(SKU.active.is_(True))
    result=[]
    for sku,override,category in db.session.execute(query):
        policy=_policy_value(sku,override or category,override is not None)
        result.append(dict(sku_snapshot(sku),policy=policy.snapshot() if policy else None))
    return result


def category_list():
    resolve_scope('inventory.read')
    return [dict(category_id=c.category_id,code=c.code,display_name=c.display_name)
            for c in db.session.scalars(select(Category).order_by(Category.code))]


def create_category(values):
    scope=resolve_scope('catalog.manage',consolidated=True,lock=True)
    if not isinstance(values,dict) or set(values)-{'code','display_name','reason'}:raise MutationError('Invalid category fields.')
    category=Category(code=code(values.get('code')),display_name=text(values.get('display_name'),'Category name',100))
    db.session.add(category);db.session.flush()
    _record(scope,'category',category.category_id,1,values.get('reason'),None,
           dict(code=category.code,display_name=category.display_name))
    return dict(category_id=category.category_id,code=category.code,display_name=category.display_name)


def _hold_undated(scope, sku_ids, reason):
    if not sku_ids:return []
    lots=db.session.scalars(select(StockLot).where(StockLot.sku_id.in_(sku_ids),StockLot.expiry_date.is_(None)).order_by(StockLot.lot_id).with_for_update()).all()
    affected=[]
    for lot in lots:
        stocks=db.session.scalars(select(BranchStock).where(BranchStock.lot_id==lot.lot_id).order_by(BranchStock.branch_id).with_for_update()).all()
        if any(stock.reserved for stock in stocks):
            raise MutationError('Reserved undated stock needs ledger reconciliation before changing this policy.',409)
        if lot.validation_state != 'review':
            before=dict(validation_state=lot.validation_state,version=lot.version)
            lot.validation_state='review';lot.version+=1
            _record(scope,'lot',lot.lot_id,lot.version,reason,before,dict(validation_state='review',version=lot.version))
        affected.extend(dict(branch_id=s.branch_id,lot_id=lot.lot_id,on_hand=s.on_hand) for s in stocks)
    return affected


def save_sku(values, sku_id=None):
    scope=resolve_scope('catalog.manage',consolidated=True,lock=True)
    allowed={'code','name','category_id','unit','variant_attributes','default_price','active','expected_version','reason'}
    if not isinstance(values,dict) or set(values)-allowed:raise MutationError('Invalid SKU fields. Expiry and quantity belong to lots and receipts.')
    sku=db.session.get(SKU,integer_id(sku_id),populate_existing=True,with_for_update=True) if sku_id is not None else None
    if sku_id is not None and sku is None:raise MutationError('Resource not found.',404)
    before=sku_snapshot(sku) if sku else None
    if sku:revision(values.get('expected_version'),sku.version)
    else:revision(values.get('expected_version',0),0)
    defaults=before or dict(unit='unit',variant_attributes={},active=True)
    data={key:values.get(key,defaults.get(key)) for key in ('code','name','category_id','unit','variant_attributes','default_price','active')}
    if sku:
        if data['code'] != sku.code:raise MutationError('SKU codes are stable and cannot be changed.')
    else:data['code']=code(data['code'])
    data['name']=text(data['name'],'SKU name',100);data['unit']=text(data['unit'],'Unit',20)
    if type(data['active']) is not bool:raise MutationError('active must be a boolean.')
    data['category_id']=integer_id(data['category_id'])
    category=db.session.get(Category,data['category_id'],populate_existing=True,with_for_update=True)
    if not category:raise MutationError('Resource not found.',404)
    attrs=data['variant_attributes']
    if not isinstance(attrs,dict) or len(attrs)>20 or any(not isinstance(k,str) or not isinstance(v,str) or not 1<=len(k)<=40 or len(v)>100 for k,v in attrs.items()):
        raise MutationError('Variant attributes must be a small object of text values.')
    if not (sku is not None and sku.default_price is None and 'default_price' not in values):
        data['default_price']=price(data['default_price'])
    key=signature(data['name'],data['unit'],attrs)
    # Also compare preserved T04 definitions which predate the identity-key table.
    for other in db.session.scalars(select(SKU).where(SKU.sku_id != sku_id) if sku_id else select(SKU)):
        if other.code.upper()==data['code'].upper() or signature(other.name,other.unit,other.variant_attributes or {}) == key:
            raise MutationError('This sellable variant already has a SKU identity.',409)
    if sku is None:
        sku=SKU(version=1,**data);db.session.add(sku);db.session.flush()
    else:
        for field,value in data.items():setattr(sku,field,value)
        sku.version+=1
    identity=db.session.get(SKUIdentity,sku.sku_id)
    if identity is None:db.session.add(SKUIdentity(sku_id=sku.sku_id,signature=key))
    else:identity.signature=key
    policy=_effective_policy(sku,lock=True)
    affected=_hold_undated(scope,[sku.sku_id],values.get('reason')) if policy and policy.require_expiry else []
    _record(scope,'sku',sku.sku_id,sku.version,values.get('reason'),before,dict(sku_snapshot(sku),review_holdings=affected))
    db.session.flush()
    return sku_snapshot(sku)


def save_policy(kind, key, values, expected_revision, reason, remove=False):
    scope=resolve_scope('expiry_policy.manage',consolidated=True,lock=True)
    key=integer_id(key)
    if kind not in {'category','sku'}:raise MutationError('Invalid policy source.')
    parent=db.session.get(Category if kind=='category' else SKU,key,populate_existing=True,with_for_update=True)
    if parent is None:raise MutationError('Resource not found.',404)
    model=CategoryExpiryPolicy if kind=='category' else ProductExpiryPolicyOverride
    row=db.session.get(model,key,populate_existing=True,with_for_update=True)
    revision(expected_revision,row.revision if row else 0)
    if remove and (kind!='sku' or row is None):raise MutationError('Only an existing SKU override can be removed.')
    clean=None if remove else validate_policy(values)
    entity=kind+'_policy'
    previous=db.session.scalar(select(func.max(CatalogAudit.revision)).where(CatalogAudit.entity_type==entity,CatalogAudit.entity_key==str(key))) or 0
    next_revision=max(previous,row.revision if row else 0)+1
    before=dict(revision=row.revision,**{field:getattr(row,field) for field in POLICY_FIELDS}) if row else None
    if remove:db.session.delete(row)
    else:
        if row is None:
            row=model(**{('category_id' if kind=='category' else 'sku_id'):key});db.session.add(row)
        for field,value in clean.items():setattr(row,field,value)
        row.revision=next_revision;row.effective_at=utcnow().replace(tzinfo=None)
    db.session.flush()
    skus=db.session.scalars(select(SKU).where(SKU.category_id==key).order_by(SKU.sku_id).with_for_update()).all() if kind=='category' else [parent]
    for sku in skus:_adopt_identity(sku)
    required=[]
    for sku in skus:
        policy=_effective_policy(sku,lock=True)
        if policy and policy.require_expiry:required.append(sku.sku_id)
    affected=_hold_undated(scope,required,reason)
    after=dict(removed=remove,revision=next_revision,policy=clean,review_holdings=affected,managed_skus=[s.sku_id for s in skus])
    _record(scope,entity,key,next_revision,reason,before,after)
    db.session.flush()
    return after


def save_settings(timezone_name, expected_version, reason):
    scope=resolve_scope('expiry_policy.manage',consolidated=True,lock=True)
    zone(timezone_name)
    row=db.session.get(CompanyExpirySettings,1,populate_existing=True,with_for_update=True)
    revision(expected_version,row.version if row else 0)
    before=dict(label_timezone=row.label_timezone,version=row.version) if row else None
    if row is None:row=CompanyExpirySettings(settings_id=1,version=1);db.session.add(row)
    else:row.version+=1
    row.label_timezone=timezone_name
    after=dict(label_timezone=row.label_timezone,version=row.version)
    _record(scope,'settings',1,row.version,reason,before,after);db.session.flush()
    return after


def settings():
    resolve_scope('inventory.read')
    row=db.session.get(CompanyExpirySettings,1)
    return dict(label_timezone=row.label_timezone,version=row.version) if row else dict(label_timezone=None,version=0)


def assortment_list():
    scope=resolve_scope('inventory.read')
    return [dict(branch_id=a.branch_id,sku_id=a.sku_id,enabled=a.enabled,version=a.version,
        sale_price_override=str(a.sale_price_override) if a.sale_price_override is not None else None,
        reorder_level_override=a.reorder_level_override,
        effective_price=str(a.sale_price_override if a.sale_price_override is not None else sku.default_price))
        for a,sku in db.session.execute(select(BranchProduct,SKU).join(SKU,SKU.sku_id==BranchProduct.sku_id)
          .where(BranchProduct.branch_id.in_(scope.branches)).order_by(BranchProduct.branch_id,SKU.code))]


def save_assortment(branch_id,sku_id,values):
    scope=resolve_scope('catalog.manage',consolidated=True,lock=True)
    if not isinstance(values,dict) or set(values)-{'enabled','sale_price_override','reorder_level_override','expected_version','reason'}:raise MutationError('Invalid assortment fields.')
    branch_id,sku_id=integer_id(branch_id),integer_id(sku_id)
    sku=db.session.get(SKU,sku_id,populate_existing=True,with_for_update=True)
    branch=db.session.get(Branch,branch_id,populate_existing=True,with_for_update=True)
    if sku is None or branch is None:raise MutationError('Resource not found.',404)
    row=db.session.get(BranchProduct,(branch_id,sku_id),populate_existing=True,with_for_update=True)
    revision(values.get('expected_version'),row.version if row else 0)
    before=dict(enabled=row.enabled,sale_price_override=str(row.sale_price_override) if row.sale_price_override is not None else None,
                reorder_level_override=row.reorder_level_override,version=row.version) if row else None
    enabled=values.get('enabled',row.enabled if row else False)
    if type(enabled) is not bool:raise MutationError('enabled must be a boolean.')
    if enabled and (not branch.active or not sku.active):raise MutationError('Only active branches and SKUs may be offered.',409)
    if row is None:row=BranchProduct(branch_id=branch_id,sku_id=sku_id,version=1);db.session.add(row)
    else:row.version+=1
    row.enabled=enabled
    if 'sale_price_override' in values:row.sale_price_override=None if values['sale_price_override'] in (None,'') else price(values['sale_price_override'])
    if 'reorder_level_override' in values:
        from expiry import integer
        row.reorder_level_override=None if values['reorder_level_override'] in (None,'') else integer(values['reorder_level_override'],'Reorder level',0,2147483647)
    after=dict(enabled=row.enabled,sale_price_override=str(row.sale_price_override) if row.sale_price_override is not None else None,
               reorder_level_override=row.reorder_level_override,version=row.version)
    _record(scope,'assortment',f'{branch_id}:{sku_id}',row.version,values.get('reason'),before,after)
    db.session.flush();return dict(branch_id=branch_id,sku_id=sku_id,**after)


def correct_lot(lot_id,values):
    scope=resolve_scope('batch.correct',consolidated=True,lock=True)
    if not isinstance(values,dict) or set(values)!={'expected_version','expiry_date','expiry_timezone','reason','evidence'}:raise MutationError('Supply the correction revision, actual label facts, reason and evidence.')
    reason=text(values['reason'],'Reason',255);evidence=text(values['evidence'],'Evidence',255)
    lot=db.session.get(StockLot,integer_id(lot_id),populate_existing=True,with_for_update=True)
    if lot is None:raise MutationError('Resource not found.',404)
    revision(values['expected_version'],lot.version)
    _adopt_identity(db.session.get(SKU,lot.sku_id,populate_existing=True,with_for_update=True))
    stocks=db.session.scalars(select(BranchStock).where(BranchStock.lot_id==lot.lot_id).with_for_update()).all()
    if any(s.reserved for s in stocks):raise MutationError('Reserved lots require ledger reconciliation before correction.',409)
    before=dict(expiry_date=lot.expiry_date.isoformat() if lot.expiry_date else None,expiry_timezone=lot.expiry_timezone,
                expires_at_utc=lot.expires_at_utc.isoformat() if lot.expires_at_utc else None,validation_state=lot.validation_state,version=lot.version)
    if values['expiry_date']:
        from expiry import parse_date
        actual=parse_date(values['expiry_date']); instant=canonical_expiry(actual,values['expiry_timezone'])
        lot.expiry_date=actual;lot.expiry_timezone=values['expiry_timezone'];lot.expires_at_utc=instant.replace(tzinfo=None)
    else:
        if values['expiry_timezone']:raise MutationError('Undated lots cannot have an expiry timezone.')
        lot.expiry_date=lot.expiry_timezone=lot.expires_at_utc=None
    lot.validation_state='review';lot.version+=1
    identity=db.session.get(LotIdentity,lot.lot_id)
    if identity is not None:
        identity.signature=lot_signature(lot.sku_id,lot.provenance['supplier_lot_code'],
                                         lot.expiry_date.isoformat() if lot.expiry_date else None)
    after=dict(expiry_date=lot.expiry_date.isoformat() if lot.expiry_date else None,expiry_timezone=lot.expiry_timezone,
        expires_at_utc=lot.expires_at_utc.isoformat() if lot.expires_at_utc else None,version=lot.version,
        validation_state='review',evidence=evidence,affected_holdings=[dict(branch_id=s.branch_id,lot_id=lot.lot_id,on_hand=s.on_hand) for s in stocks],**conversion_provenance())
    _record(scope,'lot',lot.lot_id,lot.version,reason,before,after);db.session.flush();return after
