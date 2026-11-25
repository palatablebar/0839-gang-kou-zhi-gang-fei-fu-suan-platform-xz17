from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from app.handover.core import HandoverError, issue_handover, split_handover
from app.review.service import serialize as serialize_run


def _json_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_run_or_404(store, run_id: str) -> dict[str, Any]:
    row = store.get(run_id)
    if row is None:
        raise HandoverError("RUN_NOT_FOUND", "批次不存在", 404)
    return serialize_run(row)


def find_container_result(run: dict[str, Any], container_id: str | None) -> dict[str, Any]:
    container_id = str(container_id or "").strip()
    candidates = []
    for key in ("computed_rows", "results", "items"):
        if isinstance(run.get(key), list):
            candidates.extend(run[key])
    if isinstance(run.get("rows"), list):
        candidates.extend(run["rows"])

    if not container_id:
        if len(candidates) == 1:
            return candidates[0]
        raise HandoverError("CONTAINER_REQUIRED", "请选择箱子", 400)

    for item in candidates:
        if not isinstance(item, dict):
            continue
        identifiers = (
            item.get("container_id"), item.get("key"), item.get("label"), item.get("id"),
        )
        if any(str(value) == container_id for value in identifiers if value is not None):
            if item.get("fee_cents") is not None or item.get("charge_days") is not None or item.get("free_end"):
                return item

    # Stored run payloads intentionally contain only a preview table. Recompute
    # from the same immutable sample/profile when the requested box is beyond it.
    profile = run.get("profile")
    if profile:
        from app.service import pipeline
        fresh = pipeline(profile)
        for item in fresh.get("computed_rows", []):
            if str(item.get("container_id") or "") == container_id:
                return item

    raise HandoverError("CONTAINER_NOT_FOUND", f"批次中找不到可复算箱子 {container_id}", 404)


def handover_params(body: dict[str, Any]) -> dict[str, Any]:
    params = dict(body or {})
    nested = params.pop("handover", None)
    if isinstance(nested, dict):
        merged = dict(nested)
        merged.update({k: v for k, v in params.items() if v is not None})
        params = merged
    params.pop("run_id", None)
    params.pop("action", None)
    params.pop("issue", None)
    params.pop("issue_now", None)
    params.pop("preview", None)
    params.pop("expected_revision", None)
    params.pop("decision", None)
    params.pop("comment", None)
    return params


def preview_for_run(store, run_id: str, body: dict[str, Any]):
    run = get_run_or_404(store, run_id)
    params = handover_params(body)
    container_id = params.pop("container_id", None) or params.pop("key", None)
    item = find_container_result(run, container_id)
    container_id = container_id or item.get("container_id") or item.get("label") or item.get("key")
    return split_handover(item, container_id=container_id, **params)


def _request_key(run_id: str, container_id: str, params: dict[str, Any], body: dict[str, Any]) -> str:
    client_key = (
        body.get("idempotency_key") or body.get("idempotency") or body.get("request_id")
        or params.get("idempotency_key")
    )
    if client_key:
        return "client:" + str(client_key)
    canonical = {
        "run_id": run_id,
        "container_id": container_id,
        "old_owner": params.get("old_owner") or params.get("previous_owner"),
        "new_owner": params.get("new_owner") or params.get("next_owner"),
        "dates": params.get("handover_dates") or params.get("dates") or params.get("records")
        or params.get("handovers") or [params.get("handover_date"), params.get("second_handover_date")],
        "mode": params.get("boundary") or params.get("handover_day") or ("end" if params.get("inclusive") else "start"),
    }
    return "deterministic:" + _json_digest(canonical)[:28]


def _ledger_from_store(store, run_id: str, container_id: str):
    rows = store.list_day_charges(run_id=run_id, container_id=container_id)
    ledger = []
    for row in rows:
        item = {
            "charge_id": row["id"],
            "handover_id": row["handover_id"],
            "container_id": row["container_id"],
            "charge_date": row["charge_date"],
            "owner_role": row["owner_role"],
            "amount_cents": row["amount_cents"],
        }
        try:
            item.update(json.loads(row.get("payload") or "{}"))
        except json.JSONDecodeError:
            pass
        item["charge_id"] = row["id"]
        ledger.append(item)
    return ledger


