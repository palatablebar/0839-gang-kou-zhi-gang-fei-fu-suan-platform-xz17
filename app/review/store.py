import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS runs(
              id TEXT PRIMARY KEY,
              request_key TEXT UNIQUE NOT NULL,
              created_at TEXT NOT NULL,
              profile TEXT NOT NULL,
              status TEXT NOT NULL,
              revision INTEGER NOT NULL,
              payload TEXT NOT NULL,
              comment TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS handovers(
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              container_id TEXT NOT NULL,
              request_key TEXT UNIQUE NOT NULL,
              status TEXT NOT NULL,
              revision INTEGER NOT NULL DEFAULT 0,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS handover_day_charges(
              id TEXT PRIMARY KEY,
              handover_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              container_id TEXT NOT NULL,
              charge_date TEXT NOT NULL,
              owner_role TEXT NOT NULL,
              amount_cents INTEGER NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(run_id, container_id, charge_date)
            );
            CREATE INDEX IF NOT EXISTS idx_handover_charges_lookup
              ON handover_day_charges(run_id, container_id, charge_date);
            ''')

    def find_by_request(self, request_key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE request_key=?", (request_key,)).fetchone()
        return dict(row) if row else None

    def create(self, request_key, profile, payload):
        existing = self.find_by_request(request_key)
        if existing:
            return existing
        run_id = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute("INSERT INTO runs(id,request_key,created_at,profile,status,revision,payload) VALUES(?,?,?,?,?,?,?)",
                       (run_id, request_key, datetime.now(timezone.utc).isoformat(), profile, "DRAFT", 0,
                        json.dumps(payload, ensure_ascii=False)))
        return self.get(run_id)

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def review(self, run_id, decision, comment, expected_revision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None, "missing"
            if row["revision"] != expected_revision:
                return None, "stale"
            db.execute("UPDATE runs SET status=?, revision=revision+1, comment=? WHERE id=?",
                       (decision, comment, run_id))
        return self.get(run_id), None

    def find_handover_by_request(self, request_key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM handovers WHERE request_key=?", (request_key,)).fetchone()
        return dict(row) if row else None

    def get_handover(self, handover_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM handovers WHERE id=?", (handover_id,)).fetchone()
        return dict(row) if row else None

    def list_handovers(self, run_id=None, container_id=None):
        sql = "SELECT * FROM handovers WHERE 1=1"
        params = []
        if run_id:
            sql += " AND run_id=?"
            params.append(run_id)
        if container_id:
            sql += " AND container_id=?"
            params.append(container_id)
        sql += " ORDER BY created_at, id"
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def create_handover(self, handover_id, request_key, run_id, container_id, status, payload):
        existing = self.find_handover_by_request(request_key)
        if existing:
            return existing
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                """INSERT INTO handovers
                   (id,run_id,container_id,request_key,status,revision,payload,created_at,updated_at)
                   VALUES(?,?,?,?,?,0,?,?,?)""",
                (handover_id, run_id, container_id, request_key, status,
                 json.dumps(payload, ensure_ascii=False), now, now),
            )
        return self.get_handover(handover_id)

    def save_handover(self, handover_id, status, payload, increment_revision=False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM handovers WHERE id=?", (handover_id,)).fetchone()
            if row is None:
                return None
            revision_sql = ", revision=revision+1" if increment_revision else ""
            db.execute(
                f"""UPDATE handovers
                    SET status=?, payload=?, updated_at=?{revision_sql}
                    WHERE id=?""",
                (status, json.dumps(payload, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(), handover_id),
            )
        return self.get_handover(handover_id)

    def list_day_charges(self, run_id=None, container_id=None, handover_id=None):
        sql = "SELECT * FROM handover_day_charges WHERE 1=1"
        params = []
        if run_id:
            sql += " AND run_id=?"
            params.append(run_id)
        if container_id:
            sql += " AND container_id=?"
            params.append(container_id)
        if handover_id:
            sql += " AND handover_id=?"
            params.append(handover_id)
        sql += " ORDER BY charge_date, id"
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def insert_day_charge(self, charge_id, handover_id, run_id, container_id, charge_date,
                          owner_role, amount_cents, payload):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO handover_day_charges
                   (id,handover_id,run_id,container_id,charge_date,owner_role,
                    amount_cents,payload,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (charge_id, handover_id, run_id, container_id, charge_date, owner_role,
                 int(amount_cents), json.dumps(payload, ensure_ascii=False), now),
            )
            row = db.execute(
                "SELECT * FROM handover_day_charges WHERE run_id=? AND container_id=? AND charge_date=?",
                (run_id, container_id, charge_date),
            ).fetchone()
        return dict(row) if row else None
