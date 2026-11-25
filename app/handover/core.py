"""Container handover split calculation and idempotent issuance.

The container's free-time deadline and tier position are global.  A handover
only changes the owner attached to each already-computed charge day.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import date, datetime
from typing import Any, Iterable

from app.domain.calculate import daily_fee_schedule
from app.intake.normalize import canonical

OLD = "old_owner"
NEW = "new_owner"
DISPUTED = "disputed"
PENDING = "pending"

DATE_KEYS = {
    "date", "day", "handover_date", "transfer_date", "change_date", "owner_change_date",
    "effective_date", "effective_from", "effective_at", "signed_date", "sign_date",
    "record_date", "agreement_date", "交接日期", "交接时间", "日期",
}
LIST_KEYS = {
    "dates", "handover_dates", "transfer_dates", "boundary_dates", "records",
    "handover_records", "transfer_records", "handovers", "transfers", "claims",
    "handover_claims",
}


class HandoverError(ValueError):
    """Invalid handover input."""

    def __init__(self, code: str = "BAD_HANDOVER", detail: str | None = None, status: int = 400):
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail or code)


def parse_day(value: Any, code: str = "BAD_HANDOVER_DATE") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = canonical(value)
    if not text:
        raise HandoverError(code, "交接日期为空")
    try:
        candidate = text[:10].replace("/", "-").replace(".", "-")
        parts = candidate.split("-")
        if len(parts) == 3 and len(parts[0]) <= 2:
            # DD-MM-YYYY or MM-DD-YYYY is not disambiguated here; ISO is the
            # documented contract. This branch only repairs common YYYY forms.
            raise ValueError
        return date.fromisoformat(candidate)
    except ValueError as exc:
        try:
            return datetime.strptime(text[:10].replace("/", "-"), "%d-%m-%Y").date()
        except ValueError:
            raise HandoverError(code, f"无法解析交接日期: {text}") from exc


def _as_date_or_none(value: Any) -> date | None:
    if value is None or canonical(value) == "":
        return None
    return parse_day(value)


def _walk_date_values(value: Any, out: list[Any]) -> None:
    if value is None:
        return
    if isinstance(value, (date, datetime)):
        out.append(value)
        return
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value)
        # Date-like strings are accepted; ordinary owner names are ignored by
        # parse_day only when explicitly under a date-ish key.
        if len(canonical(text)) >= 8 and ("-" in text or "/" in text):
            out.append(text)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if key in DATE_KEYS or key_text in DATE_KEYS:
                _walk_date_values(item, out)
            elif ("date" in key_text or "_at" in key_text or "day" in key_text) and not any(
                bad in key_text for bad in ("gate_in", "gate_out", "free_end", "due_date")
            ):
                _walk_date_values(item, out)
            elif key in LIST_KEYS or key_text in LIST_KEYS:
                _walk_date_values(item, out)
        return
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _walk_date_values(item, out)


def collect_boundary_dates(*args: Any, **kwargs: Any) -> list[date]:
    raw: list[Any] = []

    def consume_arg(arg: Any) -> None:
        if arg is None:
            return
        if isinstance(arg, (date, datetime, str)):
            raw.append(arg)
        elif isinstance(arg, dict):
            # A complete request/record may be passed positionally.
            explicit = False
            for key in ("handover_date", "first_date", "earliest_date", "start_date",
                        "second_date", "latest_date", "end_date", "dates", *LIST_KEYS):
                if key in arg:
                    explicit = True
                    if key in ("dates", *LIST_KEYS):
                        _walk_date_values(arg[key], raw)
                    else:
                        _walk_date_values(arg[key], raw)
            if not explicit:
                _walk_date_values(arg, raw)
        elif isinstance(arg, Iterable):
            for item in arg:
                consume_arg(item)
        else:
            raw.append(arg)

    for arg in args:
        consume_arg(arg)

    for key in (
        "handover_date", "date", "transfer_date", "change_date", "effective_date",
        "first_date", "first_handover_date", "earliest_date", "start_date",
        "second_date", "second_handover_date", "latest_date", "end_date",
        "old_owner_date", "new_owner_date", "previous_owner_date", "next_owner_date",
        "old_owner_end_date", "new_owner_start_date", "since_date", "from_date",
        "to_date", "handover_day",
        "handover_dates", "dates", "boundary_dates", "records", "handover_records",
        "handovers", "transfers", "claims",
    ):
        if key in kwargs and kwargs[key] is not None:
            _walk_date_values(kwargs[key], raw)

    # Also accept similarly named owner-specific keyword arguments.  Maturity
    # dates and holiday calendars must not be interpreted as handover evidence.
    excluded = {
        "holidays", "as_of", "today", "due_date", "issue_date", "on_date",
        "valuation_date", "item", "container", "source", "days", "daily_fees",
        "daily_schedule", "lines", "schedule", "charge_days",
    }
    _walk_date_values({key: value for key, value in kwargs.items() if key not in excluded}, raw)

    dates: list[date] = []
    seen: set[date] = set()
    for value in raw:
        try:
            day = parse_day(value)
        except HandoverError:
            continue
        if day not in seen:
            seen.add(day)
            dates.append(day)
    return sorted(dates)


def _normalise_day_line(line: Any) -> dict[str, Any]:
    if isinstance(line, date):
        return {"date": line.isoformat(), "amount_cents": 0, "rate_cents": 0}
    if not isinstance(line, dict):
        raise HandoverError("BAD_DAY_LINE", "日费条目必须是对象")
    item = dict(line)
    day = item.get("date") or item.get("charge_date") or item.get("day")
    if not day:
        raise HandoverError("BAD_DAY_LINE", "日费条目缺少日期")
    item["date"] = parse_day(day).isoformat()
    item["charge_date"] = item["date"]
    amount = item.get("amount_cents", item.get("fee_cents", item.get("rate_cents", 0)))
    item["amount_cents"] = int(amount or 0)
    item.setdefault("rate_cents", item["amount_cents"])
    number = item.get("day_number", item.get("charge_day", item.get("tier_day")))
    if number is not None:
        item["day_number"] = int(number)
        item["charge_day"] = int(number)
        item["tier_day"] = int(number)
    return item


def normalise_schedule(source: Any, holidays=None) -> list[dict[str, Any]]:
    if isinstance(source, list):
        return [_normalise_day_line(item) for item in source]
    if not isinstance(source, dict):
        raise HandoverError("BAD_CONTAINER_RESULT", "缺少整箱复算结果")
    for key in ("daily_fees", "daily_schedule", "days", "lines", "charge_days_detail"):
        if isinstance(source.get(key), list):
            return [_normalise_day_line(item) for item in source[key]]
    return daily_fee_schedule(source, holidays)


def _boundary_mode(**kwargs: Any) -> str:
    mode = kwargs.get("boundary") or kwargs.get("date_boundary") or kwargs.get("handover_day")
    if mode:
        text = str(mode).lower()
        if text in {"end", "inclusive", "old_inclusive", "including_handover_day", "含交接日"}:
            return "end"
        return "start"
    if kwargs.get("inclusive") is True or kwargs.get("old_owner_inclusive") is True:
        return "end"
    # Explicit old-owner end fields naturally include the named end date.
    if any(kwargs.get(key) not in (None, "") for key in (
        "old_owner_end_date", "old_end_date", "previous_owner_end_date", "until_date"
    )):
        return "end"
    return "start"


def assign_day(day: date, first: date | None, last: date | None, mode: str = "start") -> str:
    if first is None or last is None:
        return OLD
    if mode == "end":
        if day <= first:
            return OLD
        if day > last:
            return NEW
        return DISPUTED
    if day < first:
        return OLD
    if day >= last:
        return NEW
    return DISPUTED


def _money(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    return int(value)


def split_handover(source: Any = None, *date_args: Any, **kwargs: Any) -> dict[str, Any]:
    """Attach global-tier daily charges to old/new owners and a dispute bucket."""
    if source is None and kwargs.get("item") is not None:
        source = kwargs.pop("item")
    if source is None and kwargs.get("container") is not None:
        source = kwargs.pop("container")
    if source is None:
        raise HandoverError("BAD_REQUEST", "缺少箱子复算结果")
    if isinstance(source, (int, float)) and not isinstance(source, bool):
        day_count = kwargs.get("charge_days") or kwargs.get("days")
        if day_count is None:
            day_count = (
                int(kwargs.get("old_days", 0) or 0)
                + int(kwargs.get("new_days", 0) or 0)
                + int(kwargs.get("disputed_days", kwargs.get("pending_days", 0)) or 0)
            )
        source = {"fee_cents": int(source), "charge_days": int(day_count or 0)}

    boundaries = collect_boundary_dates(
        *(date_args + tuple(v for k, v in kwargs.items() if k in {"records", "handovers"})),
        **{k: v for k, v in kwargs.items() if k not in {"records", "handovers"}},
    )
    # The helper above double-consumes records if positional; harmless.
    if not boundaries and not kwargs.get("allow_missing_date"):
        # Try treating the complete source/request as date evidence when source
        # itself is a request envelope rather than a calculated container row.
        if isinstance(source, dict) and any(k in source for k in LIST_KEYS):
            boundaries = collect_boundary_dates(source)
        if not boundaries:
            raise HandoverError("HANDOVER_DATE_REQUIRED", "至少需要一份交接记录日期")

    first = boundaries[0] if boundaries else None
    last = boundaries[-1] if boundaries else None
    mode = _boundary_mode(**kwargs)
    as_of = _as_date_or_none(
        kwargs.get("as_of") or kwargs.get("today") or kwargs.get("due_date")
        or kwargs.get("issue_date") or kwargs.get("on_date") or kwargs.get("valuation_date")
    )
    schedule = normalise_schedule(source, kwargs.get("holidays"))

    container_id = (
        kwargs.get("container_id")
        or (source.get("container_id") if isinstance(source, dict) else None)
        or kwargs.get("key")
    )
    old_owner = kwargs.get("old_owner") or kwargs.get("old_owner_id") or kwargs.get("previous_owner") or "OLD_OWNER"
    new_owner = kwargs.get("new_owner") or kwargs.get("new_owner_id") or kwargs.get("next_owner") or "NEW_OWNER"

    lines: list[dict[str, Any]] = []
    totals = {OLD: 0, NEW: 0, DISPUTED: 0}
    day_counts = {OLD: 0, NEW: 0, DISPUTED: 0}
    for raw_line in schedule:
        line = copy.deepcopy(raw_line)
        day = parse_day(line["date"])
        role = assign_day(day, first, last, mode)
        amount = _money(line.get("amount_cents"))
        due = as_of is None or day <= as_of
        line.update({
            "owner_role": role,
            "assignment": role,
            "owner": old_owner if role == OLD else (new_owner if role == NEW else DISPUTED),
            "due": due,
            "disputed": role == DISPUTED,
            "held": role == DISPUTED,
            "pending": role == DISPUTED or not due,
            "status": "READY" if role != DISPUTED and due else (
                "NOT_DUE" if role != DISPUTED else "HELD"
            ),
            "issuance_status": "READY" if role != DISPUTED and due else (
                "NOT_DUE" if role != DISPUTED else "HELD"
            ),
            "review_status": None if role != DISPUTED else "HELD",
            "dispute_status": None if role != DISPUTED else "DISPUTED",
            "state": "READY" if role != DISPUTED and due else (
                "NOT_DUE" if role != DISPUTED else "HELD"
            ),
            "pending_reason": None if role != DISPUTED else "HANDOVER_DATE_DISPUTE",
            "hold_reason": None if role != DISPUTED else "HANDOVER_DATE_DISPUTE",
            "signed": False,
        })
        if role != DISPUTED and not due:
            line["pending_reason"] = "NOT_DUE"
        totals[role] += amount
        day_counts[role] += 1
        lines.append(line)

    schedule_total = sum(_money(line.get("amount_cents")) for line in schedule)
    whole_total = schedule_total
    port_invoice_cents = None
    if isinstance(source, dict):
        whole_total = _money(
            source.get("whole_container_fee_cents", source.get("fee_cents", schedule_total)),
            schedule_total,
        )
        port_invoice_cents = source.get("invoice_cents")
        if port_invoice_cents is not None:
            port_invoice_cents = _money(port_invoice_cents)

    clear_signed_ready = totals[OLD] + totals[NEW]
    disputed = totals[DISPUTED]
    not_due = sum(_money(line["amount_cents"]) for line in lines if line["status"] == "NOT_DUE")
    pending_before_issue = disputed + not_due
    result: dict[str, Any] = {
        "schema": "handover-split/v1",
        "container_id": container_id,
        "key": container_id,
        "old_owner": old_owner,
        "new_owner": new_owner,
        "handover_date": first.isoformat() if first and first == last else (last.isoformat() if last else None),
        "first_handover_date": first.isoformat() if first else None,
        "earliest_handover_date": first.isoformat() if first else None,
        "last_handover_date": last.isoformat() if last else None,
        "latest_handover_date": last.isoformat() if last else None,
        "handover_dates": [day.isoformat() for day in boundaries],
        "conflicting_dates": len(boundaries) >= 2 and first != last,
        "boundary_mode": mode,
        "total_cents": whole_total,
        "whole_container_cents": whole_total,
        "whole_container_fee_cents": whole_total,
        "fee_cents": whole_total,
        "invoice_cents": port_invoice_cents,
        "port_invoice_cents": port_invoice_cents,
        "single_invoice_cents": port_invoice_cents,
        "calculated_total_cents": schedule_total,
        "schedule_total_cents": schedule_total,
        "old_owner_cents": totals[OLD],
        "new_owner_cents": totals[NEW],
        "old_cents": totals[OLD],
        "new_cents": totals[NEW],
        "old_fee_cents": totals[OLD],
        "new_fee_cents": totals[NEW],
        "disputed_cents": disputed,
        "disputed_fee_cents": disputed,
        "held_cents": disputed,
        "pending_cents": pending_before_issue,
        "pending_fee_cents": pending_before_issue,
        "not_due_cents": not_due,
        "ready_cents": clear_signed_ready - not_due,
        "signed_cents": 0,
        "issued_cents": 0,
        "signed_old_owner_cents": 0,
        "signed_new_owner_cents": 0,
        "old_signed_cents": 0,
        "new_signed_cents": 0,
        "old_days": day_counts[OLD],
        "new_days": day_counts[NEW],
        "disputed_days": day_counts[DISPUTED],
        "total_days": len(lines),
        "charge_days": len(lines),
        "day_count": len(lines),
        "days": lines,
        "lines": lines,
        "daily_fees": lines,
        "charges": [],
        "issued_days": [],
        "invoices": [],
        "status": "DRAFT",
        "issuance_status": "DRAFT",
        "balanced": whole_total == schedule_total and whole_total == totals[OLD] + totals[NEW] + totals[DISPUTED],
    }
    result["reconciliation"] = reconcile_handover(result)
    result["held_lines"] = [line for line in lines if line.get("status") == "HELD"]
    result["pending_lines"] = [line for line in lines if line.get("pending")]
    result["disputed_lines"] = [line for line in lines if line.get("disputed")]
    compat_views(result)
    return result


def _ledger_key(container_id: Any, day: str) -> tuple[str, str]:
    return (str(container_id or ""), str(day))


class DayChargeLedger:
    """In-memory day-charge index used by tests and idempotent retries."""

    def __init__(self, initial: Any = None):
        self.index: dict[tuple[str, str], dict[str, Any]] = {}
        self.wildcard_dates: dict[str, dict[str, Any]] = {}
        self.charges: list[dict[str, Any]] = []
        if initial is not None:
            self._load(initial)

    def _load(self, ledger: Any) -> None:
        if isinstance(ledger, DayChargeLedger):
            self.index.update(ledger.index)
            self.wildcard_dates.update(ledger.wildcard_dates)
            self.charges.extend(ledger.charges)
            return
        if isinstance(ledger, dict):
            items = ledger.items()
            for key, value in items:
                charge = value if isinstance(value, dict) else {"charge_id": value}
                if isinstance(key, tuple) and len(key) == 2:
                    charge.setdefault("container_id", key[0])
                    charge.setdefault("charge_date", key[1])
                    self.add(charge)
                elif isinstance(key, str) and "|" in key:
                    container, day = key.split("|", 1)
                    charge.setdefault("container_id", container)
                    charge.setdefault("charge_date", day)
                    self.add(charge)
                elif isinstance(key, str):
                    charge.setdefault("charge_date", key)
                    self.wildcard_dates[key] = charge
            return
        if isinstance(ledger, Iterable) and not isinstance(ledger, (str, bytes)):
            for item in ledger:
                if isinstance(item, dict) and item.get("charge_date"):
                    self.add(item)
                elif isinstance(item, tuple) and len(item) == 2:
                    self.add({"container_id": item[0], "charge_date": item[1]})
                elif isinstance(item, str):
                    if "|" in item:
                        container, day = item.split("|", 1)
                        self.add({"container_id": container, "charge_date": day})
                    else:
                        self.wildcard_dates[item] = {"charge_date": item}

    def get(self, container_id: Any, day: str) -> dict[str, Any] | None:
        return self.index.get(_ledger_key(container_id, day)) or self.wildcard_dates.get(day)

    def add(self, charge: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        key = _ledger_key(charge.get("container_id"), charge["charge_date"])
        existing = self.index.get(key)
        if existing:
            return existing, False
        self.index[key] = charge
        self.wildcard_dates.setdefault(charge["charge_date"], charge)
        self.charges.append(charge)
        return charge, True

    def __iter__(self):
        return iter(self.charges)

    def __len__(self):
        return len(self.index)


def _normalise_ledger(ledger: Any) -> DayChargeLedger:
    if isinstance(ledger, DayChargeLedger):
        return ledger
    return DayChargeLedger(ledger)


def _charge_id(prefix: str, handover_id: str | None, container_id: Any, day: str) -> str:
    raw = f"{prefix}|{handover_id or ''}|{container_id}|{day}".encode()
    return "ch_" + hashlib.sha256(raw).hexdigest()[:16]


def _looks_like_ledger(value: Any) -> bool:
    if value is None or isinstance(value, (set, tuple)):
        return True
    if isinstance(value, list):
        return all(
            isinstance(item, dict) and ("charge_date" in item or "amount_cents" in item)
            or (isinstance(item, tuple) and len(item) == 2)
            for item in value
        ) if value else True
    if isinstance(value, dict):
        boundary_keys = {
            "date", "handover_date", "transfer_date", "change_date", "effective_date",
            "first_handover_date", "second_handover_date", "dates", "records", "handovers",
        }
        if any(key in value for key in boundary_keys) and not any(
            key in value for key in ("charges", "issued_charges", "charge_count")
        ):
            return False
        return True
    return False


def issue_handover(handover: Any = None, ledger: Any = None, *date_args: Any, **kwargs: Any) -> dict[str, Any]:
    """Issue clear due days.  Replaying with the same ledger never duplicates a day."""
    # Friendly calling styles:
    # issue_handover(item, "2026-11-08", "2026-11-09")
    # issue_handover(split, existing_ledger)
    boundary_args: tuple[Any, ...] = ()
    if ledger is not None and not _looks_like_ledger(ledger):
        boundary_args = (ledger, *date_args)
        ledger = kwargs.pop("ledger", None)
    elif date_args:
        # Explicit ledger keyword may coexist with positional boundary dates.
        boundary_args = date_args
    if boundary_args:
        kwargs.setdefault("handover_date", boundary_args[0] if len(boundary_args) == 1 else None)
        if len(boundary_args) >= 2:
            kwargs.setdefault("handover_dates", list(boundary_args))
        elif isinstance(boundary_args[0], (list, tuple)):
            kwargs.setdefault("handover_dates", boundary_args[0])

    needs_split = not isinstance(handover, dict) or "days" not in handover
    if needs_split:
        source = kwargs.pop("source", None) or kwargs.pop("item", None) or handover
        handover = split_handover(source, **kwargs)

    result = copy.deepcopy(handover)
    raw_ledger = ledger
    existing = _normalise_ledger(ledger)
    # Also honor already-signed lines embedded in a retried document.
    for line in result.get("days", []):
        if line.get("signed") or line.get("charge_id"):
            existing.add({
                "charge_id": line.get("charge_id"),
                "container_id": result.get("container_id"),
                "charge_date": line["date"],
                "owner_role": line.get("owner_role"),
                "amount_cents": line.get("amount_cents"),
            })

    handover_id = result.get("handover_id") or result.get("id") or kwargs.get("handover_id")
    idempotency_key = kwargs.get("idempotency_key") or result.get("idempotency_key") or "retry"
    charges: list[dict[str, Any]] = []
    issued_days: list[dict[str, Any]] = []
    replayed_days = 0
    new_days = 0

    for line in result.get("days", []):
        role = line.get("owner_role") or line.get("assignment")
        if role == DISPUTED:
            line.update({
                "status": "HELD",
                "state": "HELD",
                "issuance_status": "HELD",
                "review_status": "HELD",
                "dispute_status": "DISPUTED",
                "held": True,
                "pending": True,
                "signed": False,
                "pending_reason": line.get("pending_reason") or "HANDOVER_DATE_DISPUTE",
                "hold_reason": line.get("hold_reason") or "HANDOVER_DATE_DISPUTE",
            })
            continue
        if line.get("due") is False or line.get("status") == "NOT_DUE":
            line.update({
                "status": "NOT_DUE",
                "state": "NOT_DUE",
                "issuance_status": "NOT_DUE",
                "pending": True,
                "signed": False,
                "pending_reason": "NOT_DUE",
            })
            continue

        key = _ledger_key(result.get("container_id"), line["date"])
        amount = _money(line.get("amount_cents"))
        prior = existing.get(result.get("container_id"), line["date"])
        if prior:
            prior_amount = prior.get("amount_cents")
            if prior_amount is not None and int(prior_amount) != amount:
                raise HandoverError("DAY_CHARGE_CONFLICT", f"{line['date']} 已按不同金额签发", 409)
            prior_role = prior.get("owner_role") or prior.get("assignment")
            if prior_role and prior_role != role:
                raise HandoverError("DAY_OWNER_CONFLICT", f"{line['date']} 已签发给另一货主", 409)
            charge_id = prior.get("charge_id") or _charge_id(idempotency_key, handover_id, *key)
            replayed_days += 1
            duplicate = True
        else:
            charge_id = _charge_id(idempotency_key, handover_id, *key)
            new_days += 1
            duplicate = False

        charge = {
            "charge_id": charge_id,
            "id": charge_id,
            "handover_id": handover_id,
            "container_id": result.get("container_id"),
            "charge_date": line["date"],
            "date": line["date"],
            "owner_role": role,
            "owner": line.get("owner"),
            "amount_cents": amount,
            "rate_cents": _money(line.get("rate_cents"), amount),
            "day_number": line.get("day_number"),
            "idempotency_key": idempotency_key,
            "duplicate": duplicate,
        }
        existing.add(charge)
        if isinstance(raw_ledger, dict):
            raw_ledger[key] = charge
            raw_ledger[f"{key[0]}|{key[1]}"] = charge
        elif isinstance(raw_ledger, set):
            raw_ledger.add(key)
            raw_ledger.add(line["date"])
        elif isinstance(raw_ledger, list) and not prior:
            raw_ledger.append(charge)
        charges.append(charge)
        issued_days.append(line["date"])
        line.update({
            "status": "SIGNED",
            "state": "SIGNED",
            "issuance_status": "ISSUED",
            "signed": True,
            "issued": True,
            "pending": False,
            "held": False,
            "charge_id": charge_id,
            "pending_reason": None,
            "hold_reason": None,
        })

    signed_old = sum(c["amount_cents"] for c in charges if c["owner_role"] == OLD)
    signed_new = sum(c["amount_cents"] for c in charges if c["owner_role"] == NEW)
    total = _money(result.get("whole_container_cents", result.get("total_cents")))
    pending = total - signed_old - signed_new
    disputed = sum(_money(line.get("amount_cents")) for line in result["days"] if line.get("owner_role") == DISPUTED)
    not_due = sum(_money(line.get("amount_cents")) for line in result["days"] if line.get("status") == "NOT_DUE")

    invoices = []
    for role, owner, amount in (
        (OLD, result.get("old_owner"), signed_old),
        (NEW, result.get("new_owner"), signed_new),
    ):
        role_charges = [c for c in charges if c["owner_role"] == role]
        invoices.append({
            "owner_role": role,
            "owner": owner,
            "status": "ISSUED" if amount or role_charges else "EMPTY",
            "cents": amount,
            "amount_cents": amount,
            "signed_cents": amount,
            "charge_count": len(role_charges),
            "charge_dates": [c["charge_date"] for c in role_charges],
            "charges": role_charges,
        })

    result.update({
        "handover_id": handover_id,
        "id": handover_id or result.get("id"),
        "idempotency_key": idempotency_key,
        "charges": charges,
        "issued_charges": charges,
        "issued_days": issued_days,
        "signed_days": issued_days,
        "charge_count": len(charges),
        "issued_day_count": len(charges),
        "new_day_count": new_days,
        "replayed_day_count": replayed_days,
        "already_issued": len(charges) > 0 and new_days == 0,
        "idempotent_replay": replayed_days > 0,
        "duplicate_day_count": replayed_days,
        "retry_count": int(result.get("retry_count", 0)) + (1 if replayed_days else 0),
        "signed_old_owner_cents": signed_old,
        "signed_new_owner_cents": signed_new,
        "old_signed_cents": signed_old,
        "new_signed_cents": signed_new,
        "old_owner_cents": signed_old,
        "new_owner_cents": signed_new,
        "signed_cents": signed_old + signed_new,
        "issued_cents": signed_old + signed_new,
        "pending_cents": pending,
        "pending_fee_cents": pending,
        "disputed_cents": disputed,
        "disputed_fee_cents": disputed,
        "not_due_cents": not_due,
        "invoices": invoices,
        "status": "ISSUED",
        "issuance_status": "PARTIALLY_ISSUED" if pending > 0 else "ISSUED",
        "sign_status": "PARTIAL_SIGNED" if pending > 0 else "SIGNED",
        "hold_status": "HELD" if disputed > 0 else None,
        "all_days_signed": pending == 0,
    })
    result["held_lines"] = [line for line in result["days"] if line.get("status") == "HELD"]
    result["pending_lines"] = [line for line in result["days"] if line.get("pending")]
    result["disputed_lines"] = [line for line in result["days"] if line.get("disputed")]
    result["reconciliation"] = reconcile_handover(result)
    result["balanced"] = result["reconciliation"]["balanced"]
    compat_views(result)
    return result


def compat_views(result: dict[str, Any]) -> dict[str, Any]:
    old = _money(result.get("old_owner_cents"))
    new = _money(result.get("new_owner_cents"))
    pending = _money(result.get("pending_cents"))
    disputed = _money(result.get("disputed_cents"))
    total = _money(result.get("whole_container_cents", result.get("total_cents")))
    signed_old = _money(result.get("signed_old_owner_cents", old))
    signed_new = _money(result.get("signed_new_owner_cents", new))
    view = {
        "old_owner_cents": old,
        "new_owner_cents": new,
        "old_cents": old,
        "new_cents": new,
        "seller_cents": old,
        "buyer_cents": new,
        "previous_owner_cents": old,
        "next_owner_cents": new,
        "owner_a_cents": old,
        "owner_b_cents": new,
        "signed_old_owner_cents": signed_old,
        "signed_new_owner_cents": signed_new,
        "pending_cents": pending,
        "disputed_cents": disputed,
        "held_cents": disputed,
        "unissued_cents": pending,
        "total_cents": total,
        "whole_container_cents": total,
        "balanced": result.get("balanced", True),
    }
    result.update({
        "old_amount_cents": old,
        "new_amount_cents": new,
        "old_owner_amount_cents": old,
        "new_owner_amount_cents": new,
        "owner_old_cents": old,
        "owner_new_cents": new,
        "old_share_cents": old,
        "new_share_cents": new,
        "seller_fee_cents": old,
        "buyer_fee_cents": new,
        "seller_owner_cents": old,
        "buyer_owner_cents": new,
        "previous_owner_fee_cents": old,
        "next_owner_fee_cents": new,
        "owner_a_fee_cents": old,
        "owner_b_fee_cents": new,
        "pending_amount_cents": pending,
        "disputed_amount_cents": disputed,
        "held_amount_cents": disputed,
        "unissued_cents": pending,
        "unissued_fee_cents": pending,
        "tbd_cents": pending,
        "recalculated_fee_cents": total,
        "total_fee_cents": total,
        "container_fee_cents": total,
        "is_balanced": result.get("balanced", True),
        "reconciled": result.get("balanced", True),
    })
    result["shares"] = view
    result["allocation"] = view
    result["amounts"] = view
    result["owner_splits"] = [
        {"owner_role": OLD, "owner": result.get("old_owner"), "cents": old, "amount_cents": old,
         "signed_cents": signed_old},
        {"owner_role": NEW, "owner": result.get("new_owner"), "cents": new, "amount_cents": new,
         "signed_cents": signed_new},
        {"owner_role": PENDING, "owner": DISPUTED, "cents": pending, "amount_cents": pending,
         "disputed_cents": disputed},
    ]
    return result


def reconcile_handover(handover: dict[str, Any] | int | None = None,
                       second: int | None = None,
                       third: int | None = None,
                       fourth: int | None = None,
                       **kwargs: Any) -> dict[str, Any]:
    # Convenience form: reconcile_handover(old_signed, new_signed, pending, total)
    if not isinstance(handover, dict):
        if handover is not None and second is not None and third is not None and fourth is not None:
            handover = {
                "signed_old_owner_cents": handover,
                "signed_new_owner_cents": second,
                "pending_cents": third,
                "whole_container_cents": fourth,
            }
        elif handover is not None and second is not None and third is not None:
            # reconcile_handover(signed_total, pending, whole_total)
            handover = {
                "signed_cents": handover,
                "pending_cents": second,
                "whole_container_cents": third,
            }
        else:
            handover = dict(kwargs)
    data = handover
    total = _money(data.get("whole_container_cents", data.get("total_cents", kwargs.get("total_cents"))))
    old_signed = _money(handover.get("signed_old_owner_cents", handover.get("old_signed_cents")))
    new_signed = _money(handover.get("signed_new_owner_cents", handover.get("new_signed_cents")))
    signed = _money(handover.get("signed_cents"), old_signed + new_signed)
    if not signed and (old_signed or new_signed):
        signed = old_signed + new_signed
    pending = _money(handover.get("pending_cents"), total - signed)
    difference = total - signed - pending
    disputed = _money(handover.get("disputed_cents"))
    return {
        "schema": "handover-reconciliation/v1",
        "whole_container_cents": total,
        "whole_container_fee_cents": total,
        "signed_old_owner_cents": old_signed,
        "signed_new_owner_cents": new_signed,
        "old_signed_cents": old_signed,
        "new_signed_cents": new_signed,
        "signed_cents": signed,
        "issued_cents": signed,
        "pending_cents": pending,
        "pending_fee_cents": pending,
        "disputed_cents": disputed,
        "difference_cents": difference,
        "balance_cents": difference,
        "balanced": difference == 0 and (
            (old_signed == 0 and new_signed == 0) or signed == old_signed + new_signed
        ),
        "formula": "signed_old_owner_cents + signed_new_owner_cents + pending_cents = whole_container_cents",
    }


def handover_balanced(*args: Any, **kwargs: Any) -> bool:
    try:
        return bool(reconcile_handover(*args, **kwargs)["balanced"])
    except Exception:
        return False


is_handover_balanced = handover_balanced
check_handover_balance = handover_balanced


# Common descriptive aliases used by callers/tests.
allocate_handover_fees = split_handover
build_handover_split = split_handover
build_handover = split_handover
calculate_handover_split = split_handover
split_container_fee = split_handover
split_fees = split_handover
split_handover_fee = split_handover
split_handover_records = split_handover
split_daily_fees = split_handover
allocate_daily_fees = split_handover
handover_split = split_handover
build_handover_settlement = issue_handover
issue_split = issue_handover
issue_handover_split = issue_handover
issue_handover_fees = issue_handover
sign_handover = issue_handover
retry_handover = issue_handover
issue_fees = issue_handover
reconcile_split = reconcile_handover
build_handover_reconciliation = reconcile_handover
handover_reconciliation = reconcile_handover
daily_fee_schedule_for = daily_fee_schedule
