"""Pure advisory projections. Never changes a balance, reservation or document."""
from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
from expiry import zone
from security import MutationError


def number(value):
    if isinstance(value,bool) or value is None:raise MutationError('Unknown quantity cannot be zero-filled.')
    try:result=Decimal(str(value))
    except Exception:raise MutationError('Invalid calculation quantity.') from None
    if not result.is_finite() or result<0:raise MutationError('Nonnegative finite quantity required.')
    return result


def ceiling(value):return int(value.to_integral_value(rounding=ROUND_CEILING))
def floor(value):return int(value.to_integral_value(rounding=ROUND_FLOOR))
def utc(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc) if value else None


def day_end(day,tz):
    return datetime.combine(day+timedelta(days=1),datetime.min.time(),zone(tz)).astimezone(timezone.utc)-timedelta(seconds=1)


def order(lot):
    # Real lot IDs retain T07's numeric tie-break; expected-inbound keys are namespaced.
    identity=(0,lot['key']) if type(lot['key']) is int else (1,str(lot['key']))
    return (lot['expires_at'] is None,lot['expires_at'] or '',lot.get('first_received') is None,
        lot.get('first_received') or '',identity)


def project(lots,demand,origin,tz,inbound=()):
    """Fractional forecast consumption is hypothetical. Receipts become usable on their explicit ready day."""
    if not 1<=len(demand)<=7:raise MutationError('A bounded daily forecast is required.')
    origin=date.fromisoformat(origin);pool=[];arrivals=deepcopy(list(inbound));daily=[];unmet=Decimal(0);expired=Decimal(0)
    for lot in deepcopy(lots):
        pool.append({**lot,'remaining':number(lot['available'])})
    for index,raw in enumerate(demand,1):
        day=origin+timedelta(days=index);end=day_end(day,tz);loss=Decimal(0)
        for item in arrivals:
            if item['ready_date']==day.isoformat():
                pool.append({**item,'remaining':number(item['available'])})
        for lot in pool:
            if lot['expires_at'] and utc(lot['expires_at'])<=end:
                loss+=lot['remaining'];lot['remaining']=Decimal(0)
        need=number(raw)
        for lot in sorted(pool,key=order):
            take=min(need,lot['remaining']);need-=take;lot['remaining']-=take
        unmet+=need;expired+=loss
        daily.append(dict(date=day.isoformat(),demand=str(number(raw)),unmet=str(need),
            projected_expiry=str(loss),ending_available=str(sum((l['remaining'] for l in pool),Decimal(0)))))
    return dict(days=daily,unmet=unmet,expiry=expired,ending=sum((l['remaining'] for l in pool),Decimal(0)),lots=pool)


def calculate(lots,demand,origin,tz,policy,inbound=()):
    horizon=policy['lead_days']+policy['review_days']
    if len(demand)!=7:raise MutationError('Complete seven-day predictions required.')
    with_inbound=project(lots,demand[:horizon],origin,tz,inbound)
    physical=project(lots,demand[:horizon],origin,tz)
    raw=ceiling(with_inbound['unmet']+max(Decimal(0),number(policy['safety_units'])-with_inbound['ending']))
    pack=policy['pack_units'];purchase=((max(raw,policy['minimum_order_units'])+pack-1)//pack)*pack if raw else 0
    if purchase>2147483647:raise MutationError('Recommendation exceeds supported whole-unit quantity bounds.')
    excess=floor(max(Decimal(0),physical['ending']-number(policy['safety_units'])-number(policy['excess_buffer_units'])))
    return dict(raw_need=raw,purchase_quantity=purchase,potential_excess=excess,
        projected_expiry=str(physical['expiry']),ending_available=str(with_inbound['ending']),
        unmet_demand=str(with_inbound['unmet']),horizon_days=horizon,
        demand=str(sum((number(v) for v in demand[:horizon]),Decimal(0))),
        timeline=with_inbound['days'],donor_remaining=[dict(l,remaining=str(l['remaining'])) for l in physical['lots']],
        before_lead_time_shortage=any(number(d['unmet'])>0 for d in with_inbound['days'][:policy['lead_days']]),
        formula='raw=ceil(unmet+max(0,safety-ending)); purchase=pack*ceil(max(raw,minimum)/pack) when raw>0; excess=floor(max(0,physical_ending-safety-excess_buffer))')
