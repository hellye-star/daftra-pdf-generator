"""
Vista Client Proposal Portal — internal SQLite side (Phase 1).

Additive only. Lives in the SAME database file as tp_db.py
(~/.vista-platform/technical-proposals/technical-proposals.db) but every
object is prefixed `portal_` and nothing here reads or writes the existing
`projects` / `photos` / `settings` / `review_drafts` tables.

Phase 1 tables:
  portal_links        — maps a Vista project.id to its cloud proposal / client
  portal_inbound      — reserved for Phase 4 (client change-requests to action)
  portal_audit_mirror — local copy of the cloud audit log (offline history + backups)
"""
import datetime
import json
import os
import sqlite3

import tp_db  # reuse the same data dir / db path / connection convention

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_links (
  internal_project_id     TEXT PRIMARY KEY,
  cloud_proposal_id       TEXT,
  cloud_client_id         TEXT,
  portal_status           TEXT,           -- 'published' | 'closed'
  last_published_revision TEXT,
  last_published_at       TEXT,
  last_pulled_at          TEXT,
  data_json               TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS portal_inbound (
  id                  TEXT PRIMARY KEY,
  cloud_id            TEXT,
  kind                TEXT,               -- 'change_request' | 'comment' | 'approval'
  internal_project_id TEXT,
  item_ref            TEXT,
  field_token         TEXT,
  old_value_json      TEXT,
  new_value_json      TEXT,
  payload_json        TEXT NOT NULL DEFAULT '{}',
  state               TEXT NOT NULL DEFAULT 'pending',
  pulled_at           TEXT,
  decided_at          TEXT,
  decided_by          TEXT
);
CREATE INDEX IF NOT EXISTS idx_portal_inbound_project ON portal_inbound(internal_project_id);

CREATE TABLE IF NOT EXISTS portal_audit_mirror (
  cloud_id            INTEGER PRIMARY KEY,
  proposal_id         TEXT,
  internal_project_id TEXT,
  actor_type          TEXT,
  actor_id            TEXT,
  actor_name          TEXT,
  event_type          TEXT,
  field_token         TEXT,
  old_value_json      TEXT,
  new_value_json      TEXT,
  note                TEXT,
  created_at          TEXT,
  row_json            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portal_audit_mirror_project ON portal_audit_mirror(internal_project_id, created_at);
"""


def _conn():
    os.makedirs(os.path.dirname(tp_db.db_path()), exist_ok=True)
    c = sqlite3.connect(tp_db.db_path())
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = _conn()
    try:
        c.executescript(_SCHEMA)
        c.commit()
    finally:
        c.close()


def _now():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


# ── portal_links ─────────────────────────────────────────────────────────────

def get_link(internal_project_id):
    c = _conn()
    try:
        row = c.execute("SELECT * FROM portal_links WHERE internal_project_id=?",
                        (internal_project_id,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def upsert_link(internal_project_id, **fields):
    cur = get_link(internal_project_id) or {'internal_project_id': internal_project_id, 'data_json': '{}'}
    cur.update({k: v for k, v in fields.items() if v is not None})
    c = _conn()
    try:
        c.execute("""
            INSERT INTO portal_links
              (internal_project_id, cloud_proposal_id, cloud_client_id, portal_status,
               last_published_revision, last_published_at, last_pulled_at, data_json)
            VALUES (:internal_project_id, :cloud_proposal_id, :cloud_client_id, :portal_status,
                    :last_published_revision, :last_published_at, :last_pulled_at, :data_json)
            ON CONFLICT(internal_project_id) DO UPDATE SET
              cloud_proposal_id=excluded.cloud_proposal_id,
              cloud_client_id=excluded.cloud_client_id,
              portal_status=excluded.portal_status,
              last_published_revision=excluded.last_published_revision,
              last_published_at=excluded.last_published_at,
              last_pulled_at=excluded.last_pulled_at,
              data_json=excluded.data_json
        """, {
            'internal_project_id': internal_project_id,
            'cloud_proposal_id': cur.get('cloud_proposal_id'),
            'cloud_client_id': cur.get('cloud_client_id'),
            'portal_status': cur.get('portal_status'),
            'last_published_revision': cur.get('last_published_revision'),
            'last_published_at': cur.get('last_published_at'),
            'last_pulled_at': cur.get('last_pulled_at'),
            'data_json': cur.get('data_json') or '{}',
        })
        c.commit()
    finally:
        c.close()
    return get_link(internal_project_id)


def list_links():
    c = _conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM portal_links ORDER BY last_published_at DESC").fetchall()]
    finally:
        c.close()


# ── portal_audit_mirror ──────────────────────────────────────────────────────

def mirror_audit_rows(rows, internal_project_id=None):
    """rows: list of cloud portal_audit_events dicts. Idempotent on cloud_id."""
    c = _conn()
    n = 0
    try:
        for r in rows:
            c.execute("""
                INSERT OR IGNORE INTO portal_audit_mirror
                  (cloud_id, proposal_id, internal_project_id, actor_type, actor_id, actor_name,
                   event_type, field_token, old_value_json, new_value_json, note, created_at, row_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r.get('id'), r.get('proposal_id'), internal_project_id,
                r.get('actor_type'), r.get('actor_id'), r.get('actor_name'),
                r.get('event_type'), r.get('field_token'),
                json.dumps(r.get('old_value')), json.dumps(r.get('new_value')),
                r.get('note'), r.get('created_at'), json.dumps(r, ensure_ascii=False),
            ))
            n += c.total_changes and 1 or 0
        c.commit()
    finally:
        c.close()
    return n


def touch_pull(internal_project_id):
    upsert_link(internal_project_id, last_pulled_at=_now())


def list_audit(internal_project_id, limit=500):
    c = _conn()
    try:
        rows = c.execute("""
            SELECT cloud_id, actor_type, actor_id, actor_name, event_type, field_token,
                   old_value_json, new_value_json, note, created_at
            FROM portal_audit_mirror
            WHERE internal_project_id = ?
            ORDER BY created_at DESC, cloud_id DESC
            LIMIT ?
        """, (internal_project_id, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ('old_value_json', 'new_value_json'):
                try:
                    d[k.replace('_json', '')] = json.loads(d.pop(k))
                except (TypeError, ValueError):
                    d[k.replace('_json', '')] = d.pop(k, None)
            out.append(d)
        return out
    finally:
        c.close()


# ── portal_inbound (Phase 2 — record of each Vista accept/reject decision) ────

def record_inbound(cloud_id, internal_project_id, cr_row, *, state, decided_by):
    c = _conn()
    try:
        c.execute("""
            INSERT INTO portal_inbound
              (id, cloud_id, kind, internal_project_id, item_ref, field_token,
               old_value_json, new_value_json, payload_json, state, pulled_at, decided_at, decided_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              state=excluded.state, decided_at=excluded.decided_at, decided_by=excluded.decided_by
        """, (
            'cr:' + str(cloud_id), str(cloud_id), (cr_row.get('kind') or 'field'),
            internal_project_id, cr_row.get('item_ref'), cr_row.get('field_token'),
            json.dumps(cr_row.get('old_value')), json.dumps(cr_row.get('new_value')),
            json.dumps(cr_row, ensure_ascii=False), state, _now(), _now(), decided_by,
        ))
        c.commit()
    finally:
        c.close()


def list_inbound(internal_project_id):
    c = _conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM portal_inbound WHERE internal_project_id=? ORDER BY decided_at DESC",
            (internal_project_id,)).fetchall()]
    finally:
        c.close()
