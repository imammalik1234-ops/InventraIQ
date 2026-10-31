"""Calendar-day linear regression; eligibility rules are not confidence scores."""
from datetime import timedelta
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error


def forecast_demand(sales, today):
    result = dict(status='insufficient_data', reason='Need 14 calendar days and 7 selling days.',
                  predicted_demand=None, horizon_days=7, mae=None, history_days=0,
                  selling_days=0, method='daily_linear_regression')
    daily = {}
    for sale in sales:
        if sale.sale_date is None or sale.sale_date > today or sale.quantity is None or sale.quantity <= 0:
            return dict(result, status='invalid_data', reason='History has missing/future dates or nonpositive quantities.')
        daily[sale.sale_date] = daily.get(sale.sale_date, 0) + sale.quantity
    if not daily:
        return result
    days = (today - min(daily)).days + 1
    result.update(history_days=days, selling_days=len(daily))
    if days < 14 or len(daily) < 7:
        return result
    if (today - max(daily)).days > 7:
        return dict(result, status='stale_data', reason='No sales recorded in the last 7 days.')
    start = min(daily)
    y = np.array([daily.get(start + timedelta(days=i), 0) for i in range(days)], dtype=float)
    x = np.arange(days).reshape(-1, 1)
    split = days - max(3, days // 5)
    model = LinearRegression().fit(x[:split], y[:split])
    mae = float(mean_absolute_error(y[split:], np.maximum(model.predict(x[split:]), 0)))
    result['mae'] = round(mae, 3)
    if not np.isfinite(mae) or mae > max(1.0, float(y[split:].mean())):
        return dict(result, status='unreliable', reason='Holdout MAE exceeds max(1 unit, holdout daily mean).')
    model.fit(x, y)
    predicted = np.maximum(model.predict(np.arange(days, days + 7).reshape(-1, 1)), 0).sum()
    if not np.isfinite(predicted):
        return dict(result, status='unreliable', reason='Nonfinite forecast.')
    return dict(result, status='eligible', reason='Passed history, freshness and chronological holdout rules.',
                predicted_demand=round(float(predicted), 3))
