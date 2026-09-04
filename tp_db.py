"""
Vista Platform — Technical Proposals central local database layer.

Data lives under ~/.vista-platform/technical-proposals/ (same convention as
catalogue_db.py and sqi_db.py), so it survives browser refreshes, incognito
windows, proxy restarts and PC restarts, and is visible from any browser
profile / any browser hitting this same local Vista server.

Design (deliberate, mirrors sqi_db.py):
  * Each PROJECT is stored as ONE ROW holding its full JSON document exactly
    as the browser holds it today — project metadata, pricing settings, the
    items[] array (names / quantities / units / dimensions / specifications /
    presets / statuses / notes / pricing / client inputs), each item's
    execution roadmap (activities / days / progress / waiting / parallel /
    dependencies / risks / client inputs) and project.execution.waves[].
    A handful of columns (name, client, updated_at) are lifted out purely
    for listing/sorting — never a second source of truth. Nothing is
    field-mapped or restructured.
  * PHOTO BYTES never go in the JSON. They are written to disk under
    uploads/<projectId>/<photoId>.<ext>; the database holds only metadata
    (slot, mime, w, h, bytes, sha256, keep_page) + a relative path. Photo
    ORDER is already canonical inside the project JSON
    (item.existingPhotoIds[] etc), so it is not duplicated here.

Phase 1 scope: storage + migration + manual backup + emergency browser
export. NOT in scope yet: automatic daily backups, Keep-importer
integration, review-draft storage, IndexedDB removal.
"""
import base64
import datetime
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import zipfile

_DATA_DIR   = os.path.join(os.path.expanduser('~'), '.vista-platform', 'technical-proposals')
_DB_PATH    = os.path.join(_DATA_DIR, 'technical-proposals.db')
UPLOADS_DIR = os.path.join(_DATA_DIR, 'uploads')    # accessed by the photo-serving route
BACKUPS_DIR = os.path.join(_DATA_DIR, 'backups')

