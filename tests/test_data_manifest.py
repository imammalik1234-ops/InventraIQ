"""T13 uses original synthetic metadata only; no app, dataset or DB is opened."""
import json
from pathlib import Path
import random
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import data_manifest as dm


def template(profile='small'):
    return json.loads((dm.ROOT / 'data/manifests' / f'{profile}.v1.json').read_text(encoding='utf-8'))


def synthetic_rows(count=45):
    """Formula-generated counts; not copied or sampled from M5 observations."""
    return [dict(store_id=store, item_id=f'{category}_1_{i:03}', category=category,
                 training_end_day=1857, observed_days=1857,
                 nonzero_days=0 if i % 3 == 0 else (100 if i % 3 == 1 else 1500),
                 total_units=0 if i % 3 == 0 else (200 if i % 3 == 1 else 6000))
            for store in dm.STORES for category in ('FOODS', 'HOBBIES', 'HOUSEHOLD')
            for i in range(1, count+1)]


def fixture(profile='small'):
    m = template(profile)
    m['source'].update(provenance='synthetic_fixture', revision='synthetic-metadata-v1', byte_identity='not_applicable')
    return m


class ManifestTests(unittest.TestCase):
    def test_all_profile_templates_validate_and_are_explicitly_blocked(self):
        for profile, (cap, days) in dm.PROFILES.items():
            m = template(profile)
            self.assertEqual(len(dm.validate_manifest(m)), 64)
            self.assertEqual(m['selection']['sku_cap'], cap)
            self.assertEqual(m['coverage']['last_day']-m['coverage']['first_day']+1, days)
            self.assertTrue(all(f['sha256'] is None and f['size_bytes'] is None for f in m['files']))
            self.assertEqual(m['rights']['allowed_real_uses'], [])

    def test_store_selection_order_independent_and_three_bijective_branches(self):
        stores = ['WI_3','CA_4','TX_2','WI_1','TX_1','CA_1','CA_2']
        self.assertEqual(dm.select_stores(stores), dm.STORES)
        self.assertEqual(dm.select_stores(reversed(stores)), dm.STORES)
        self.assertEqual([r['branch_code'] for r in template()['branches']], list(dm.BRANCHES))
        for bad in [stores+['CA_1'], ['CA_2','TX_1','WI_1'], ['CA_1','TX_1'], ['CA_1','TX_1','WI_1','HQ']]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):dm.select_stores(bad)

    def test_sku_selection_and_manifest_determinism(self):
        rows = synthetic_rows()
        expected = dm.freeze_selection(fixture(), rows)
        random.Random(42).shuffle(rows)
        actual = dm.freeze_selection(fixture(), rows)
        self.assertEqual(actual, expected)
        self.assertEqual(dm.validate_manifest(actual), dm.canonical_hash(expected))
        self.assertEqual(len(actual['selected_item_ids']), 30)
        self.assertEqual(actual['source']['provenance'], 'synthetic_fixture')
        self.assertEqual(fixture()['selected_item_ids'], [])

    def test_profile_selection_is_nested_and_caps_are_real(self):
        rows = synthetic_rows(120)
        small, medium, large = [dm.select_skus(rows, p) for p in dm.PROFILES]
        self.assertEqual(len(small), 30);self.assertEqual(len(medium), 300);self.assertEqual(len(large), 360)
        self.assertEqual(small, medium[:30]);self.assertEqual(medium, large[:300])

    def test_training_strata_include_categories_and_zero_demand(self):
        selected = dm.select_skus(synthetic_rows(), 'small')
        self.assertEqual({s.split('_')[0] for s in selected}, {'FOODS','HOBBIES','HOUSEHOLD'})
        self.assertTrue(any(int(s[-3:]) % 3 == 0 for s in selected))

    def test_missing_store_candidates_excluded_without_replication(self):
        rows = synthetic_rows(1)
        rows = [r for r in rows if not (r['category']=='FOODS' and r['store_id']=='WI_1')]
        self.assertEqual(len(dm.select_skus(rows, 'large')), 2)
        self.assertFalse(any(i.startswith('FOODS') for i in dm.select_skus(rows, 'large')))

    def test_future_fields_counts_duplicates_and_incomplete_metadata_rejected(self):
        changes = [dict(training_end_day=1886), dict(observed_days=1856),dict(nonzero_days=True),
                   dict(total_units=-1),dict(test_total_units=99),dict(nonzero_days=1858),
                   dict(nonzero_days=0,total_units=1),dict(category='OTHER'),dict(store_id='CA_2')]
        for change in changes:
            rows = synthetic_rows(1);rows[0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):dm.select_skus(rows,'small')
        rows = synthetic_rows(1)
        with self.assertRaises(ValueError):dm.select_skus(rows+[rows[0]],'small')

    def test_schema_closed_fields_types_versions_and_identity_constraints(self):
        changes = [('schema_version','v2'),('profile','huge'),('rights',{}),('unknown',True),
                   ('selected_item_ids',['FOODS_1_001','FOODS_1_001']),('selected_item_ids',['<script>'])]
        for key,value in changes:
            m=template();m[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):dm.validate_manifest(m)
        m=template();m['selection']['sku_cap']=True
        with self.assertRaises(ValueError):dm.validate_manifest(m)
        m=template();m['transform']['date_shift_days']=False
        with self.assertRaises(ValueError):dm.validate_manifest(m)

    def test_mapping_and_date_shift_injection_rejected(self):
        for section,key,value in [('coverage','last_date','2026-10-22'),('coverage','train_end_day',1913),
                                  ('transform','pos_sales',True),('transform','source_currency','MYR'),
                                  ('selection','seed','new-seed')]:
            m=template();m[section][key]=value
            with self.subTest(section=section,key=key),self.assertRaises(ValueError):dm.validate_manifest(m)
        m=template();m['branches'][1]['branch_code']='DEMO-01'
        with self.assertRaises(ValueError):dm.validate_manifest(m)

    def test_source_fingerprints_names_columns_and_provenance(self):
        for field,value in [('sha256','not-a-hash'),('size_bytes',0),('name','other.csv'),
                            ('columns',['wrong']),('path','data/private/raw/kaggle.json'),('types','calendar-v1')]:
            m=template();m['files'][1][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):dm.validate_manifest(m)
        m=template();m['files'][0]['sha256']='a'*64
        with self.assertRaises(ValueError):dm.validate_manifest(m)
        m=fixture();m['source']['revision']='accuracy-validation-d1-d1913'
        with self.assertRaises(ValueError):dm.validate_manifest(m)
        m=template();m['source']['provenance']='operational_sale'
        with self.assertRaises(ValueError):dm.validate_manifest(m)

    def test_frozen_selection_requires_ids_and_metadata_evidence(self):
        m=template();m['status']='selection_frozen'
        with self.assertRaises(ValueError):dm.validate_manifest(m)
        m=dm.freeze_selection(fixture(),synthetic_rows());m['selection_metadata_sha256']=None
        with self.assertRaises(ValueError):dm.validate_manifest(m)
        with self.assertRaises(ValueError):dm.freeze_selection(fixture(),[])

    def test_paths_reject_traversal_credentials_drives_devices_and_urls(self):
        bad=['../raw.csv','/tmp/raw.csv','C:/raw.csv','data/private/../raw.csv',
             'data/private/raw/../../escape','data/private/raw\\file','data/private/raw/a:stream',
             'https://example.com/raw','data/private//raw','data/private/raw/CON.csv',
             'data/private/raw/file.','data/private/raw/.env','data/private/raw/a\x00b']
        with TemporaryDirectory() as root:
            self.assertEqual(dm.safe_local_path('data/private/raw/calendar.csv',root),Path(root)/'data/private/raw/calendar.csv')
            for path in bad:
                with self.subTest(path=path),self.assertRaises(ValueError):dm.safe_local_path(path,root)

    def test_symlink_resolution_cannot_escape_even_at_private_root(self):
        # Mock resolution, portable on Windows without symlink creation privileges.
        with TemporaryDirectory() as root, TemporaryDirectory() as outside:
            original=Path.resolve
            private=Path(root)/'data/private'
            def resolve(p,*a,**kw):
                if p==private:return Path(outside)
                return original(p,*a,**kw)
            with patch.object(Path,'resolve',resolve),self.assertRaises(ValueError):
                dm.safe_local_path('data/private/raw/file.csv',root)

    def test_rights_gate_cannot_be_unlocked_by_manifest_edits_or_hashes(self):
        for m in [template(),dm.freeze_selection(fixture(),synthetic_rows()),{'rights':{'decision':'APPROVED'}}]:
            with self.assertRaisesRegex(ValueError,'BLOCKED'):dm.require_real_ingestion(m)
        m=template();m['rights']['decision']='APPROVED'
        with self.assertRaises(ValueError):dm.validate_manifest(m)

    def test_gitignore_protects_private_and_restricted_artifacts(self):
        paths=['data/private/raw/x.csv','data/private/rights/terms.html','data/private/manifests/private.json',
               'data/derived/result.json','data/artifacts/chart.png','data/raw/source.json',
               'data/downloads/source.zip','kaggle.json','kaggle-token.txt','model.joblib']
        result=subprocess.run(['git','check-ignore','--stdin','-z'],input=('\x00'.join(paths)+'\x00').encode(),
                              capture_output=True,cwd=dm.ROOT,check=True)
        self.assertEqual(set(result.stdout.decode().rstrip('\x00').split('\x00')),set(paths))
        for p in ['data/README.md','data/manifest.schema.json','data/manifests/small.v1.json']:
            self.assertEqual(subprocess.run(['git','check-ignore',p],cwd=dm.ROOT,capture_output=True).returncode,1)

    def test_published_evidence_does_not_claim_local_file_verification(self):
        evidence=json.loads((dm.ROOT/'data/source_evidence.v1.json').read_text(encoding='utf-8'))
        self.assertEqual(evidence['decision'],'BLOCKED')
        ids=[r['record_id'] for r in evidence['records']]
        self.assertEqual(len(ids),len(set(ids)))
        for record in evidence['records']:
            self.assertEqual(record['uploader_authority'],'UNVERIFIED')
            self.assertEqual(record['license_field_present'],record['recorded_license_id'] is not None)
            self.assertEqual(record['metadata_url'],'https://zenodo.org/api/records/'+record['record_id'])
            names=[f['name'] for f in record['files']]
            self.assertEqual(len(names),len(set(names)))
            for file in record['files']:
                self.assertEqual(file['published_checksum']['algorithm'],'md5')
                self.assertRegex(file['published_checksum']['value'],r'^[a-f0-9]{32}$')
                self.assertGreater(file['published_size_bytes'],0)
                for field in ('locally_verified_md5','locally_verified_sha256','locally_verified_size_bytes'):
                    self.assertIsNone(file[field])

    def test_mirror_evidence_cannot_be_used_as_an_approved_source_manifest(self):
        evidence=json.loads((dm.ROOT/'data/source_evidence.v1.json').read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):dm.validate_manifest(evidence)
        for record in evidence['records']:
            with self.assertRaisesRegex(ValueError,'BLOCKED'):dm.require_real_ingestion(record)
        for profile in dm.PROFILES:
            m=template(profile)
            for file in m['files']:
                self.assertIsNone(file['sha256']);self.assertIsNone(file['size_bytes'])
            self.assertEqual(m['rights']['decision'],'BLOCKED')
            dm.validate_manifest(m)

    def test_schema_keyword_subset_is_exhaustive(self):
        schema=json.loads((dm.ROOT/'data/manifest.schema.json').read_text())
        allowed={'$schema','$id','title','type','const','enum','properties','required','additionalProperties',
                 'items','minItems','maxItems','uniqueItems','minLength','pattern','minimum','maximum'}
        def visit(node):
            self.assertFalse(set(node)-allowed)
            for child in node.get('properties',{}).values():visit(child)
            if 'items' in node:visit(node['items'])
        visit(schema)
