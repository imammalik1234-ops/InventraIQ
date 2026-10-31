"""Chronological validation -> frozen selection -> one locked-test evaluation.

No DB access, model publication, operational facts, or random train/test splits.
"""
from collections import Counter,defaultdict
from dataclasses import asdict
from datetime import date,timedelta
import hashlib
import json
import math
from forecasting.data import prepare_history
from forecasting.candidates import CANDIDATES,predict

VERSION='inventraiq-t19-evaluation-v1'
MIN_TRAIN=56
HORIZON=7


def plan(days):
    if type(days) is not int or not 1<=days<=1096:raise ValueError('Invalid bounded history')
    full=days>=MIN_TRAIN+112
    if full:validation_start=days-112;validation_end=days-56;test_start=days-56
    else:
        # Four complete weeks where possible; never pretend these are 56+56.
        weeks=min(8,max(0,(days-MIN_TRAIN)//7))
        validation_start=days-weeks*7;validation_end=days;test_start=None
    origins=list(range(validation_start-1,validation_end-1,7)) if validation_end>validation_start else []
    return dict(protocol='full_56_validation_56_locked_test' if full else 'limited_validation_no_locked_test',
        validation_origins=origins,test_origins=list(range(test_start-1,days-1,7)) if full else [],
        validation_start=validation_start,validation_end_exclusive=validation_end,
        locked_test_start=test_start,horizon=7,minimum_training_days=MIN_TRAIN)


def fold(history,origin):
    contract=dict(history['contract']);start=date.fromisoformat(contract['start'])
    cutoff=(start+timedelta(days=origin)).isoformat();end=(start+timedelta(days=origin+7)).isoformat()
    contract.update(as_of=end,training_end=cutoff,forecast_origin=cutoff,evaluation_cutoff=end)
    rows=[dict(branch_id=contract['branch_id'],sku_id=contract['sku_id'],source_revision=contract['source_revision'],
        provenance=contract['provenance'],business_date=r['date'],units=r['units'],
        coverage='observed' if r['observed'] else 'missing') for r in history['days'] if r['record_present']]
    exposure=contract.get('exposure')
    if not exposure or exposure['start'] is None:exposure=None
    return prepare_history(contract,rows,exposure=exposure)


def scales(values):
    pairs=[(values[i],values[i-7]) for i in range(7,len(values)) if values[i] is not None and values[i-7] is not None]
    if not pairs:return None,None
    return sum(abs(a-b) for a,b in pairs)/len(pairs),sum((a-b)**2 for a,b in pairs)/len(pairs)


def score(actual,predicted,training):
    if len(actual)!=7 or (predicted is not None and (len(predicted)!=7 or any(not math.isfinite(p) or p<0 for p in predicted))):
        raise ValueError('Invalid seven-day predictions')
    pairs=[(y,p) for y,p in zip(actual,predicted or []) if y is not None]
    if not pairs:return dict(n=0,absolute_error=0,squared_error=0,actual_units=0,bias_units=0,
        complete_horizon=False,cumulative_error=None,mase=None,rmsse=None)
    errors=[p-y for y,p in pairs];n=len(pairs);denom1,denom2=scales(training)
    absolute=sum(abs(v) for v in errors);square=sum(v*v for v in errors)
    return dict(n=n,absolute_error=absolute,squared_error=square,actual_units=sum(y for y,p in pairs),
        bias_units=sum(errors),complete_horizon=n==7,cumulative_error=sum(errors) if n==7 else None,
        mase=absolute/n/denom1 if denom1 else None,rmsse=math.sqrt(square/n/denom2) if denom2 else None)


def aggregate(records):
    n=sum(r['score']['n'] for r in records);actual=sum(r['score']['actual_units'] for r in records)
    absolute=sum(r['score']['absolute_error'] for r in records);bias=sum(r['score']['bias_units'] for r in records)
    complete=[r['score']['cumulative_error'] for r in records if r['score']['complete_horizon']]
    mase=[r['score']['mase'] for r in records if r['score']['mase'] is not None]
    rmsse=[r['score']['rmsse'] for r in records if r['score']['rmsse'] is not None]
    return dict(forecasts=len(records),scored_days=n,eligible_series=len({r['identity'] for r in records if r['score']['n']}),
        mae=absolute/n if n else None,wape_percent=100*absolute/actual if actual else None,
        signed_bias_units=bias/n if n else None,signed_bias_percent=100*bias/actual if actual else None,
        absolute_7day_total_error=sum(abs(v) for v in complete) if complete else None,
        mean_absolute_7day_total_error=sum(abs(v) for v in complete)/len(complete) if complete else None,
        signed_7day_total_error=sum(complete)/len(complete) if complete else None,
        complete_horizons=len(complete),partial_horizons=sum(0<r['score']['n']<7 for r in records),
        mase=sum(mase)/len(mase) if mase else None,rmsse=sum(rmsse)/len(rmsse) if rmsse else None,
        scaled_metric_defined_forecasts=len(mase),native_forecasts=sum(r['native'] for r in records),
        fallbacks=dict(Counter(r['fallback_reason'] for r in records if r['fallback_reason'])),
        errors=dict(Counter(r['error_type'] for r in records if r['error_type'])),
        fit_ms=sum(r['fit_ms'] for r in records),inference_ms=sum(r['inference_ms'] for r in records))


def breakdown(records):
    result={}
    for key in ('branch','category','cohort'):
        groups=defaultdict(list)
        for row in records:groups[str(row[key])].append(row)
        result[key]={group:aggregate(rows) for group,rows in sorted(groups.items())}
    return result


def select_champion(results):
    valid=[c for c in CANDIDATES if results[c.name]['overall']['absolute_7day_total_error'] is not None
        and not results[c.name]['overall']['errors']]
    baselines=[c for c in valid if c.family=='baseline']
    if not baselines:return dict(champion=None,reference=None,promoted=False,reason='No scored complete horizons.',checks={})
    rank=lambda c:(results[c.name]['overall']['absolute_7day_total_error'],results[c.name]['overall']['mae'],CANDIDATES.index(c))
    reference=min(baselines,key=rank);base=results[reference.name];checks={};qualified=[]
    for c in valid:
        if c.family!='ml':continue
        m=results[c.name];denominator=base['overall']['absolute_7day_total_error']
        improvement=1-m['overall']['absolute_7day_total_error']/denominator if denominator else None
        branch_ok=True
        for branch,b in base['groups']['branch'].items():
            a=m['groups']['branch'][branch]['absolute_7day_total_error'];old=b['absolute_7day_total_error']
            if old is None or a is None or a>old*1.10+1e-9:branch_ok=False
        coverage_ok=m['overall']['scored_days']==base['overall']['scored_days'] and m['overall']['native_forecasts']>0
        passed=improvement is not None and improvement>=.05 and branch_ok and coverage_ok
        checks[c.name]=dict(improvement_fraction=improvement,branch_guard=branch_ok,
            coverage_guard=coverage_ok,passes=passed)
        if passed:qualified.append(c)
    chosen=min(qualified,key=rank) if qualified else reference
    return dict(champion=chosen.name,reference=reference.name,promoted=bool(qualified),checks=checks,
        reason='Validation-only 5%/10% gates; zero reference error cannot show a relative improvement.')


def evaluate(histories):
    if not histories or len(histories)>36:raise ValueError('Use 1..36 authorized series')
    identities=[(h['contract']['branch_id'],h['contract']['sku_id']) for h in histories]
    if len(set(identities))!=len(identities):raise ValueError('Duplicate branch/SKU series')
    n=len(histories[0]['days']);protocol=plan(n)
    if any(len(h['days'])!=n or h['contract']['start']!=histories[0]['contract']['start'] for h in histories):
        raise ValueError('Series must share a declared calendar frame')
    records={c.name:[] for c in CANDIDATES};folds=[]
    def run(history,origin,candidates):
        data=fold(history,origin);values=[r['units'] for r in data['training']]
        actual=[r['units'] for r in data['evaluation']];positive=sum(v>0 for v in values if v is not None)
        observed=sum(v is not None for v in values)
        cohort='sparse_or_intermittent' if observed and positive/observed<.2 else 'regular'
        entries={}
        for c in candidates:
            output=predict(c,values,data['contract']['start'])
            entries[c.name]=dict(output,identity=f"{data['contract']['branch_id']}:{data['contract']['sku_id']}",
                branch=data['contract']['branch_id'],category='unavailable_source_category',cohort=cohort,
                origin=data['contract']['training_end'],score=score(actual,output['predictions'],values))
            entries[c.name].pop('predictions')  # No model object or row-level labels serialized.
        return entries
    for origin in protocol['validation_origins']:
        folds.append(dict(origin_index=origin,training_end=histories[0]['days'][origin]['date'],
            label_start=histories[0]['days'][origin+1]['date'],label_end=histories[0]['days'][origin+7]['date']))
        for history in histories:
            for name,row in run(history,origin,CANDIDATES).items():records[name].append(row)
    results={name:dict(overall=aggregate(rows),groups=breakdown(rows)) for name,rows in records.items()}
    selection=select_champion(results)  # Receives validation summaries only, not histories/test labels.
    test={};test_evaluations=0
    if protocol['test_origins'] and selection['champion']:
        frozen=[c for c in CANDIDATES if c.name in (selection['champion'],selection['reference'])]
        test_records={c.name:[] for c in frozen}
        for origin in protocol['test_origins']:
            for history in histories:
                for name,row in run(history,origin,frozen).items():test_records[name].append(row)
        test={name:dict(overall=aggregate(rows),groups=breakdown(rows)) for name,rows in test_records.items()}
        test_evaluations=1
    def without_timings(value):
        if isinstance(value,dict):return {k:without_timings(v) for k,v in value.items() if k not in ('fit_ms','inference_ms')}
        if isinstance(value,list):return [without_timings(v) for v in value]
        return value
    semantic=dict(protocol=protocol,selection=selection,validation=without_timings(results),locked_test=without_timings(test),
        histories=[h['history_identity'] for h in histories])
    return dict(version=VERSION,protocol=protocol,folds=folds,candidates=[asdict(c) for c in CANDIDATES],
        validation=results,selection=selection,locked_test=test,locked_test_evaluations=test_evaluations,
        selected_series=len(histories),full_protocol_eligible_series=len(histories) if protocol['test_origins'] else 0,
        decision_status='full_protocol_evaluated' if test_evaluations else 'provisional_limited_validation' if folds else 'insufficient_history',
        reproducibility_sha256=hashlib.sha256(json.dumps(semantic,sort_keys=True,allow_nan=False).encode()).hexdigest())
