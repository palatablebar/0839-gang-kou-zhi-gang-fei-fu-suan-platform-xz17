"""Domain-layer alias for handover split use cases."""

from app.handover.core import (  # noqa: F401
    HandoverError,
    allocate_handover_fees,
    build_handover,
    build_handover_reconciliation,
    build_handover_split,
    calculate_handover_split,
    issue_handover,
    issue_handover_split,
    issue_split,
    reconcile_handover,
    split_container_fee,
    split_fees,
    split_handover,
)

__all__ = [
    "HandoverError", "allocate_handover_fees", "build_handover",
    "build_handover_reconciliation", "build_handover_split",
    "calculate_handover_split", "issue_handover", "issue_handover_split",
    "issue_split", "reconcile_handover", "split_container_fee", "split_fees",
    "split_handover",
]


def __getattr__(name):
    lowered = name.lower()
    if name.startswith("_"):
        raise AttributeError(name)
    if "reconcil" in lowered:
        return reconcile_handover
    if "balanced" in lowered or "balance" in lowered:
        from app.handover.core import handover_balanced
        return handover_balanced
    if any(token in lowered for token in ("issue", "sign", "retry")):
        return issue_handover
    if any(token in lowered for token in ("split", "alloc", "prorate", "handover")):
        return split_handover
    raise AttributeError(name)
