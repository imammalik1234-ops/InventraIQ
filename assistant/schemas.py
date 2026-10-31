"""Small JSON Schema vocabulary, used for runtime checks and published contracts."""
from dataclasses import dataclass
from datetime import date
import math


class Invalid(ValueError):
    pass


def obj(properties, required=()):
    return dict(type='object',properties=properties,required=list(required),additionalProperties=False)


def integer(maximum=2147483647, minimum=1):
    return dict(type='integer',minimum=minimum,maximum=maximum)


def string(maximum=255, choices=None):
    result=dict(type='string',maxLength=maximum)
    if choices is not None:result['enum']=list(choices)
    return result


def nullable(schema):return dict(schema,type=[schema['type'],'null'])
def array(schema, maximum=25):return dict(type='array',items=schema,maxItems=maximum)


def validate(value, schema):
    types=schema['type'];types=[types] if isinstance(types,str) else types
    kind=('null' if value is None else 'boolean' if type(value) is bool else
          'integer' if type(value) is int else 'number' if type(value) is float else
          'string' if type(value) is str else 'array' if type(value) is list else
          'object' if type(value) is dict else 'unsupported')
    if kind not in types and not (kind=='integer' and 'number' in types):raise Invalid()
    if value is None:return
    if 'enum' in schema and value not in schema['enum']:raise Invalid()
    if kind in ('integer','number'):
        if (kind=='number' and not math.isfinite(value)) or value<schema.get('minimum',-math.inf) or value>schema.get('maximum',math.inf):raise Invalid()
    elif kind=='string':
        if len(value)>schema.get('maxLength',1024) or any(ord(c)<32 for c in value):raise Invalid()
        if schema.get('format')=='date':
            try:
                if date.fromisoformat(value).isoformat()!=value:raise Invalid()
            except ValueError:raise Invalid() from None
    elif kind=='array':
        if not schema.get('minItems',0)<=len(value)<=schema['maxItems']:raise Invalid()
        for item in value:validate(item,schema['items'])
    elif kind=='object':
        if len(value)>64 or any(type(k) is not str for k in value):raise Invalid()
        props=schema['properties']
        if set(value)-set(props) or set(schema['required'])-set(value):raise Invalid()
        for key,item in value.items():validate(item,props[key])


DATE=dict(type='string',maxLength=10,format='date')
BASE=dict(branch_id=integer(),page=integer(1000),page_size=integer(25))
WINDOW=dict(BASE,start=DATE,end=DATE)
INVENTORY=dict(WINDOW,search=string(80),status=string(20,('all','healthy','warning','expired','review','not_tracked','low_stock')),
               sort=string(4,('asc','desc')),snapshot=string(4096))
NUMBER=dict(type='number',minimum=0,maximum=1e15)
COUNT=integer(10**15,0)
MONEY=string(32)
METRICS={k:COUNT for k in ('on_hand','available','reserved','quarantined','unusable','known_sold_units','unknown_revenue_units','refunded_units')}
METRICS.update({k:MONEY for k in ('recorded_gross','recorded_refunds','recorded_net')})
BRANCH=obj(dict(branch_id=integer(),code=string(40),**METRICS),('branch_id',))
LOT=obj(dict(branch_id=integer(),sku_id=integer(),sku_code=string(80),name=string(100),lot_id=nullable(integer()),
             lot_code=string(100),source=string(40),on_hand=COUNT,available=COUNT,reserved=COUNT,quarantined=COUNT,
             unusable=COUNT,status=string(40),days_remaining=nullable(dict(type='integer',minimum=-1000000,maximum=1000000)),
             expiry_date=nullable(DATE),expiry_timezone=nullable(string(64)),branch_timezone=string(64),
             availability_verified=dict(type='boolean'),hold_reason=string(255),stock_revision=COUNT,
             threshold=nullable(COUNT)),('branch_id','sku_id','on_hand','available','status'))
SALE=obj(dict(sale_id=integer(),branch_id=integer(),date=DATE,total=MONEY),('sale_id','branch_id','date','total'))
HISTORY=obj(dict(branch_id=integer(),sku_id=integer(),observation_id=integer(),units=nullable(COUNT),cells=COUNT,
                 missing=COUNT,observed_zero=COUNT),('branch_id','sku_id','units','cells','missing'))
FORECAST=obj(dict(branch_id=integer(),sku_id=integer(),sku_code=string(80),snapshot_id=integer(),run_id=integer(),
                 model_id=string(100),model_version=string(100),
                 as_of=DATE,generated_at=string(80),qualification=string(80),freshness=string(40),eligibility=string(80),
                 fallback_reason=nullable(string(255)),predictions=nullable(array(NUMBER,7)),
                 horizon_days=integer(7,7),
                 predicted_demand=nullable(NUMBER),status=string(40),reason=nullable(string(512)),
                 history_days=nullable(COUNT),provenance=string(80)),('branch_id','sku_id','predictions','qualification','freshness','status'))
