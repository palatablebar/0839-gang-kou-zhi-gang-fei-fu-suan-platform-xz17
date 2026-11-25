from app.catalog import SPEC
from app.domain.calculate import calculate_rows
from app.domain.decision import decide_rows
from app.domain.prepare import prepare_rows
from app.intake.service import load

CLEAN = [{'container_id': 'C1',
  'gate_in': '2026-11-02',
  'gate_out': '2026-11-10',
  'free_days': '3',
  'invoice_cents': '52000'},
 {'container_id': 'C2',
  'gate_in': '2026-11-06',
  'gate_out': '2026-11-11',
  'free_days': '1',
  'invoice_cents': '16000'},
 {'container_id': 'C3',
  'gate_in': '2026-09-29',
  'gate_out': '2026-10-12',
  'free_days': '3',
  'invoice_cents': '24000'},
 {'container_id': 'C4',
  'gate_in': '2026-11-02',
  'gate_out': '2026-11-10',
  'free_days': '30',
  'invoice_cents': '0'}]


def row(**changes):
    item = dict(CLEAN[0])
    item.update(changes)
    return item


def calc(rows):
    items, issues = prepare_rows(rows)
    return calculate_rows(items), issues


def run(rows):
    items, issues = prepare_rows(rows)
    return decide_rows(calculate_rows(items)), issues


def run_profile(profile):
    return run(load(profile)["rows"])