def serialize_handover(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = json.loads(row["payload"])
    payload.update({
        "id": row["id"],
        "handover_id": row["id"],
        "run_id": row["run_id"],
        "container_id": row["container_id"],
        "status": row["status"],
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    })
    return payload


def issue_for_run(store, run_id: str, body: dict[str, Any]):
    run = get_run_or_404(store, run_id)
    raw_body = dict(body or {})
    params = handover_params(raw_body)
    container_id = params.pop("container_id", None) or params.pop("key", None)
    item = find_container_result(run, container_id)
    container_id = container_id or item.get("container_id") or item.get("label") or item.get("key")

    should_issue = raw_body.get("issue", raw_body.get("issue_now", True))
    if isinstance(should_issue, str):
        should_issue = should_issue.lower() not in {"false", "0", "no", "preview"}
    if raw_body.get("action") == "preview" or raw_body.get("preview") is True:
        should_issue = False

    split = split_handover(item, container_id=container_id, **params)
    if not should_issue:
        return split

    request_key = _request_key(run_id, str(container_id), params, raw_body)
    existing = store.find_handover_by_request(request_key)
    if existing:
        payload = serialize_handover(existing)
        payload["created"] = False
        payload["duplicate"] = True
        payload["idempotent_replay"] = True
        payload["already_issued"] = True
        return payload

    handover_id = "hv_" + uuid.uuid4().hex[:12]
    ledger = _ledger_from_store(store, run_id, str(container_id))
    issued = issue_handover(
        split,
        ledger=ledger,
        handover_id=handover_id,
        idempotency_key=request_key,
    )
    issued["request_key"] = request_key
    row = store.create_handover(
        handover_id, request_key, run_id, str(container_id), issued["status"], issued
    )
    for charge in issued.get("charges", []):
        store.insert_day_charge(
            charge["charge_id"], handover_id, run_id, str(container_id), charge["charge_date"],
            charge["owner_role"], charge["amount_cents"], charge,
        )
    result = serialize_handover(store.get_handover(row["id"]))
    result["created"] = True
    result["duplicate"] = False
    return result


def issue_existing(store, handover_id: str, body: dict[str, Any] | None = None):
    row = store.get_handover(handover_id)
    if row is None:
        raise HandoverError("HANDOVER_NOT_FOUND", "交接分账不存在", 404)
    current = serialize_handover(row)
    body = body or {}

    if any(key in body for key in (
        "handover_date", "second_handover_date", "handover_dates", "dates", "records", "handovers"
    )):
        run = get_run_or_404(store, row["run_id"])
        params = handover_params(body)
        container_id = params.pop("container_id", None) or row["container_id"]
        item = find_container_result(run, container_id)
        params.setdefault("old_owner", current.get("old_owner"))
        params.setdefault("new_owner", current.get("new_owner"))
        current = split_handover(item, container_id=container_id, **params)

    ledger = _ledger_from_store(store, row["run_id"], row["container_id"])
    issued = issue_handover(
        current,
        ledger=ledger,
        handover_id=handover_id,
        idempotency_key=current.get("idempotency_key") or row["request_key"],
    )
    store.save_handover(handover_id, issued["status"], issued)
    for charge in issued.get("charges", []):
        store.insert_day_charge(
            charge["charge_id"], handover_id, row["run_id"], row["container_id"], charge["charge_date"],
            charge["owner_role"], charge["amount_cents"], charge,
        )
    return serialize_handover(store.get_handover(handover_id))


def list_for_run(store, run_id: str):
    get_run_or_404(store, run_id)
    return [serialize_handover(row) for row in store.list_handovers(run_id=run_id)]


def reconciliation_for_run(store, run_id: str, container_id: str | None = None):
    items = list_for_run(store, run_id)
    if container_id:
        items = [item for item in items if str(item.get("container_id")) == str(container_id)]
    from app.reconcile.handover import aggregate_handover_reconciliation
    return aggregate_handover_reconciliation(items)


class HandoverService:
    """Small object facade for callers that prefer service objects."""

    def __init__(self, store):
        self.store = store

    def preview(self, run_id: str, body: dict[str, Any]):
        return preview_for_run(self.store, run_id, body or {})

    def create(self, run_id: str, body: dict[str, Any]):
        return issue_for_run(self.store, run_id, body or {})

    issue = create
    issue_for_run = create

    def issue_existing(self, handover_id: str, body: dict[str, Any] | None = None):
        return issue_existing(self.store, handover_id, body or {})

    retry = issue_existing

    def get(self, handover_id: str):
        return serialize_handover(self.store.get_handover(handover_id))

    def list_for_run(self, run_id: str):
        return list_for_run(self.store, run_id)
