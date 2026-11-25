from flask import Blueprint, abort, jsonify, request, send_file

from app.catalog import SPEC
from app.config import SAMPLES
from app.errors import DomainIssue
from app.evidence.render import render
from app.handover.core import HandoverError, reconcile_handover
from app.handover.service import issue_existing, issue_for_run, list_for_run, preview_for_run, reconciliation_for_run, serialize_handover
from app.review.service import approve, create_run, serialize
from app.service import pipeline


api = Blueprint("api", __name__)
STAGE_INDEX = {"intake": 1, "prepare": 2, "calculate": 3, "decision": 4, "reconcile": 5}


def store():
    from flask import current_app
    return current_app.extensions["runs"]


@api.get("/api/catalog")
def catalog():
    return jsonify(code=SPEC["code"], name=SPEC["name"], actor=SPEC["actor"], rule=SPEC["rule"],
                   rule_short=SPEC["rule_short"],
                   stages=[{"id": item, "name": name} for item, name in zip(
                       ["intake", "prepare", "calculate", "decision", "reconcile", "review", "evidence"],
                       SPEC["stages"])],
                   chart=SPEC["chart"], result_columns=SPEC["result_columns"],
                   columns=SPEC["columns"], samples=["dirty", "clean"])


@api.post("/api/stages/<stage_id>")
def stage(stage_id):
    data = request.get_json(silent=True) or {}
    if stage_id in {"handover", "handover-split", "handover_split"}:
        from app.handover.core import split_handover
        from app.handover.service import find_container_result
        profile = data.get("profile", "clean")
        payload = pipeline(profile)
        stage_data = dict(data)
        container_id = stage_data.pop("container_id", None) or stage_data.pop("key", None)
        item = find_container_result(payload, container_id)
        container_id = container_id or item.get("container_id") or item.get("label")
        return jsonify(split_handover(item, container_id=container_id, **stage_data))
    if stage_id not in STAGE_INDEX:
        raise DomainIssue("STAGE_UNKNOWN", 404)
    profile = data.get("profile", "dirty")
    upto = SPEC["stages"][STAGE_INDEX[stage_id] - 1]
    return jsonify(pipeline(profile, upto=upto))


@api.post("/api/runs")
def create():
    data = request.get_json(silent=True) or {}
    profile = data.get("profile", "dirty")
    payload = pipeline(profile)
    return jsonify(create_run(store(), payload))


@api.get("/api/runs/<run_id>")
def get_run(run_id):
    run = serialize(store().get(run_id))
    if not run:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    run["handovers"] = list_for_run(store(), run_id)
    run["handover"] = run["handovers"][-1] if run["handovers"] else None
    return jsonify(run)


@api.post("/api/runs/<run_id>/review")
def review(run_id):
    data = request.get_json(silent=True) or {}
    if "decision" not in data or "expected_revision" not in data:
        raise DomainIssue("BAD_REQUEST")
    result = approve(store(), run_id, data["decision"], data.get("comment", ""), int(data["expected_revision"]))
    handover_data = data
    if isinstance(data.get("handover"), dict):
        nested = dict(data["handover"])
        nested.update({k: v for k, v in data.items() if k != "handover"})
        handover_data = nested
    has_handover_dates = any(key in handover_data for key in (
        "handover_date", "handover_dates", "first_handover_date", "second_handover_date",
        "dates", "records", "handovers", "transfer_date", "transfer_dates"
    ))
    if (handover_data.get("container_id") or handover_data.get("key") or isinstance(data.get("handover"), dict)) and has_handover_dates:
        handover = issue_for_run(store(), run_id, handover_data)
        result["handover"] = handover
        result["handover_split"] = handover
        result["handovers"] = list_for_run(store(), run_id)
    return jsonify(result)


@api.get("/api/runs/<run_id>/evidence")
def evidence(run_id):
    run = serialize(store().get(run_id))
    if not run:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    run["handovers"] = list_for_run(store(), run_id)
    return jsonify(render(run))


def _with_idempotency_header(data):
    if not data.get("idempotency_key"):
        key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
        if key:
            data["idempotency_key"] = key
    return data


@api.post("/api/runs/<run_id>/handover/preview")
@api.post("/api/runs/<run_id>/handovers/preview")
def run_handover_preview(run_id):
    data = request.get_json(silent=True) or {}
    return jsonify(preview_for_run(store(), run_id, data))


@api.post("/api/runs/<run_id>/handover")
@api.post("/api/runs/<run_id>/handovers")
@api.post("/api/runs/<run_id>/handover-split")
@api.post("/api/runs/<run_id>/handover-splits")
@api.post("/api/runs/<run_id>/transfers")
@api.post("/api/runs/<run_id>/owner-transfers")
@api.post("/api/runs/<run_id>/handover/issue")
@api.post("/api/runs/<run_id>/handovers/issue")
def run_handover_issue(run_id):
    data = _with_idempotency_header(request.get_json(silent=True) or {})
    return jsonify(issue_for_run(store(), run_id, data))


