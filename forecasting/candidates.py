"""Bounded candidates; every estimator/preprocessor is newly fitted per origin."""
from dataclasses import dataclass
from datetime import date,timedelta
import math
import time
from types import SimpleNamespace
import numpy as np
from forecasting import features


@dataclass(frozen=True)
class Candidate:
    name:str
    family:str
    parameter:float=0


# Frozen before outcomes: 7 baseline configurations, 6 legacy/ML configurations.
CANDIDATES=(Candidate('naive','baseline'),Candidate('mean7','baseline',7),Candidate('mean28','baseline',28),
    Candidate('seasonal_naive7','baseline'),Candidate('seasonal_mean2','baseline',2),
    Candidate('seasonal_mean4','baseline',4),Candidate('sba_croston','baseline',.1),
    Candidate('legacy_regression','ml'),Candidate('observed_trend','ml'),
    Candidate('calendar_lag_linear','ml'),Candidate('ridge1','ml',1),Candidate('ridge10','ml',10),
    Candidate('hist_gradient_boosting','ml'))


def average(values):
    known=[v for v in values if v is not None]
    return sum(known)/len(known) if known else None


def baseline(candidate,values,horizon):
    known=[v for v in values if v is not None]
    if not known:return None,'no_observed_training'
    fallback=average(values[-28:])
    if fallback is None:fallback=average(values)
    name=candidate.name
    if name=='naive':return [known[-1]]*horizon,None
    if name.startswith('mean'):
        recent=average(values[-int(candidate.parameter):])
        return [fallback if recent is None else recent]*horizon,'missing_recent_window' if recent is None else None
    if name in ('seasonal_naive7','seasonal_mean2','seasonal_mean4'):
        weeks=1 if name=='seasonal_naive7' else int(candidate.parameter)
        predictions=[];missing=False
        for step in range(horizon):
            observations=[values[len(values)+step-7*w] for w in range(1,weeks+1)
                if 0<=len(values)+step-7*w<len(values) and values[len(values)+step-7*w] is not None]
            missing |= not observations
            predictions.append(average(observations) if observations else fallback)
        return predictions,'missing_seasonal_slots' if missing else None
    if name=='sba_croston':
        # Unknown days cannot be counted as zero intervals in Croston.
        if any(v is None for v in values):return [fallback]*horizon,'croston_incomplete_calendar'
        positions=[i for i,v in enumerate(values) if v>0]
        if not positions:return [0.0]*horizon,None
        alpha=candidate.parameter;z=values[positions[0]];interval=positions[0]+1;previous=positions[0]
        for i in positions[1:]:
            z+=alpha*(values[i]-z);interval+=alpha*(i-previous-interval);previous=i
        return [(1-alpha/2)*z/interval]*horizon,None
    raise ValueError('Unknown baseline')


def predict(candidate,values,start,horizon=7):
    if candidate not in CANDIDATES:raise ValueError('Unapproved candidate')
    if horizon!=7:raise ValueError('T19 pins a seven-day horizon')
    started=time.perf_counter();fit_ms=0;reason=None;error=None;predictions=None
    try:
        if candidate.family=='baseline':predictions,reason=baseline(candidate,values,horizon)
        elif candidate.name=='legacy_regression':
            # Keep the original implementation unchanged. Its zero fill is unsafe
            # on incomplete calendars, so score its documented fallback there.
            if any(v is None for v in values):reason='legacy_requires_complete_calendar'
            else:
                from ai_model import forecast_demand
                sales=[SimpleNamespace(sale_date=date.fromisoformat(start)+timedelta(days=i),quantity=v)
                    for i,v in enumerate(values) if v>0]
                t=time.perf_counter();old=forecast_demand(sales,date.fromisoformat(start)+timedelta(days=len(values)-1))
                fit_ms=(time.perf_counter()-t)*1000
                if old['status']=='eligible':predictions=[old['predicted_demand']/7]*7
                else:reason='legacy_'+old['status']
        else:
            from sklearn.linear_model import LinearRegression,Ridge
            from sklearn.ensemble import HistGradientBoostingRegressor
            from sklearn.impute import SimpleImputer
            from sklearn.pipeline import make_pipeline
            from sklearn.preprocessing import StandardScaler
            if candidate.name=='observed_trend':
                x=np.asarray([[i] for i,v in enumerate(values) if v is not None],dtype=float)
                y=np.asarray([v for v in values if v is not None],dtype=float);model=LinearRegression()
            else:
                x,y=features.training(values,start)
                estimator=(HistGradientBoostingRegressor(max_iter=40,max_leaf_nodes=7,min_samples_leaf=8,
                    learning_rate=.08,l2_regularization=1,early_stopping=False,random_state=42)
                    if candidate.name=='hist_gradient_boosting' else
                    Ridge(alpha=candidate.parameter) if candidate.name.startswith('ridge') else LinearRegression())
                model=make_pipeline(SimpleImputer(strategy='median',add_indicator=True,keep_empty_features=True),
                    StandardScaler(),estimator)
            if len(y)<20:reason='insufficient_training_targets'
            else:
                t=time.perf_counter();model.fit(x,y);fit_ms=(time.perf_counter()-t)*1000
                recursive=list(values);predictions=[]
                for _ in range(horizon):
                    vector=[len(recursive)] if candidate.name=='observed_trend' else features.row(recursive,len(recursive),start)
                    value=float(model.predict(np.asarray([vector]))[0])
                    if not math.isfinite(value):raise ValueError('Nonfinite prediction')
                    value=max(0.0,value)
                    predictions.append(value);recursive.append(value)
        if predictions is not None and (len(predictions)!=7 or any(not math.isfinite(p) or p<0 for p in predictions)):
            predictions=None;reason='nonfinite_or_negative_prediction'
    except Exception as exc:
        # Classification only: no SQL, input rows, paths or credentials in outputs.
        error=type(exc).__name__;reason='candidate_exception';predictions=None
    if predictions is None:
        predictions,fallback_reason=baseline(CANDIDATES[2],values,horizon)
        reason=reason or fallback_reason or 'candidate_unavailable'
    elapsed=(time.perf_counter()-started)*1000
    return dict(predictions=predictions,native=reason is None,fallback_reason=reason,error_type=error,
        fit_ms=fit_ms,inference_ms=max(0,elapsed-fit_ms))
