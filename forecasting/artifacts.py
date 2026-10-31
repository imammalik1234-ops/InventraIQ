"""Trusted JSON configuration artifact. No uploaded paths, pickle or model loading."""
import hashlib
import json
from pathlib import Path
from security import MutationError

MODEL='seasonal_mean2'
VERSION='synthetic-demo-v1'


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def trusted():
    return dict(artifact_schema='inventraiq-forecast-artifact-v1',model_id=MODEL,model_version=VERSION,
        qualification='provisional_synthetic',production_publication=False,real_data=False,horizon=7,
        parameters=dict(weeks=2,minimum_calendar_days=56,minimum_observed_days=28),
        feature_schema='seasonal-weekday-lags-v1',data_schema='inventraiq-forecast-data-v1',
        implementation_sha256=hashlib.sha256((Path(__file__).parent/'candidates.py').read_bytes()).hexdigest(),
        data_implementation_sha256=hashlib.sha256((Path(__file__).parent/'data.py').read_bytes()).hexdigest(),
        publisher_implementation_sha256=hashlib.sha256((Path(__file__).parent/'jobs.py').read_bytes()).hexdigest(),
        evaluation_reference='ff45ea1ac08c4f9b6aed568bad5466207b12f02ee1788c52155c2c9d773aa0fa',
        evaluation_status='limited_validation_no_locked_test')


def validate(encoded,content_hash):
    try:
        if not isinstance(encoded,str) or len(encoded)>8192:raise ValueError()
        value=json.loads(encoded)
        if digest(value)!=content_hash or value!=trusted():raise ValueError()
        return value
    except (ValueError,TypeError):raise MutationError('Forecast artifact incompatible or damaged.',409) from None


def validate_run(run):
    value=validate(run['artifact_json'],run['artifact_hash'])
    if any(run[key]!=value[key] for key in ('model_id','model_version','qualification','horizon')):
        raise MutationError('Forecast run and artifact versions disagree.',409)
    return value
