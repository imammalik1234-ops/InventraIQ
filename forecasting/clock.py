"""Explicit historical dates and trusted live/demo instants; no stock-clock writes."""
from datetime import date, datetime, timezone
from security import MutationError
from expiry import zone


def business_date(instant, timezone_name):
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        raise MutationError('An aware business-clock instant is required.')
    return instant.astimezone(zone(timezone_name)).date()


def iso_date(value):
    if type(value) is date:return value
    if not isinstance(value,str):raise MutationError('Use an ISO business date.')
    try:
        result=date.fromisoformat(value)
        if result.isoformat()!=value:raise ValueError()
        return result
    except ValueError:raise MutationError('Use an ISO business date.') from None


def clocks(branches):
    from simulation_clock import current_instant
    live=datetime.now(timezone.utc)
    simulated=current_instant()
    return dict(live_utc=live.isoformat(),operational_clock='simulated_demo' if simulated else 'live',
        operational_utc=(simulated or live).isoformat(),branches=[dict(branch_id=b.branch_id,
            timezone=b.timezone,business_date=business_date(simulated or live,b.timezone).isoformat()) for b in branches])