FORECAST['properties']['predictions']['minItems']=7
RECOMMENDATION=obj(dict(branch_id=integer(),branch_code=string(40),sku_id=integer(),sku_code=string(80),
                       business_date=DATE,timezone=string(64),decision_id=string(64),reason_codes=array(string(100),40),
                       assumptions=array(string(512),10),advisory_only=dict(type='boolean'),
                       production_numeric_enabled=dict(type='boolean'),
                       forecast=obj(dict(run_id=nullable(integer()),snapshot_id=nullable(integer()),qualification=string(80),
                           freshness=string(40),as_of=nullable(DATE),model_id=nullable(string(100)),model_version=nullable(string(100)),
                           fallback_reason=nullable(string(512)))),
                       recommendations=array(obj(dict(type=string(40),state=string(80),suggested_quantity=nullable(COUNT),
                           observed_expired_units=nullable(COUNT),projected_unused_expiry=nullable(string(40))),('type','state','suggested_quantity')),3)),
                   ('branch_id','sku_id','recommendations','reason_codes','advisory_only','production_numeric_enabled'))


@dataclass(frozen=True)
class Tool:
    name: str
    capability: str
    description: str
    inputs: dict
    row: dict


READ_TOOLS=(
 Tool('inventory_summary','inventory.read','Current scoped lot page; summaries cover the full authorized scope.',obj(INVENTORY),LOT),
 Tool('sales_summary','sales.read','Recorded MYR sale headers, refunds and unknown-revenue quantities; no inferred prices.',obj(dict(WINDOW,cursor=string(4096))),SALE),
 Tool('compare_branches','reports.read','Authorized branch facts from the existing report, not unsupported analytics.',obj(dict(branch_id=integer(),start=DATE,end=DATE)),BRANCH),
 Tool('inventory_risks','alerts.read','Existing expiry/stock classifications, no stock mutation or invented dates.',obj(INVENTORY),LOT),
 Tool('product_performance','reports.read','Synthetic historical observed units and missing coverage by SKU; not POS revenue or latent demand.',obj(dict(WINDOW,cursor=string(4096)),('start','end')),HISTORY),
 Tool('forecast_evidence','predictions.read','Existing immutable snapshots only; missing/stale/unqualified values remain withheld.',
      obj(dict(BASE,sku_id=integer(),run_id=integer(),as_of=DATE)),FORECAST),
 Tool('recommendation_preview','predictions.read','Existing T21 provisional synthetic advisory; never an executable action.',
      obj(dict(BASE,sku_id=integer(),page_size=integer(5))),RECOMMENDATION),
)
ARTIFACT_INPUT=obj(dict(branch_id=integer(),start=DATE,end=DATE,page=integer(1000),page_size=integer(25),
    source=string(40,('inventory_summary','sales_summary','compare_branches','inventory_risks')),
    format=string(8,('csv','chart')),metric=string(40,('on_hand','available','recorded_net'))),('source','format'))
ARTIFACT_ROW=obj(dict(artifact_id=string(32),media_type=string(80),expires_at=string(80),bytes=COUNT,
                      sha256=string(64),download_path=string(120)),('artifact_id','media_type','expires_at','bytes','sha256','download_path'))
TOOLS=READ_TOOLS+(Tool('report_artifact','export.read','Owned bounded expiring CSV or fixed bar-chart JSON; no HTML, code or model-supplied labels.',ARTIFACT_INPUT,ARTIFACT_ROW),)


def output_schema(tool):
    return obj(dict(schema_version=string(40),tool=nullable(string(40)),status=string(24,('ok','unavailable','error')),
        scope=nullable(obj(dict(branch_ids=array(integer(),50),role=string(24),consolidated=dict(type='boolean')),('branch_ids','role','consolidated'))),
        as_of=nullable(string(80)),filters=obj(tool.inputs['properties']),
        units=obj(dict(quantity=string(40),money=string(40),forecast=string(60)),('quantity','money','forecast')),
        provenance=array(string(512),16),
        data=nullable(obj(dict(rows=array(tool.row,150),summary=obj(METRICS),state=string(80),reason=nullable(string(512))),('rows','summary','state','reason'))),
        completeness=obj(dict(returned_rows=COUNT,total_rows=nullable(COUNT),truncated=dict(type='boolean'),
            page=integer(1000),page_size=integer(150),continuation=nullable(string(4096))),
            ('returned_rows','total_rows','truncated','page','page_size','continuation')),
        error=nullable(obj(dict(code=string(40),message=string(150)),('code','message')))),
        ('schema_version','tool','status','scope','as_of','filters','units','provenance','data','completeness','error'))
