"""
Supplier Quotation Intelligence — persistent local database layer.

Data lives under ~/.vista-platform/sqi/ (same convention as
catalogue_db.py), so it survives browser refreshes, incognito windows,
proxy restarts, and PC restarts, and is visible from any browser profile
hitting this same local Vista server.

Design note: each major entity (an evaluation, a historical price record,
an extraction) is stored as ONE ROW holding its full JSON payload, plus a
handful of indexed columns for listing/sorting. The rich nested structure
that already exists in the browser's data model — client scope, supplier
lines, commercial charges, revisions, matching overrides, the AI
assessment, the Vista quotation draft — all travels inside that JSON
payload exactly as it already does in memory today. This is a deliberate
choice: it makes every one of those nested entities durable (satisfying
the persistence requirement for all of them) without requiring a rewrite
of the large existing matching/scoring/AI logic that already reads and
writes that nested shape directly. Uploaded document BYTES are the one
thing that never belongs in a JSON blob — those are written to disk under
files/, with the database holding only metadata + a path reference.
"""
import base64
import os
import re
import shutil
import sqlite3
import json

_DATA_DIR  = os.path.join(os.path.expanduser('~'), '.vista-platform', 'sqi')
_DB_PATH   = os.path.join(_DATA_DIR, 'sqi.db')
FILES_DIR  = os.path.join(_DATA_DIR, 'files')   # accessed by proxy file-serving route

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS evaluations (
  id          TEXT PRIMARY KEY,
  name        TEXT,
  client_name TEXT,
  created_at  TEXT,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  data_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history_records (
  id          TEXT PRIMARY KEY,
  supplier    TEXT,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  data_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key         TEXT PRIMARY KEY,
  value_json  TEXT NOT NULL,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS files (
  id             TEXT PRIMARY KEY,
  evaluation_id  TEXT,
  original_name  TEXT,
  stored_path    TEXT NOT NULL,
  content_type   TEXT,
  size_bytes     INTEGER,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_files_eval ON files(evaluation_id);

CREATE TABLE IF NOT EXISTS extractions (
  id             TEXT PRIMARY KEY,
  evaluation_id  TEXT,
  updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  data_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extractions_eval ON extractions(evaluation_id);
"""

_SAFE_ID_RE = re.compile(r'^[A-Za-z0-9_.\-]+$')


def _conn():
    os.makedirs(_DATA_DIR, exist_ok=True)
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    os.makedirs(FILES_DIR, exist_ok=True)
    c = _conn()
    try:
        c.executescript(_SCHEMA)
        c.commit()
    finally:
        c.close()


def db_path():
    return _DB_PATH


# ── Evaluations ────────────────────────────────────────────────────────────────

def list_evaluations():
    c = _conn()
    try:
        rows = c.execute("SELECT data_json FROM evaluations ORDER BY updated_at DESC").fetchall()
        return [json.loads(r['data_json']) for r in rows]
    finally:
        c.close()


def get_evaluation(eval_id):
    c = _conn()
    try:
        row = c.execute("SELECT data_json FROM evaluations WHERE id=?", (eval_id,)).fetchone()
        return json.loads(row['data_json']) if row else None
    finally:
        c.close()


def upsert_evaluation(ev: dict):
    """ev is the full evaluation object exactly as the browser holds it —
    stored verbatim as JSON; id/name/clientName/updatedAt are also lifted
    into indexed columns purely for listing/sorting, never a second source
    of truth."""
    if not ev.get('id'):
        raise ValueError('evaluation id is required')
    c = _conn()
    try:
        c.execute("""
            INSERT INTO evaluations (id, name, client_name, created_at, updated_at, data_json)
            VALUES (:id, :name, :client_name, :created_at, :updated_at, :data_json)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name, client_name=excluded.client_name,
              updated_at=excluded.updated_at, data_json=excluded.data_json
        """, {
            'id': ev['id'], 'name': ev.get('name'), 'client_name': ev.get('clientName'),
            'created_at': ev.get('createdAt'), 'updated_at': ev.get('updatedAt') or ev.get('createdAt'),
            'data_json': json.dumps(ev, ensure_ascii=False),
        })
        c.commit()
    finally:
        c.close()


def delete_evaluation(eval_id):
    c = _conn()
    try:
        c.execute("DELETE FROM evaluations WHERE id=?", (eval_id,))
        file_rows = c.execute("SELECT id, stored_path FROM files WHERE evaluation_id=?", (eval_id,)).fetchall()
        c.execute("DELETE FROM files WHERE evaluation_id=?", (eval_id,))
        c.execute("DELETE FROM extractions WHERE evaluation_id=?", (eval_id,))
        c.commit()
        for r in file_rows:
            try:
                d = os.path.dirname(os.path.join(FILES_DIR, r['stored_path']))
                shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
    finally:
        c.close()


# ── Historical pricing records ───────────────────────────────────────────────

def list_history():
    c = _conn()
    try:
        rows = c.execute("SELECT data_json FROM history_records ORDER BY updated_at ASC").fetchall()
        return [json.loads(r['data_json']) for r in rows]
    finally:
        c.close()


def save_history(records: list):
    """Bulk replace — matches the browser's existing whole-list get/save
    semantics exactly (Store.getHistory()/saveHistory())."""
    c = _conn()
    try:
        c.execute("DELETE FROM history_records")
        for rec in records:
            rid = rec.get('id') or rec.get('recordId')
            if not rid:
                continue
            c.execute("""
                INSERT INTO history_records (id, supplier, data_json)
                VALUES (?, ?, ?)
            """, (str(rid), rec.get('supplier'), json.dumps(rec, ensure_ascii=False)))
        c.commit()
    finally:
        c.close()


# ── Settings (aiConfig, rowClassCorrections, seeded flag, etc) ──────────────

def get_setting(key, default=None):
    c = _conn()
    try:
        row = c.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row['value_json']) if row else default
    finally:
        c.close()


def set_setting(key, value):
    c = _conn()
    try:
        c.execute("""
            INSERT INTO settings (key, value_json, updated_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
        """, (key, json.dumps(value, ensure_ascii=False)))
        c.commit()
    finally:
        c.close()


def list_settings():
    c = _conn()
    try:
        rows = c.execute("SELECT key, value_json FROM settings").fetchall()
        return {r['key']: json.loads(r['value_json']) for r in rows}
    finally:
        c.close()


# ── Files (uploaded source documents — bytes on disk, metadata in DB) ───────

def _safe_filename(name):
    name = os.path.basename(name or 'file')
    name = re.sub(r'[^A-Za-z0-9 ._\-()]', '_', name).strip() or 'file'
    return name[:180]


def save_file(file_id, evaluation_id, original_name, content_type, data: bytes):
    if not _SAFE_ID_RE.match(file_id or ''):
        raise ValueError('invalid file id')
    safe_name = _safe_filename(original_name)
    rel_dir = file_id
    abs_dir = os.path.join(FILES_DIR, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(abs_dir, safe_name)
    with open(abs_path, 'wb') as f:
        f.write(data)
    stored_path = rel_dir + '/' + safe_name
    c = _conn()
    try:
        c.execute("""
            INSERT INTO files (id, evaluation_id, original_name, stored_path, content_type, size_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              evaluation_id=excluded.evaluation_id, original_name=excluded.original_name,
              stored_path=excluded.stored_path, content_type=excluded.content_type,
              size_bytes=excluded.size_bytes
        """, (file_id, evaluation_id, original_name, stored_path, content_type, len(data)))
        c.commit()
    finally:
        c.close()
    return {'id': file_id, 'evaluationId': evaluation_id, 'originalName': original_name,
            'contentType': content_type, 'sizeBytes': len(data)}


def get_file_meta(file_id):
    c = _conn()
    try:
        row = c.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def get_file_bytes(file_id):
    meta = get_file_meta(file_id)
    if not meta:
        return None
    abs_path = os.path.join(FILES_DIR, meta['stored_path'])
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, 'rb') as f:
        data = f.read()
    return data, meta['content_type'], meta['original_name']


def list_files(evaluation_id=None):
    c = _conn()
    try:
        if evaluation_id:
            rows = c.execute("SELECT * FROM files WHERE evaluation_id=?", (evaluation_id,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM files").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# ── Extractions (structured extraction results, incl. embedded thumbnails) ──

def save_extraction(extraction_id, evaluation_id, data: dict):
    c = _conn()
    try:
        c.execute("""
            INSERT INTO extractions (id, evaluation_id, updated_at, data_json)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'), ?)
            ON CONFLICT(id) DO UPDATE SET
              evaluation_id=excluded.evaluation_id, updated_at=excluded.updated_at, data_json=excluded.data_json
        """, (extraction_id, evaluation_id, json.dumps(data, ensure_ascii=False)))
        c.commit()
    finally:
        c.close()


def get_extraction(extraction_id):
    c = _conn()
    try:
        row = c.execute("SELECT data_json FROM extractions WHERE id=?", (extraction_id,)).fetchone()
        return json.loads(row['data_json']) if row else None
    finally:
        c.close()


# ── Status / counts ───────────────────────────────────────────────────────────

def counts():
    c = _conn()
    try:
        return {
            'evaluations': c.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0],
            'historyRecords': c.execute("SELECT COUNT(*) FROM history_records").fetchone()[0],
            'files': c.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            'extractions': c.execute("SELECT COUNT(*) FROM extractions").fetchone()[0],
        }
    finally:
        c.close()


# ── Backup export / import (also used for one-shot browser migration) ───────

def export_all():
    """Self-contained backup: file bytes are inlined as base64 so a single
    downloaded JSON is a complete, restorable snapshot."""
    files_out = []
    for meta in list_files():
        got = get_file_bytes(meta['id'])
        if not got:
            continue
        data, content_type, original_name = got
        files_out.append({
            'id': meta['id'], 'evaluationId': meta['evaluation_id'], 'originalName': original_name,
            'contentType': content_type, 'dataBase64': base64.b64encode(data).decode('ascii'),
        })
    c = _conn()
    try:
        extraction_rows = c.execute("SELECT id, evaluation_id, data_json FROM extractions").fetchall()
    finally:
        c.close()
    return {
        'evaluations': list_evaluations(),
        'history': list_history(),
        'settings': list_settings(),
        'files': files_out,
        'extractions': [{'id': r['id'], 'evaluationId': r['evaluation_id'], 'data': json.loads(r['data_json'])} for r in extraction_rows],
    }


def import_all(payload: dict, overwrite=False):
    """Writes a bundle (shaped like export_all()'s output, or the
    browser's own localStorage/IndexedDB bundle for first-run migration)
    into the database. When overwrite=False (migration), an id that
    already exists in the backend is left untouched and counted as
    'skipped' rather than clobbered — makes this endpoint safe to call
    repeatedly / idempotent. When overwrite=True (explicit restore), the
    backend copy always loses to the imported one."""
    result = {'evaluations': {'imported': 0, 'skipped': 0}, 'history': {'imported': 0, 'skipped': 0},
              'settings': {'imported': 0, 'skipped': 0}, 'files': {'imported': 0, 'skipped': 0},
              'extractions': {'imported': 0, 'skipped': 0}, 'errors': []}

    existing_eval_ids = {e['id'] for e in list_evaluations()}
    for ev in (payload.get('evaluations') or []):
        try:
            eid = ev.get('id')
            if not eid:
                continue
            if not overwrite and eid in existing_eval_ids:
                result['evaluations']['skipped'] += 1
                continue
            upsert_evaluation(ev)
            result['evaluations']['imported'] += 1
        except Exception as e:  # noqa: BLE001
            result['errors'].append('evaluation ' + str(ev.get('id')) + ': ' + str(e))

    history_in = payload.get('history') or []
    if history_in:
        if overwrite:
            save_history(history_in)
            result['history']['imported'] = len(history_in)
        else:
            existing_hist_ids = {str(h.get('id') or h.get('recordId')) for h in list_history()}
            merged = list_history()
            for rec in history_in:
                rid = str(rec.get('id') or rec.get('recordId') or '')
                if rid and rid in existing_hist_ids:
                    result['history']['skipped'] += 1
                    continue
                merged.append(rec)
                result['history']['imported'] += 1
            save_history(merged)

    existing_settings = list_settings() if not overwrite else {}
    for key, value in (payload.get('settings') or {}).items():
        if not overwrite and key in existing_settings:
            result['settings']['skipped'] += 1
            continue
        set_setting(key, value)
        result['settings']['imported'] += 1
    # Back-compat: allow the browser's flat LS_KEYS shape too (aiConfig, rowClassCorrections, seeded)
    for key in ('aiConfig', 'rowClassCorrections', 'seeded'):
        if key in payload and key not in (payload.get('settings') or {}):
            if overwrite or key not in existing_settings:
                set_setting(key, payload[key])
                result['settings']['imported'] += 1
            else:
                result['settings']['skipped'] += 1

    existing_file_ids = {f['id'] for f in list_files()}
    for f in (payload.get('files') or []):
        try:
            fid = f.get('id')
            if not fid:
                continue
            if not overwrite and fid in existing_file_ids:
                result['files']['skipped'] += 1
                continue
            data = base64.b64decode(f['dataBase64']) if f.get('dataBase64') else b''
            save_file(fid, f.get('evaluationId'), f.get('originalName') or fid, f.get('contentType'), data)
            result['files']['imported'] += 1
        except Exception as e:  # noqa: BLE001
            result['errors'].append('file ' + str(f.get('id')) + ': ' + str(e))

    c = _conn()
    try:
        existing_extraction_ids = {r[0] for r in c.execute("SELECT id FROM extractions").fetchall()}
    finally:
        c.close()
    for ex in (payload.get('extractions') or []):
        try:
            xid = ex.get('id')
            if not xid:
                continue
            if not overwrite and xid in existing_extraction_ids:
                result['extractions']['skipped'] += 1
                continue
            save_extraction(xid, ex.get('evaluationId'), ex.get('data') or {})
            result['extractions']['imported'] += 1
        except Exception as e:  # noqa: BLE001
            result['errors'].append('extraction ' + str(ex.get('id')) + ': ' + str(e))

    return result
