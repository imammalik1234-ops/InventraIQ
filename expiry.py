"""Shared pure expiry semantics. No stock deductions or caller-controlled HTTP clock."""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import version
from importlib.resources import files
import re
from zoneinfo import ZoneInfo
from security import MutationError

UTC = timezone.utc
TICK = timedelta(seconds=1)  # Existing MySQL DATETIME precision: one second.
POLICY_FIELDS = ('require_expiry', 'early_days', 'high_days', 'critical_days',
                 'expected_shelf_life_days', 'min_remaining_days_on_receipt',
                 'min_remaining_days_on_transfer_receipt')


def utcnow():
    from simulation_clock import current_instant
    simulated=current_instant()
    if simulated is not None:return simulated
    return datetime.now(UTC).replace(microsecond=0)


def zone(name):
    # Use the declared package on every host, and record that exact tzdata version.
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_+/-]{1,64}', name) or any(p in ('', '.', '..') for p in name.split('/')):
        raise MutationError('A valid IANA timezone is required.')
    try:
        with files('tzdata.zoneinfo').joinpath(*name.split('/')).open('rb') as source:
            return ZoneInfo.from_file(source, key=name)
    except (OSError, ValueError):
        raise MutationError('A valid IANA timezone is required.') from None


def integer(value, name, minimum=0, maximum=3650):
    if type(value) is int:
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 10:
        number = int(value)
    else:
        raise MutationError(name+' must be an integer.')
    if not minimum <= number <= maximum:
        raise MutationError(name+' is outside its allowed range.')
    return number


def validate_policy(values):
    if not isinstance(values, dict) or set(values) != set(POLICY_FIELDS):
        raise MutationError('Supply a complete expiry policy; partial overrides are not supported.')
    if type(values['require_expiry']) is not bool:
        raise MutationError('require_expiry must be a boolean.')
    clean = dict(require_expiry=values['require_expiry'])
    for field in POLICY_FIELDS[1:]:
        value=values[field]
        clean[field] = None if field == 'expected_shelf_life_days' and value in (None, '') else integer(value,field,minimum=1 if field == 'expected_shelf_life_days' else 0)
    if not clean['critical_days'] < clean['high_days'] < clean['early_days']:
        raise MutationError('Thresholds must satisfy 0 <= critical < high < early <= 3650.')
    return clean


@dataclass(frozen=True)
class Policy:
    source: str
    key: int
    revision: int
    effective_at: str
    require_expiry: bool
    early_days: int
    high_days: int
    critical_days: int
    expected_shelf_life_days: int | None
    min_remaining_days_on_receipt: int
    min_remaining_days_on_transfer_receipt: int

    def snapshot(self):
        return asdict(self)


def parse_date(value):
    if type(value) is date: return value
    if isinstance(value,str):
        try:
            parsed=date.fromisoformat(value)
            if parsed.isoformat() == value: return parsed
        except ValueError: pass
    raise MutationError('Supply an actual expiry date in YYYY-MM-DD format.')


def canonical_expiry(actual_date, timezone_name):
    actual_date=parse_date(actual_date)
    tz=zone(timezone_name)
    try: midnight=datetime.combine(actual_date+timedelta(days=1),datetime.min.time())
    except OverflowError: raise MutationError('Expiry date cannot be interpreted.') from None
    def candidates(local):
        found=set()
        for fold in (0,1):
            candidate=local.replace(tzinfo=tz,fold=fold).astimezone(UTC)
            if candidate.astimezone(tz).replace(tzinfo=None) == local: found.add(candidate)
        return sorted(found)
    # Earlier instant for ambiguous midnight; first valid second for a gap.
    for minute in range(1440):
        local=midnight+timedelta(minutes=minute)
        found=candidates(local)
        if found:
            if minute:
                for second in range((minute-1)*60,minute*60):
                    earlier=candidates(midnight+timedelta(seconds=second))
                    if earlier: return earlier[0]
            return found[0]
    for second in range(1439*60+1,86400):
        found=candidates(midnight+timedelta(seconds=second))
        if found:return found[0]
    raise MutationError('The label boundary skips a calendar date; headquarters review is required.')


def classify(*, policy, expiry_date, expiry_timezone, expires_at_utc, branch_timezone, as_of,
             validation_state='validated'):
    if as_of.tzinfo is None: raise ValueError('A trusted aware evaluation instant is required.')
    result=dict(status='review', days_remaining=None, expired=False)
    if policy is None or validation_state != 'validated':return result
    try: clean=validate_policy({field:getattr(policy,field) for field in POLICY_FIELDS})
    except MutationError:return result
    if expiry_date is None:
        if expiry_timezone is not None or expires_at_utc is not None or policy.require_expiry:return result
        return dict(result,status='not_tracked')
    if not expiry_timezone or expires_at_utc is None:return result
    instant=expires_at_utc.replace(tzinfo=UTC) if expires_at_utc.tzinfo is None else expires_at_utc.astimezone(UTC)
    try:
        branch_zone=zone(branch_timezone)
        zone(expiry_timezone)
    except MutationError:return result
    remaining=((instant-TICK).astimezone(branch_zone).date()-as_of.astimezone(branch_zone).date()).days
    if as_of.astimezone(UTC) >= instant:return dict(status='expired',days_remaining=remaining,expired=True)
    status=('critical' if remaining <= clean['critical_days'] else 'high' if remaining <= clean['high_days']
            else 'early' if remaining <= clean['early_days'] else 'healthy')
    return dict(status=status,days_remaining=remaining,expired=False)


def conversion_provenance():
    return dict(tzdata_version=version('tzdata'), timestamp_precision='second', meaning='last-valid-label-date')
