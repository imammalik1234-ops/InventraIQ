"""T13 offline selection/manifest contract. No network, Flask or database access."""
from collections import defaultdict, deque
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
PROFILES = {'small': (30, 365), 'medium': (300, 1095), 'large': (3049, 1913)}
STORES = ('CA_1', 'TX_1', 'WI_1')
BRANCHES = ('DEMO-01', 'DEMO-02', 'DEMO-03')
SEED = 'inventraiq-t13-v1'
TRAIN_END = 1857
SOURCE = 'https://www.kaggle.com/competitions/m5-forecasting-accuracy'


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def safe_local_path(value, root):
    """Only portable, root-relative private paths; resolve existing symlinks too."""
    if not isinstance(value, str) or not re.fullmatch(r'data/private/[A-Za-z0-9_./-]+', value):
        raise ValueError('Unsafe private path')
    parts = value.split('/')
    if any(p in ('', '.', '..') or p.endswith('.') for p in parts):
        raise ValueError('Unsafe path segment')
    if any(p.lower().startswith(('.env', 'kaggle')) or p.lower().endswith(('.pem', '.key', '.p12', '.pfx')) for p in parts):
        raise ValueError('Credential path is not dataset provenance')
    if any(p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL'} |
           {f'{prefix}{n}' for prefix in ('COM', 'LPT') for n in range(1, 10)} for p in parts):
        raise ValueError('Reserved Windows path')
    base = (Path(root) / 'data/private').resolve()
    resolved = (Path(root) / value).resolve()
    if not base.is_relative_to(Path(root).resolve()) or not resolved.is_relative_to(base) or resolved == base:
        raise ValueError('Path escapes private root')
    return resolved


def select_stores(store_ids):
    """Lexicographically first store per CA/TX/WI; never inspect outcomes."""
    ids = list(store_ids)
    if len(ids) != len(set(ids)) or any(not isinstance(s, str) or not re.fullmatch(r'(CA|TX|WI)_[1-9][0-9]*', s) for s in ids):
        raise ValueError('Invalid/duplicate stores')
    try:
        result = tuple(min(s for s in ids if s.startswith(state + '_')) for state in ('CA', 'TX', 'WI'))
    except ValueError:
        raise ValueError('Each source state is required') from None
    if result != STORES:
        raise ValueError('Source metadata differs from frozen v1 store selection')
    return result


def select_skus(rows, profile):
    """Training-only shared IDs, category/activity/volume strata, seeded rank."""
    if profile not in PROFILES:
        raise ValueError('Unknown profile')
    by_item = defaultdict(dict)
    fields = {'store_id', 'item_id', 'category', 'training_end_day', 'observed_days', 'nonzero_days', 'total_units'}
    for row in rows:
        if set(row) != fields or row['store_id'] not in STORES:
            raise ValueError('Invalid training metadata fields/store')
        item = row['item_id']
        if not isinstance(item, str) or not re.fullmatch(r'(FOODS|HOBBIES|HOUSEHOLD)_[1-9]_[0-9]{3}', item):
            raise ValueError('Invalid item ID')
        if row['category'] != item.rsplit('_', 2)[0] or row['store_id'] in by_item[item]:
            raise ValueError('Inconsistent category/duplicate item-store')
        for name in ('training_end_day', 'observed_days', 'nonzero_days', 'total_units'):
            if type(row[name]) is not int or row[name] < 0:
                raise ValueError('Invalid training counts')
        if row['training_end_day'] != TRAIN_END or row['observed_days'] != TRAIN_END:
            raise ValueError('Incomplete coverage or future selection data')
        if row['nonzero_days'] > TRAIN_END or row['total_units'] < row['nonzero_days'] or (row['nonzero_days'] == 0) != (row['total_units'] == 0):
            raise ValueError('Inconsistent training units')
        by_item[item][row['store_id']] = row
    groups = defaultdict(list)
    for item, stores in by_item.items():
        if set(stores) != set(STORES):
            continue
        values = list(stores.values())
        nz = sum(r['nonzero_days'] for r in values)
        units = sum(r['total_units'] for r in values)
        activity = 'zero' if not nz else ('intermittent' if 2 * nz < 3 * TRAIN_END else 'dense')
        volume = 'low' if units < 3 * TRAIN_END else 'high'
        groups[(values[0]['category'], activity, volume)].append(item)
    queues = [deque(sorted(groups[k], key=lambda item: (canonical_hash([SEED, item]), item))) for k in sorted(groups)]
    selected = []
    while queues and len(selected) < PROFILES[profile][0]:
        for queue in queues:
            if queue and len(selected) < PROFILES[profile][0]:
                selected.append(queue.popleft())
        queues = [q for q in queues if q]
    return selected


