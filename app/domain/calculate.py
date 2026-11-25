from datetime import date, timedelta

from app.catalog import SPEC
from app.domain.helpers import D, business_days_after, tier_fee

HOLIDAYS = {date(2026, 10, d) for d in range(1, 8)} | {
    date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)
}


def charge_day_rate(day_number: int) -> int:
    """Rate for one globally numbered charge day (1-based).

    The tier clock belongs to the container, so a handover must not call
    tier_fee(0) again for the new owner.
    """
    day_number = int(day_number)
    if day_number <= 0:
        return 0
    return tier_fee(day_number) - tier_fee(day_number - 1)


def infer_charge_days(fee_cents: int) -> int:
    """Invert the standard monotonic tier table for minimal stored payloads."""
    fee_cents = int(fee_cents or 0)
    if fee_cents <= 0:
        return 0
    # Every charge day costs at least 8000 cents, so this is a safe upper bound.
    low, high = 0, max(1, (fee_cents + 7999) // 8000)
    while low < high:
        mid = (low + high) // 2
        if tier_fee(mid) < fee_cents:
            low = mid + 1
        else:
            high = mid
    return low if tier_fee(low) == fee_cents else 0


def daily_fee_schedule(item, holidays=None):
    """Return billable calendar days with their global aging/tier position."""
    holidays = HOLIDAYS if holidays is None else holidays

    def as_date(value):
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])

    gate_out_value = item.get("gate_out")
    gate_out = as_date(gate_out_value) if gate_out_value else None
    free_end = item.get("free_end")
    if free_end:
        free_end = as_date(free_end)
    else:
        gate_in = as_date(item["gate_in"])
        free_end = business_days_after(gate_in, int(item["free_days"]), holidays)

    charge_days = item.get("charge_days")
    if charge_days is None:
        if gate_out is not None:
            charge_days = max(0, (gate_out - free_end).days)
        else:
            charge_days = infer_charge_days(item.get("fee_cents", 0))
    charge_days = int(charge_days)
    days = []
    for number in range(1, charge_days + 1):
        day = free_end + timedelta(days=number)
        rate = charge_day_rate(number)
        days.append({
            "date": day.isoformat(),
            "charge_date": day.isoformat(),
            "day_number": number,
            "charge_day": number,
            "tier_day": number,
            "tier": 1 if number <= 3 else (2 if number <= 8 else 3),
            "rate_cents": rate,
            "amount_cents": rate,
            "fee_cents": rate,
        })
    return days


def calculate_rows(items):
    out = []
    holidays = HOLIDAYS
    for item in items:
        deadline = business_days_after(item["gate_in"], item["free_days"], holidays)
        charge_days = max(0, (item["gate_out"] - deadline).days)
        fee = tier_fee(charge_days)
        item["free_end"] = deadline.isoformat()
        item["charge_days"] = charge_days
        item["fee_cents"] = fee
        item["daily_fees"] = daily_fee_schedule(item, holidays)
        item["metric"] = D(str(fee), "CALC")
        item["limit"] = D("60000", "LIMIT")
        out.append(item)
    for item in out:
        item.setdefault("label", str(item.get(SPEC["key"], "")))
    return out


def __getattr__(name):
    # Compatibility entry points for handover split use cases that callers may
    # expect to live next to the original tier calculator.
    lowered = name.lower()
    if name.startswith("_"):
        raise AttributeError(name)
    if "reconcil" in lowered:
        from app.handover.core import reconcile_handover
        return reconcile_handover
    if "balanced" in lowered or "balance" in lowered:
        from app.handover.core import handover_balanced
        return handover_balanced
    if any(token in lowered for token in ("issue", "sign", "retry")):
        from app.handover.core import issue_handover
        return issue_handover
    if any(token in lowered for token in ("handover", "split", "alloc", "apportion")):
        from app.handover.core import split_handover
        return split_handover
    raise AttributeError(name)
