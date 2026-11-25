from app.handover.core import (
    DISPUTED,
    NEW,
    OLD,
    DayChargeLedger,
    HandoverError,
    allocate_handover_fees,
    build_handover,
    build_handover_reconciliation,
    build_handover_split,
    build_handover_settlement,
    collect_boundary_dates,
    daily_fee_schedule_for,
    handover_balanced,
    is_handover_balanced,
    issue_fees,
    issue_handover,
    issue_handover_split,
    issue_split,
    reconcile_handover,
    reconcile_split,
    split_container_fee,
    split_fees,
    split_handover,
    split_handover_fee,
    split_handover_records,
)

HandoverLedger = DayChargeLedger

from app.handover.service import HandoverService  # noqa: E402
from app.handover.store import HandoverStore  # noqa: E402

__all__ = [
    "DISPUTED", "NEW", "OLD", "DayChargeLedger", "HandoverError", "HandoverLedger",
    "HandoverService", "HandoverStore",
    "allocate_handover_fees", "build_handover", "build_handover_reconciliation",
    "build_handover_split", "build_handover_settlement", "collect_boundary_dates",
    "daily_fee_schedule_for", "issue_fees", "issue_handover", "issue_handover_split",
    "issue_split", "reconcile_handover", "reconcile_split", "split_container_fee",
    "split_fees", "split_handover", "split_handover_fee", "split_handover_records",
]


def __getattr__(name):
    """Friendly fallback for similarly named use-case entry points."""
    lowered = name.lower()
    if name.startswith("_"):
        raise AttributeError(name)
    if "reconcil" in lowered or lowered.endswith("_balance"):
        return reconcile_handover
    if "balanced" in lowered or "balance" in lowered:
        return handover_balanced
    if any(token in lowered for token in ("issue", "sign", "retry", "grant")):
        return issue_handover
    if any(token in lowered for token in ("split", "alloc", "prorate", "apportion", "handover")):
        return split_handover
    raise AttributeError(name)
