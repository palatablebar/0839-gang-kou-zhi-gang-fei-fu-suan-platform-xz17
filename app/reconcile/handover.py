"""Reconciliation helpers for owner handover split charges."""

from __future__ import annotations

from typing import Any, Iterable

from app.handover.core import reconcile_handover

__all__ = ["reconcile_handover", "aggregate_handover_reconciliation"]


def aggregate_handover_reconciliation(
    handovers: Iterable[dict[str, Any]],
    whole_container_cents: int | None = None,
) -> dict[str, Any]:
    items = list(handovers or [])
    old = sum(int(item.get("signed_old_owner_cents", item.get("old_signed_cents", 0)) or 0) for item in items)
    new = sum(int(item.get("signed_new_owner_cents", item.get("new_signed_cents", 0)) or 0) for item in items)
    pending = sum(int(item.get("pending_cents", 0) or 0) for item in items)
    if whole_container_cents is None:
        whole = sum(int(item.get("whole_container_cents", item.get("total_cents", 0)) or 0) for item in items)
    else:
        whole = int(whole_container_cents)
    result = reconcile_handover({
        "whole_container_cents": whole,
        "signed_old_owner_cents": old,
        "signed_new_owner_cents": new,
        "pending_cents": pending,
    })
    result["handover_count"] = len(items)
    return result
