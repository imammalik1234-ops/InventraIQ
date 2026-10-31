"""Original formula fixtures. This module cannot read or convert external data."""
from datetime import date, timedelta
import hashlib
import json

SOURCE = 'inventraiq-generated-history-v1'
VERSION = 'inventraiq-synthetic-import-v1'
PROFILES = {'small': (6, 14), 'medium': (120, 90)}
FAULTS = {'duplicate_fact','invalid_date','unknown_branch','unknown_sku','negative_units',
          'bad_calendar','bad_price_join','duplicate_price','malformed_json'}
STORES = ('SYN-STORE-01','SYN-STORE-02','SYN-STORE-03')
BRANCHES = ('DEMO-01','DEMO-02','DEMO-03')
FILES = ('calendar.jsonl','prices.jsonl','observations.jsonl')


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')


def items(profile):
    return [f'SYN-ITEM-{i:04}' for i in range(PROFILES[profile][0])]


def day(n):
    return (date(2001,1,1)+timedelta(days=n)).isoformat()


def records(config, name):
    """Bounded generator; intentional fault variants remain generated fixtures."""
    profile=config['profile']; _, days=PROFILES[profile]; faults=set(config['faults'])
    if name=='calendar.jsonl':
        for d in range(days):
            yield dict(day_id=f'syn_d_{d}',date=day(d+1 if d==1 and 'bad_calendar' in faults else d),week=d//7)
    elif name=='prices.jsonl':
        if not config['prices']:return
        for s,store in enumerate(STORES):
            for i,item in enumerate(items(profile)):
                for week in range((days+6)//7):
                    if (s+i+week)%11==0:continue  # genuinely absent price, no fill
                    row=dict(store_id=store,item_id=item,week=week,price_minor=100+i+week,currency='USD')
                    yield row
                    if s==0 and i==1 and week==0 and 'duplicate_price' in faults:yield row
    elif name=='observations.jsonl':
        index=0; first=None
        for s,store in enumerate(STORES):
            for i,item in enumerate(items(profile)):
                for d in range(days):
                    value=(s+i+d+config['seed'])%7
                    missing=(s+i+d)%17==0
                    row=dict(store_id=store,item_id=item,day_id=f'syn_d_{d}',date=day(d),
                             coverage='missing' if missing else 'observed',units=None if missing else value,
                             price_week=d//7,provenance='synthetic_fixture')
                    if first is None:first=dict(row)
                    if index==1 and 'invalid_date' in faults:row['date']='2000-12-31'
                    if index==2 and 'unknown_branch' in faults:row['store_id']='SYN-UNKNOWN'
                    if index==3 and 'unknown_sku' in faults:row['item_id']='SYN-UNKNOWN'
                    if index==4 and 'negative_units' in faults:row['units']=-1
                    if index==5 and 'bad_price_join' in faults:row['price_week']=999
                    if index==6 and 'malformed_json' in faults:yield b'{broken-json}\n'
                    else:yield row
                    index+=1
        if 'duplicate_fact' in faults:yield first
    else:raise ValueError('Unexpected fixture format')


def lines(config,name):
    for row in records(config,name):
        yield row if isinstance(row,bytes) else encoded(row)+b'\n'


def fingerprint(config,name):
    digest=hashlib.sha256();size=0;count=0
    for line in lines(config,name):
        digest.update(line);size+=len(line);count+=1
    return dict(sha256=digest.hexdigest(),size_bytes=size,rows=count)


def specification(profile='small',revision='synthetic-r1',seed=13,prices=True,faults=()):
    import re
    if profile not in PROFILES or type(seed) is not int or not 0<=seed<=10000 or type(prices) is not bool:
        raise ValueError('Invalid synthetic generator configuration')
    if not isinstance(revision,str) or not re.fullmatch(r'synthetic-[a-z0-9-]{1,48}',revision):
        raise ValueError('Synthetic revision required')
    if not isinstance(faults,(list,tuple)) or any(f not in FAULTS for f in faults) or len(set(faults))!=len(faults):
        raise ValueError('Unknown synthetic fault configuration')
    config=dict(profile=profile,seed=seed,prices=prices,faults=sorted(faults))
    return dict(schema_version=VERSION,source_id=SOURCE,source_revision=revision,
        provenance='synthetic_fixture',generator='formula-v1',configuration=config,
        branches=[dict(store_id=s,branch_code=b) for s,b in zip(STORES,BRANCHES)],
        skus=[dict(item_id=i,sku_code=i.replace('ITEM','SKU')) for i in items(profile)],
        coverage=dict(first_date=day(0),last_date=day(PROFILES[profile][1]-1)),
        files={name:fingerprint(config,name) for name in FILES})


def validate_spec(manifest):
    """Source gate runs before opening input files. No M5-shaped source accepted."""
    if not isinstance(manifest,dict) or manifest.get('source_id')!=SOURCE or manifest.get('provenance')!='synthetic_fixture':
        raise ValueError('Real/unknown sources BLOCKED; generated synthetic fixtures only')
    try:
        config=manifest['configuration']
        expected=specification(revision=manifest['source_revision'],**config)
    except (KeyError,TypeError) as exc:
        raise ValueError('Invalid synthetic manifest') from exc
    if encoded(expected)!=encoded(manifest):
        raise ValueError('Manifest differs from deterministic generator contract')
    return hashlib.sha256(encoded(manifest)).hexdigest()