def _schema_check(value, schema, label='$'):
    """Validate the checked-in schema's deliberately small JSON Schema subset."""
    kinds = {'object': dict, 'array': list, 'string': str, 'integer': int, 'boolean': bool, 'null': type(None)}
    if 'type' in schema:
        allowed = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        if not any(type(value) is kinds[k] for k in allowed):
            raise ValueError(label + ': invalid type')
    if 'const' in schema and canonical_hash(value) != canonical_hash(schema['const']):
        raise ValueError(label + ': invalid constant')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(label + ': invalid enum')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        if set(schema.get('required', [])) - value.keys() or (schema.get('additionalProperties') is False and value.keys() - props.keys()):
            raise ValueError(label + ': invalid fields')
        for key, child in value.items():
            if key in props:
                _schema_check(child, props[key], label + '.' + key)
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', float('inf')):
            raise ValueError(label + ': invalid length')
        if schema.get('uniqueItems') and len({canonical_hash(v) for v in value}) != len(value):
            raise ValueError(label + ': duplicate items')
        for child in value:
            _schema_check(child, schema.get('items', {}), label + '[]')
    elif isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or ('pattern' in schema and not re.fullmatch(schema['pattern'], value)):
            raise ValueError(label + ': invalid string')
    elif type(value) is int and (value < schema.get('minimum', value) or value > schema.get('maximum', value)):
        raise ValueError(label + ': out of range')


def validate_manifest(manifest, *, root=ROOT):
    schema = json.loads((ROOT / 'data/manifest.schema.json').read_text(encoding='utf-8'))
    _schema_check(manifest, schema)
    m = manifest
    cap, days = PROFILES[m['profile']]
    if m['selection'] != dict(algorithm='shared-training-strata-v1', seed=SEED, training_end_day=TRAIN_END,
                              sku_cap=cap, store_algorithm='first-per-state-v1'):
        raise ValueError('Selection configuration differs from v1')
    if m['branches'] != [dict(store_id=s, branch_code=b) for s, b in zip(STORES, BRANCHES)]:
        raise ValueError('Non-bijective or changed branch mappings')
    expected = dict(first_day=1914-days, last_day=1913, train_end_day=1857,
                    validation_first=1858, validation_last=1885, test_first=1886, test_last=1913,
                    first_date=(date(2011, 1, 29)+timedelta(days=1913-days)).isoformat(),
                    last_date='2016-04-24', demo_as_of='2016-04-24')
    if m['coverage'] != expected:
        raise ValueError('Dates/splits must preserve original history')
    if len(m['selected_item_ids']) > cap:
        raise ValueError('SKU cap exceeded')
    names = [f['name'] for f in m['files']]
    if sorted(names) != ['calendar.csv', 'sales_train_validation.csv', 'sell_prices.csv']:
        raise ValueError('Required source files differ')
    for f in m['files']:
        safe_local_path(f['path'], root)
        if f['path'] != 'data/private/raw/' + f['name']:
            raise ValueError('Source path/name mismatch')
        if (f['sha256'] is None) != (f['size_bytes'] is None):
            raise ValueError('Partial source fingerprint')
        expected_columns = {
            'calendar.csv': (['date', 'wm_yr_wk', 'weekday', 'wday', 'month', 'year', 'd', 'event_name_1', 'event_type_1', 'event_name_2', 'event_type_2', 'snap_CA', 'snap_TX', 'snap_WI'], 'calendar-v1'),
            'sell_prices.csv': (['store_id', 'item_id', 'wm_yr_wk', 'sell_price'], 'weekly-price-v1'),
            'sales_train_validation.csv': (['id', 'item_id', 'dept_id', 'cat_id', 'store_id', 'state_id', 'd_1..d_1913'], 'wide-units-v1')}
        if (f['columns'], f['types']) != expected_columns[f['name']]:
            raise ValueError('Source column/type contract differs')
    for field in ('metadata_path', 'reconciliation_path'):
        safe_local_path(m[field], root)
    if m['source']['provenance'] == 'synthetic_fixture':
        if m['source']['revision'] != 'synthetic-metadata-v1' or m['source']['byte_identity'] != 'not_applicable':
            raise ValueError('Synthetic provenance mismatch')
    elif m['source']['revision'] != 'accuracy-validation-d1-d1913' or m['source']['byte_identity'] != 'unverified':
        raise ValueError('Unverified real revision must remain labeled')
    if m['status'] == 'selection_frozen':
        if not m['selected_item_ids'] or m['selection_metadata_sha256'] is None:
            raise ValueError('Frozen selection needs metadata evidence')
    elif m['selected_item_ids'] or m['selection_metadata_sha256'] is not None:
        raise ValueError('Template cannot claim a frozen selection')
    return canonical_hash(m)


def freeze_selection(template, rows, *, root=ROOT):
    """Freeze supplied metadata only; never read source files or permit ingestion."""
    validate_manifest(template, root=root)
    result = json.loads(json.dumps(template))
    result['selected_item_ids'] = select_skus(rows, result['profile'])
    result['selection_metadata_sha256'] = canonical_hash(sorted(rows, key=lambda r: (r['store_id'], r['item_id'])))
    result['status'] = 'selection_frozen'
    validate_manifest(result, root=root)
    return result


def require_real_ingestion(_manifest):
    """T13 cannot approve rights by editing a client-controlled JSON document."""
    raise ValueError('BLOCKED: verified terms/rights approval and exact source fingerprints required; T14 not implemented')
