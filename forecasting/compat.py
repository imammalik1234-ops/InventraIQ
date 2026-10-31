"""Legacy forecast-result shape without invoking the legacy regression model."""


def withheld_forecast(history):
    training=history['training']
    return dict(status='withheld',reason='No compatible published snapshot for this historical origin; no model is fitted here.',
        predicted_demand=None,horizon_days=7,mae=None,history_days=len(training),
        selling_days=sum(r['eligible'] and r['units']>0 for r in training),method='data_contract_only')