_SAFE_ID_RE = re.compile(r'^[A-Za-z0-9_.\-]+$')
_MIME_EXT   = {'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}
MAX_PHOTO_BYTES = 25 * 1024 * 1024   # generous ceiling; browser resizes to ~2000px/q0.82 first

_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS projects (
  id          TEXT PRIMARY KEY,
  name        TEXT,
  client      TEXT,
  location    TEXT,
  revision    TEXT,
  created_at  TEXT,
  updated_at  TEXT NOT NULL,
  schema_ver  INTEGER NOT NULL DEFAULT 1,
  data_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS photos (
  id          TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL,
  item_id     TEXT,
  slot        TEXT,
  mime        TEXT,
  w           INTEGER,
  h           INTEGER,
  bytes       INTEGER,
  sha256      TEXT,
  keep_page   INTEGER,
  stored_path TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_photos_project ON photos(project_id);
CREATE INDEX IF NOT EXISTS idx_photos_item    ON photos(item_id);

CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- reserved for Phase 2 (importer review-draft central storage); unused in Phase 1
CREATE TABLE IF NOT EXISTS review_drafts (
  id         TEXT PRIMARY KEY,
  updated_at TEXT NOT NULL,
  data_json  TEXT NOT NULL
);
"""


# ── connection / init ─────────────────────────────────────────────────────────

def _conn():
    os.makedirs(_DATA_DIR, exist_ok=True)
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    c = _conn()
    try:
        c.executescript(_SCHEMA)
        c.commit()
    finally:
        c.close()


def db_path():
    return _DB_PATH


def _now_iso():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


# ── projects ─────────────────────────────────────────────────────────────────

def list_projects():
    c = _conn()
    try:
        rows = c.execute("SELECT data_json FROM projects ORDER BY updated_at DESC").fetchall()
        return [json.loads(r['data_json']) for r in rows]
    finally:
        c.close()


def get_project(project_id):
    c = _conn()
    try:
        row = c.execute("SELECT data_json FROM projects WHERE id=?", (project_id,)).fetchone()
        return json.loads(row['data_json']) if row else None
    finally:
        c.close()


def project_exists(project_id):
    c = _conn()
    try:
        return c.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is not None
    finally:
        c.close()


def upsert_project(doc: dict):
    """Store the project document VERBATIM as JSON. id/name/client/location/
    revision/updatedAt are also lifted into columns purely for listing."""
    pid = doc.get('id')
    if not pid:
        raise ValueError('project id is required')
    c = _conn()
    try:
        c.execute("""
            INSERT INTO projects (id, name, client, location, revision, created_at, updated_at, schema_ver, data_json)
            VALUES (:id, :name, :client, :location, :revision, :created_at, :updated_at, :schema_ver, :data_json)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name, client=excluded.client, location=excluded.location,
              revision=excluded.revision, updated_at=excluded.updated_at,
              schema_ver=excluded.schema_ver, data_json=excluded.data_json
        """, {
            'id': pid,
            'name': doc.get('name'),
            'client': doc.get('client'),
            'location': doc.get('location'),
            'revision': doc.get('revision'),
            'created_at': _stringify_ts(doc.get('createdAt')),
            'updated_at': _stringify_ts(doc.get('updatedAt') or doc.get('createdAt')) or _now_iso(),
            'schema_ver': int(doc.get('schemaVer') or 1),
            'data_json': json.dumps(doc, ensure_ascii=False),
        })
        c.commit()
    finally:
        c.close()


def _stringify_ts(v):
    """createdAt/updatedAt are epoch-ms numbers in the browser model; keep them
    sortable as strings for the column without touching the value in data_json."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.datetime.utcfromtimestamp(v / 1000).strftime('%Y-%m-%dT%H:%M:%SZ')
        except (OverflowError, OSError, ValueError):
            return str(v)
    return str(v)


def delete_project(project_id):
    c = _conn()
    try:
        c.execute("DELETE FROM projects WHERE id=?", (project_id,))
        c.execute("DELETE FROM photos WHERE project_id=?", (project_id,))
        c.commit()
    finally:
        c.close()
    d = os.path.join(UPLOADS_DIR, project_id)
    if _SAFE_ID_RE.match(project_id or '') and os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)


# ── duplicate (server-side clone: new project + item + photo IDs, own files) ──

_uid_ctr = 0


def _uid():
    """Fresh id, matches _SAFE_ID_RE ([0-9a-f]+). Time + counter + random so it
    is unique even inside one tight duplication loop and never collides with an
    existing id."""
    global _uid_ctr
    _uid_ctr += 1
    return (format(int(datetime.datetime.utcnow().timestamp() * 1000), 'x')
            + format(_uid_ctr, 'x') + secrets.token_hex(4))


def duplicate_project(src_id, name=None, revision=None) -> dict:
    """Deep-clone project <src_id> into a brand-new project.

      * new project ID, new item IDs, new photo IDs, own copied photo files
      * every photo reference inside the doc (item.photoIds / existingPhotoIds /
        referencePhotoIds) rewritten to the new IDs; photo DB rows re-pointed to
        the new project + new item IDs
      * everything else in the JSON (quantities, dims, pricing, presets,
        statuses, notes, client inputs, keepSource metadata, roadmap activities,
        dependencies, risks, dismissedRuleKeys, execution.waves + waveId
        assignments) copied VERBATIM — wave/activity/risk ids stay as-is because
        they are only ever referenced within the same doc and remain internally
        consistent after the deep copy
      * createdAt / updatedAt set to now; provenance kept in `duplicatedFrom`
      * THE SOURCE PROJECT IS ONLY READ — never written, moved or altered
      * on any failure the partial new project (rows + files) is removed so no
        half-created duplicate is ever left behind

    Returns the new project document.
    """
    src = get_project(src_id)
    if src is None:
        raise KeyError('source project not found')

    now_ms = int(datetime.datetime.utcnow().timestamp() * 1000)
    new_pid = _uid()

    doc = json.loads(json.dumps(src))            # detached deep copy
    doc['id'] = new_pid
    doc['name'] = (name.strip() if isinstance(name, str) and name.strip()
                   else (src.get('name') or 'Project') + ' — Copy')
    if isinstance(revision, str) and revision.strip():
        doc['revision'] = revision.strip()
    doc['createdAt'] = now_ms
    doc['updatedAt'] = now_ms
    doc['duplicatedFrom'] = src_id

    item_id_map = {}
    photo_id_map = {}

    def remap_photo(old):
        if not old:
            return old
        if old not in photo_id_map:
            photo_id_map[old] = _uid()
        return photo_id_map[old]

    for it in (doc.get('items') or []):
        old_iid = it.get('id')
        new_iid = _uid()
        item_id_map[old_iid] = new_iid
        it['id'] = new_iid
        it['createdAt'] = now_ms
        it['updatedAt'] = now_ms
        it['existingPhotoIds'] = [remap_photo(x) for x in (it.get('existingPhotoIds') or [])]
        it['photoIds'] = {k: remap_photo(v) for k, v in (it.get('photoIds') or {}).items() if v}
        if 'referencePhotoIds' in it:
            it['referencePhotoIds'] = [remap_photo(x) for x in (it.get('referencePhotoIds') or [])]

    src_photos = list_photos(src_id)
    src_photo_by_id = {p['id']: p for p in src_photos}
    for p in src_photos:                          # also carry any orphan rows
        remap_photo(p['id'])

    try:
        for old_pid, new_photo_id in photo_id_map.items():
            row = src_photo_by_id.get(old_pid)
            if not row:
                continue                          # dangling ref, no file to copy
            got = get_photo_bytes(old_pid)
            if not got:
                continue
            data, mime, _meta = got
            save_photo({
                'id': new_photo_id,
                'projectId': new_pid,
                'itemId': item_id_map.get(row['item_id'], row['item_id']),
                'slot': row['slot'],
                'mime': mime,
                'w': row['w'],
                'h': row['h'],
                'keepPage': row['keep_page'],
            }, data, overwrite=True)
        upsert_project(doc)
    except Exception:
        delete_project(new_pid)                   # rows + files + uploads/<new_pid>/
        raise

    return doc


# ── photos ───────────────────────────────────────────────────────────────────

def photo_exists(photo_id):
    c = _conn()
    try:
        return c.execute("SELECT 1 FROM photos WHERE id=?", (photo_id,)).fetchone() is not None
    finally:
        c.close()


def get_photo_meta(photo_id):
    c = _conn()
    try:
        row = c.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def list_photos(project_id=None):
    c = _conn()
    try:
        if project_id:
            rows = c.execute("SELECT * FROM photos WHERE project_id=? ORDER BY created_at, id", (project_id,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM photos ORDER BY project_id, created_at, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def save_photo(meta: dict, data: bytes, overwrite=False) -> dict:
    """meta: {id, projectId, itemId, slot, mime, w, h, keepPage}. Returns
    {'id':..., 'skipped':bool, 'sha256':..., 'bytes':...}. When the id already
    exists and overwrite is False the existing copy is left untouched
    (idempotent / resumable migration)."""
    pid = meta.get('id')
    project_id = meta.get('projectId')
    if not pid or not _SAFE_ID_RE.match(pid):
        raise ValueError('invalid photo id')
    if not project_id or not _SAFE_ID_RE.match(project_id):
        raise ValueError('invalid project id')
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError('photo exceeds size limit')

    if photo_exists(pid) and not overwrite:
        m = get_photo_meta(pid)
        return {'id': pid, 'skipped': True, 'sha256': m['sha256'], 'bytes': m['bytes']}

    ext = _MIME_EXT.get((meta.get('mime') or '').lower(), '.jpg')
    rel_dir = project_id
    abs_dir = os.path.join(UPLOADS_DIR, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    stored_name = pid + ext
    abs_path = os.path.join(abs_dir, stored_name)
    tmp_path = abs_path + '.tmp'
    with open(tmp_path, 'wb') as f:
        f.write(data)
    os.replace(tmp_path, abs_path)
    stored_path = rel_dir + '/' + stored_name

    sha = hashlib.sha256(data).hexdigest()
    c = _conn()
    try:
        c.execute("""
            INSERT INTO photos (id, project_id, item_id, slot, mime, w, h, bytes, sha256, keep_page, stored_path)
            VALUES (:id, :project_id, :item_id, :slot, :mime, :w, :h, :bytes, :sha256, :keep_page, :stored_path)
            ON CONFLICT(id) DO UPDATE SET
              project_id=excluded.project_id, item_id=excluded.item_id, slot=excluded.slot,
              mime=excluded.mime, w=excluded.w, h=excluded.h, bytes=excluded.bytes,
              sha256=excluded.sha256, keep_page=excluded.keep_page, stored_path=excluded.stored_path
        """, {
            'id': pid, 'project_id': project_id, 'item_id': meta.get('itemId'),
            'slot': meta.get('slot'), 'mime': meta.get('mime'),
            'w': _int_or_none(meta.get('w')), 'h': _int_or_none(meta.get('h')),
            'bytes': len(data), 'sha256': sha, 'keep_page': _int_or_none(meta.get('keepPage')),
            'stored_path': stored_path,
        })
        c.commit()
    finally:
        c.close()
    return {'id': pid, 'skipped': False, 'sha256': sha, 'bytes': len(data)}


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def get_photo_bytes(photo_id):
    meta = get_photo_meta(photo_id)
    if not meta:
        return None
    abs_path = os.path.join(UPLOADS_DIR, meta['stored_path'])
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, 'rb') as f:
        data = f.read()
    return data, meta['mime'], meta


def delete_photo(photo_id):
    meta = get_photo_meta(photo_id)
    if not meta:
        return False
    c = _conn()
    try:
        c.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        c.commit()
    finally:
        c.close()
    try:
        os.remove(os.path.join(UPLOADS_DIR, meta['stored_path']))
    except OSError:
        pass
    return True


# ── settings ─────────────────────────────────────────────────────────────────

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


# ── status / counts ──────────────────────────────────────────────────────────

def counts():
    c = _conn()
    try:
        proj_rows = c.execute("SELECT data_json FROM projects").fetchall()
        items = 0
        for r in proj_rows:
            try:
                items += len(json.loads(r['data_json']).get('items') or [])
            except (ValueError, TypeError):
                pass
        photos = c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        photo_bytes = c.execute("SELECT COALESCE(SUM(bytes),0) FROM photos").fetchone()[0]
        return {'projects': len(proj_rows), 'items': items, 'photos': photos, 'photoBytes': photo_bytes}
    finally:
        c.close()


# ── idempotent migration import ──────────────────────────────────────────────

def migrate_bundle(payload: dict, overwrite=False) -> dict:
    """Import a browser bundle {projects:[...], photos:[{...,dataBase64}]}.
    When overwrite is False an id already present in the central DB is
    SKIPPED (not clobbered) and counted — this makes the endpoint safe to
    call repeatedly and resumable. Never moves, deletes or clears anything
    on the browser side (that is the caller's job — and it doesn't)."""
    result = {
        'projects': {'imported': 0, 'skipped': 0},
        'photos':   {'imported': 0, 'skipped': 0},
        'errors': [],
    }

    for doc in (payload.get('projects') or []):
        try:
            pid = doc.get('id')
            if not pid:
                result['errors'].append('project with no id skipped')
                continue
            if not overwrite and project_exists(pid):
                result['projects']['skipped'] += 1
                continue
            upsert_project(doc)
            result['projects']['imported'] += 1
        except Exception as e:  # noqa: BLE001
            result['errors'].append('project ' + str(doc.get('id')) + ': ' + str(e))

    for ph in (payload.get('photos') or []):
        try:
            pid = ph.get('id')
            if not pid:
                result['errors'].append('photo with no id skipped')
                continue
            if not overwrite and photo_exists(pid):
                result['photos']['skipped'] += 1
                continue
            data = base64.b64decode(ph.get('dataBase64') or '')
            r = save_photo(ph, data, overwrite=overwrite)
            if r.get('skipped'):
                result['photos']['skipped'] += 1
            else:
                result['photos']['imported'] += 1
        except Exception as e:  # noqa: BLE001
            result['errors'].append('photo ' + str(ph.get('id')) + ': ' + str(e))

    return result


# ── manual backup (Phase 1: export only, no restore UI) ──────────────────────

def _wal_checkpoint():
    try:
        c = _conn()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
    except sqlite3.Error:
        pass


def make_backup_zip_bytes():
    """Return (filename, bytes) of a self-contained .zip holding the SQLite
    database file plus the entire uploads/ tree."""
    _wal_checkpoint()
    stamp = datetime.datetime.now().strftime('%Y-%m-%d-%H%M')
    fname = f'vista-tp-backup-{stamp}.zip'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        if os.path.isfile(_DB_PATH):
            z.write(_DB_PATH, 'technical-proposals.db')
        for extra in ('-wal', '-shm'):
            p = _DB_PATH + extra
            if os.path.isfile(p):
                z.write(p, 'technical-proposals.db' + extra)
        if os.path.isdir(UPLOADS_DIR):
            for root, _dirs, files in os.walk(UPLOADS_DIR):
                for fn in files:
                    ap = os.path.join(root, fn)
                    rel = os.path.relpath(ap, _DATA_DIR).replace(os.sep, '/')
                    z.write(ap, rel)
        manifest = {
            'kind': 'vista-tp-backup',
            'version': 1,
            'createdAt': _now_iso(),
            'counts': counts(),
        }
        z.writestr('manifest.json', json.dumps(manifest, indent=2))
    return fname, buf.getvalue()