@api.get("/api/runs/<run_id>/handover")
@api.get("/api/runs/<run_id>/handovers")
@api.get("/api/runs/<run_id>/handover-splits")
@api.get("/api/runs/<run_id>/transfers")
@api.get("/api/runs/<run_id>/owner-transfers")
def run_handover_list(run_id):
    items = list_for_run(store(), run_id)
    response = jsonify(items)
    response.headers["X-Total-Count"] = str(len(items))
    return response


@api.get("/api/runs/<run_id>/handovers/reconciliation")
@api.get("/api/runs/<run_id>/handover/reconciliation")
def run_handover_reconciliation(run_id):
    container_id = request.args.get("container_id")
    return jsonify(reconciliation_for_run(store(), run_id, container_id))


@api.post("/api/handovers/preview")
@api.post("/api/handover/preview")
def handover_preview_by_body():
    data = request.get_json(silent=True) or {}
    run_id = data.get("run_id")
    if run_id:
        return jsonify(preview_for_run(store(), run_id, data))
    source = data.get("item") or data.get("container_result") or data.get("result")
    if source is not None:
        from app.handover.core import split_handover
        params = {k: v for k, v in data.items() if k not in {"item", "container_result", "result"}}
        return jsonify(split_handover(source, **params))
    raise DomainIssue("RUN_ID_REQUIRED")


@api.post("/api/handovers")
@api.post("/api/handover")
@api.post("/api/handover/split")
@api.post("/api/handovers/split")
@api.post("/api/handover-splits")
@api.post("/api/transfers")
@api.post("/api/owner-transfers")
def handover_create():
    data = _with_idempotency_header(request.get_json(silent=True) or {})
    run_id = data.get("run_id")
    if run_id:
        return jsonify(issue_for_run(store(), run_id, data))
    source = data.get("item") or data.get("container_result") or data.get("result")
    if source is not None:
        from app.handover.core import issue_handover
        return jsonify(issue_handover(source, **{k: v for k, v in data.items() if k not in {"item", "container_result", "result"}}))
    raise DomainIssue("RUN_ID_REQUIRED")


@api.get("/api/handovers")
def handover_list_all():
    run_id = request.args.get("run_id")
    container_id = request.args.get("container_id")
    rows = store().list_handovers(run_id=run_id, container_id=container_id)
    items = [serialize_handover(row) for row in rows]
    response = jsonify(items)
    response.headers["X-Total-Count"] = str(len(items))
    return response


@api.get("/api/handovers/<handover_id>")
@api.get("/api/handover/<handover_id>")
@api.get("/api/transfers/<handover_id>")
def handover_get(handover_id):
    item = serialize_handover(store().get_handover(handover_id))
    if not item:
        raise DomainIssue("HANDOVER_NOT_FOUND", 404)
    return jsonify(item)


@api.get("/api/handovers/<handover_id>/reconciliation")
@api.get("/api/handover/<handover_id>/reconciliation")
def handover_reconciliation_get(handover_id):
    item = serialize_handover(store().get_handover(handover_id))
    if not item:
        raise DomainIssue("HANDOVER_NOT_FOUND", 404)
    return jsonify(reconcile_handover(item))


@api.post("/api/handovers/reconcile")
@api.post("/api/handover/reconcile")
def handover_reconciliation_post():
    data = request.get_json(silent=True) or {}
    if data.get("handover_id") or data.get("id"):
        item = serialize_handover(store().get_handover(data["handover_id"]))
        if not item:
            raise DomainIssue("HANDOVER_NOT_FOUND", 404)
        return jsonify(reconcile_handover(item))
    return jsonify(reconcile_handover(data))


@api.post("/api/handovers/<handover_id>/issue")
@api.post("/api/handovers/<handover_id>/retry")
@api.post("/api/transfers/<handover_id>/issue")
@api.post("/api/handover/<handover_id>/issue")
@api.post("/api/handover/<handover_id>/retry")
def handover_issue(handover_id):
    data = request.get_json(silent=True) or {}
    return jsonify(issue_existing(store(), handover_id, data))


@api.post("/api/handover/issue")
@api.post("/api/handovers/issue")
@api.post("/api/handover/sign")
@api.post("/api/handovers/sign")
@api.post("/api/handover/retry")
@api.post("/api/handovers/retry")
def handover_issue_by_body():
    data = _with_idempotency_header(request.get_json(silent=True) or {})
    handover_id = data.get("handover_id") or data.get("id")
    if handover_id:
        return jsonify(issue_existing(store(), handover_id, data))
    run_id = data.get("run_id")
    if run_id:
        return jsonify(issue_for_run(store(), run_id, data))
    raise DomainIssue("HANDOVER_ID_REQUIRED")


@api.get("/api/examples/<profile>")
def example(profile):
    if profile not in {"clean", "dirty"}:
        abort(404)
    return send_file(SAMPLES / f"{profile}.csv", as_attachment=True)
