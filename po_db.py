"""
Vista Platform — PO Generator local database layer.

Separate SQLite database from the Technical Proposal / Central DB
(tp_db.py): PO drafts never share tables, files or revision counters with
projects, delivery notes or quotations. This isolates PO data; it does not
remove every integration risk (both are still served by the same proxy
process and live on the same PC).

Location: ~/.vista-platform/purchase-orders/purchase-orders.db
Override: environment variable VISTA_PO_DATA_DIR (tests MUST use this with a
temporary directory — never the production store).
Importing this module creates nothing; the directory and schema are created
lazily by the first write, and reads against missing storage return empty.

Phase 1: drafts (create / read / list / update with revision check).
Phase 2: original supplier documents (po_sources), extraction results
(po_extractions) and item photos (po_photos: image regions of an original). Both are insert-only — SQLite triggers reject every
UPDATE and DELETE — and original files are content-addressed by SHA-256
under <data_dir>/sources/, written once, never overwritten, and re-hashed
on every read. Still reserved and NOT created: issued_pos (frozen snapshot,
UNIQUE po_no, UNIQUE idempotency key, allocated atomically inside one
BEGIN IMMEDIATE transaction). No delete, no issuance.

Revision protection mirrors tp_db.py: every draft has an integer `rev`; an
update must name the rev it was loaded at and succeeds only via
UPDATE ... WHERE id=? AND rev=?, so a stale tab can never overwrite a newer
save.
"""
import datetime
import hashlib
import json
import os
import re
import secrets
import sqlite3


def _data_dir():
    return os.environ.get('VISTA_PO_DATA_DIR') or os.path.join(
        os.path.expanduser('~'), '.vista-platform', 'purchase-orders')


def db_path():
    return os.path.join(_data_dir(), 'purchase-orders.db')


_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS po_drafts (
  id            TEXT PRIMARY KEY,
  status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft')),
  title         TEXT,
  supplier_id   TEXT,
  supplier_name TEXT,
  currency      TEXT,
  item_count    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  schema_ver    INTEGER NOT NULL DEFAULT 1,
  rev           INTEGER NOT NULL DEFAULT 1,
  data_json     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_po_drafts_updated ON po_drafts(updated_at);

CREATE TABLE IF NOT EXISTS po_sources (
  id            TEXT PRIMARY KEY,
  draft_id      TEXT NOT NULL,
  original_name TEXT NOT NULL,
  mime          TEXT NOT NULL,
  size_bytes    INTEGER NOT NULL,
  sha256        TEXT NOT NULL,
  stored_name   TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  UNIQUE (draft_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_po_sources_draft ON po_sources(draft_id);
CREATE TRIGGER IF NOT EXISTS po_sources_immutable_u BEFORE UPDATE ON po_sources
  BEGIN SELECT RAISE(ABORT, 'po_sources rows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS po_sources_immutable_d BEFORE DELETE ON po_sources
  BEGIN SELECT RAISE(ABORT, 'po_sources rows are immutable'); END;

CREATE TABLE IF NOT EXISTS po_extractions (
  id            TEXT PRIMARY KEY,
  source_id     TEXT NOT NULL,
  engine        TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  summary_json  TEXT NOT NULL,
  data_json     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_po_extractions_source ON po_extractions(source_id);
CREATE TRIGGER IF NOT EXISTS po_extractions_immutable_u BEFORE UPDATE ON po_extractions
  BEGIN SELECT RAISE(ABORT, 'po_extractions rows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS po_extractions_immutable_d BEFORE DELETE ON po_extractions
  BEGIN SELECT RAISE(ABORT, 'po_extractions rows are immutable'); END;

CREATE TABLE IF NOT EXISTS po_photos (
  id            TEXT PRIMARY KEY,
  source_id     TEXT NOT NULL,
  page          INTEGER NOT NULL,
  region_json   TEXT NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN ('embedded', 'source_crop')),
  mime          TEXT NOT NULL,
  size_bytes    INTEGER NOT NULL,
  sha256        TEXT NOT NULL,
  stored_name   TEXT NOT NULL,
  extraction_id TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_po_photos_source ON po_photos(source_id);
CREATE TRIGGER IF NOT EXISTS po_photos_immutable_u BEFORE UPDATE ON po_photos
  BEGIN SELECT RAISE(ABORT, 'po_photos rows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS po_photos_immutable_d BEFORE DELETE ON po_photos
  BEGIN SELECT RAISE(ABORT, 'po_photos rows are immutable'); END;
"""

# Original supplier documents
SOURCE_MAX_BYTES = 25 * 1024 * 1024
SOURCE_TYPES = {                       # mime → (extension, required leading bytes)
    'application/pdf': ('.pdf', b'%PDF-'),
    'image/png': ('.png', b'\x89PNG\r\n\x1a\n'),
    'image/jpeg': ('.jpg', b'\xff\xd8\xff'),
}
SOURCE_ID_RE = re.compile(r'^src_[0-9a-f]{24}$')
EXTRACTION_ID_RE = re.compile(r'^ex_[0-9a-f]{24}$')
_STORED_NAME_RE = re.compile(r'^[0-9a-f]{64}\.(pdf|png|jpg)$')

# Item photos: an image region of an original document (an embedded picture as
# placed on the page, or a user-selected "source crop"), rendered from the page
# and stored as its own immutable, content-addressed file. The original
# document itself is never modified.
PHOTO_MAX_BYTES = 5 * 1024 * 1024
PHOTO_TYPES = {'image/png': ('.png', b'\x89PNG\r\n\x1a\n'), 'image/jpeg': ('.jpg', b'\xff\xd8\xff')}
PHOTO_KINDS = ('embedded', 'source_crop')
PHOTO_ID_RE = re.compile(r'^ph_[0-9a-f]{24}$')
_PHOTO_STORED_RE = re.compile(r'^[0-9a-f]{64}\.(png|jpg)$')


class SourceInvalid(ValueError):
    pass


class SourceIntegrityError(Exception):
    pass


class PhotoConflict(Exception):
    """Different image bytes were offered for an existing photo id. Refused;
    `existing` is the stored (unchanged) photo record."""
    def __init__(self, existing):
        super().__init__('a different image is already stored under this photo id')
        self.existing = existing


class DraftExists(Exception):
    pass


class DraftGone(Exception):
    pass


class RevConflict(Exception):
    def __init__(self, current):
        super().__init__(f'revision conflict (current rev {current})')
        self.current = current


_initialized = set()


def _conn(create=False):
    """Open the PO database lazily. Importing this module never touches the
    filesystem. Read operations pass create=False and get None when no
    database exists yet (callers treat that as "no drafts"), so merely
    opening the PO page or calling /api/po/status never creates storage.
    Only a write (create=True) creates the directory and the schema."""
    path = db_path()
    if not create and not os.path.isfile(path):
        return None
    if create:
        os.makedirs(_data_dir(), exist_ok=True)
    c = sqlite3.connect(path, timeout=10, isolation_level=None)  # explicit transactions
    c.row_factory = sqlite3.Row
    if path not in _initialized:       # schema is idempotent; ensure it once per database file
        c.executescript(_SCHEMA)
        _initialized.add(path)
    return c


def init_db():
    """Explicitly create the directory and schema (not called on import)."""
    _conn(create=True).close()


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _summary_cols(doc):
    sup = doc.get('supplier') or {}
    return (doc.get('title') or '', sup.get('daftraId') or None, sup.get('name') or None,
            doc.get('currency') or '', len(doc.get('items') or []))


def list_drafts():
    c = _conn()
    if c is None:
        return []
    try:
        rows = c.execute(
            'SELECT id, status, title, supplier_id, supplier_name, currency, item_count, '
            'created_at, updated_at, rev FROM po_drafts ORDER BY updated_at DESC, id').fetchall()
        return [{
            'id': r['id'], 'status': r['status'], 'title': r['title'] or '',
            'supplierId': r['supplier_id'], 'supplierName': r['supplier_name'],
            'currency': r['currency'] or '', 'itemCount': r['item_count'],
            'createdAt': r['created_at'], 'updatedAt': r['updated_at'], 'rev': r['rev'],
        } for r in rows]
    finally:
        c.close()


def get_draft(draft_id):
    """(doc, rev) or None."""
    c = _conn()
    if c is None:
        return None
    try:
        r = c.execute('SELECT data_json, rev FROM po_drafts WHERE id = ?', (draft_id,)).fetchone()
        return (json.loads(r['data_json']), r['rev']) if r else None
    finally:
        c.close()


def create_draft(doc):
    """Insert a validated draft. Returns (doc, rev=1). Never overwrites."""
    ts = now_iso()
    doc = dict(doc, createdAt=ts, updatedAt=ts)
    title, sid, sname, cur, n = _summary_cols(doc)
    c = _conn(create=True)          # first legitimate write creates the storage + schema
    try:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM po_drafts WHERE id = ?', (doc['id'],)).fetchone():
            c.execute('ROLLBACK')
            raise DraftExists(doc['id'])
        c.execute(
            'INSERT INTO po_drafts (id, status, title, supplier_id, supplier_name, currency, item_count, '
            'created_at, updated_at, schema_ver, rev, data_json) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)',
            (doc['id'], 'draft', title, sid, sname, cur, n, ts, ts, doc.get('schema', 1),
             json.dumps(doc, ensure_ascii=False)))
        c.execute('COMMIT')
        return doc, 1
    except DraftExists:
        raise
    except Exception:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    finally:
        c.close()


def update_draft(doc, expected_rev):
    """Compare-and-swap update. Returns (doc, new_rev). Raises DraftGone or
    RevConflict without writing anything."""
    c = _conn()
    if c is None:                   # no storage yet, so the draft cannot exist
        raise DraftGone(doc['id'])
    try:
        c.execute('BEGIN IMMEDIATE')
        r = c.execute('SELECT rev, created_at FROM po_drafts WHERE id = ?', (doc['id'],)).fetchone()
        if r is None:
            c.execute('ROLLBACK')
            raise DraftGone(doc['id'])
        if r['rev'] != expected_rev:
            c.execute('ROLLBACK')
            raise RevConflict(r['rev'])
        ts = now_iso()
        doc = dict(doc, createdAt=r['created_at'], updatedAt=ts)
        title, sid, sname, cur, n = _summary_cols(doc)
        cur_upd = c.execute(
            'UPDATE po_drafts SET title=?, supplier_id=?, supplier_name=?, currency=?, item_count=?, '
            'updated_at=?, rev=rev+1, data_json=? WHERE id=? AND rev=?',
            (title, sid, sname, cur, n, ts, json.dumps(doc, ensure_ascii=False), doc['id'], expected_rev))
        if cur_upd.rowcount != 1:
            c.execute('ROLLBACK')
            raise RevConflict(r['rev'])
        c.execute('COMMIT')
        return doc, expected_rev + 1
    except (DraftGone, RevConflict):
        raise
    except Exception:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    finally:
        c.close()


def counts():
    c = _conn()
    if c is None:
        return {'drafts': 0}
    try:
        return {'drafts': c.execute('SELECT COUNT(*) FROM po_drafts').fetchone()[0]}
    finally:
        c.close()


# ── original supplier documents (immutable) ────────────────────────────────

def sources_dir():
    return os.path.join(_data_dir(), 'sources')


def _safe_name(name):
    """Display name only (never used as a path): last path component, no
    control characters, bounded length."""
    name = re.split(r'[\\/]', str(name or ''))[-1]
    name = re.sub(r'[\x00-\x1f\x7f]', '', name).strip() or 'document'
    return name[:200]


def _source_row(r):
    return {'id': r['id'], 'draftId': r['draft_id'], 'name': r['original_name'], 'mime': r['mime'],
            'size': r['size_bytes'], 'sha256': r['sha256'], 'createdAt': r['created_at']}


def save_source(draft_id, name, mime, data):
    """Store an original document unchanged. Returns (meta, existing).
    Content-addressed: the bytes are written once to sources/<sha256>.<ext>
    and never overwritten; the same bytes uploaded again for the same draft
    return the existing record."""
    if mime not in SOURCE_TYPES:
        raise SourceInvalid('Only PDF, PNG and JPEG files are accepted.')
    if not data:
        raise SourceInvalid('The file is empty.')
    if len(data) > SOURCE_MAX_BYTES:
        raise SourceInvalid(f'The file is larger than {SOURCE_MAX_BYTES // (1024 * 1024)} MB.')
    ext, magic = SOURCE_TYPES[mime]
    if not data.startswith(magic):
        raise SourceInvalid('The file content does not match its type (' + mime + ').')
    sha = hashlib.sha256(data).hexdigest()
    stored = sha + ext
    c = _conn(create=True)
    try:
        c.execute('BEGIN IMMEDIATE')
        r = c.execute('SELECT * FROM po_sources WHERE draft_id = ? AND sha256 = ?', (draft_id, sha)).fetchone()
        if r:
            c.execute('ROLLBACK')
            return _source_row(r), True
        os.makedirs(sources_dir(), exist_ok=True)
        path = os.path.join(sources_dir(), stored)
        if os.path.exists(path):
            with open(path, 'rb') as f:              # same content already stored: verify, never overwrite
                if hashlib.sha256(f.read()).hexdigest() != sha:
                    raise SourceIntegrityError('Stored file does not match its hash.')
        else:
            tmp = path + '.' + secrets.token_hex(4) + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, path)
        meta = {'id': 'src_' + secrets.token_hex(12), 'draftId': draft_id, 'name': _safe_name(name), 'mime': mime,
                'size': len(data), 'sha256': sha, 'createdAt': now_iso()}
        c.execute('INSERT INTO po_sources (id, draft_id, original_name, mime, size_bytes, sha256, stored_name, created_at) '
                  'VALUES (?,?,?,?,?,?,?,?)', (meta['id'], draft_id, meta['name'], mime, len(data), sha, stored, meta['createdAt']))
        c.execute('COMMIT')
        return meta, False
    except Exception:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    finally:
        c.close()


def get_source(source_id):
    if not SOURCE_ID_RE.match(source_id or ''):
        return None
    c = _conn()
    if c is None:
        return None
    try:
        r = c.execute('SELECT * FROM po_sources WHERE id = ?', (source_id,)).fetchone()
        return _source_row(r) if r else None
    finally:
        c.close()


def list_sources(draft_id):
    c = _conn()
    if c is None:
        return []
    try:
        rows = c.execute('SELECT * FROM po_sources WHERE draft_id = ? ORDER BY created_at, id', (draft_id,)).fetchall()
        return [_source_row(r) for r in rows]
    finally:
        c.close()


def read_source_bytes(source_id):
    """(meta, bytes) or None. The path is built only from the DB's
    content-address (validated), never from request input, and the bytes are
    re-hashed so a changed file is detected instead of served."""
    if not SOURCE_ID_RE.match(source_id or ''):
        return None
    c = _conn()
    if c is None:
        return None
    try:
        r = c.execute('SELECT * FROM po_sources WHERE id = ?', (source_id,)).fetchone()
    finally:
        c.close()
    if not r:
        return None
    stored = r['stored_name']
    if not _STORED_NAME_RE.match(stored):
        raise SourceIntegrityError('Unexpected stored name.')
    base = os.path.realpath(sources_dir())
    path = os.path.realpath(os.path.join(base, stored))
    if os.path.dirname(path) != base or not os.path.isfile(path):
        raise SourceIntegrityError('Stored file is missing.')
    with open(path, 'rb') as f:
        data = f.read()
    if hashlib.sha256(data).hexdigest() != r['sha256'] or len(data) != r['size_bytes']:
        raise SourceIntegrityError('Stored file no longer matches its recorded hash.')
    return _source_row(r), data


# ── extraction results (immutable) ─────────────────────────────────────────

def save_extraction(source_id, engine, data, summary):
    ts = now_iso()
    ex_id = 'ex_' + secrets.token_hex(12)
    c = _conn(create=True)
    try:
        c.execute('INSERT INTO po_extractions (id, source_id, engine, created_at, summary_json, data_json) VALUES (?,?,?,?,?,?)',
                  (ex_id, source_id, engine, ts, json.dumps(summary, ensure_ascii=False), json.dumps(data, ensure_ascii=False)))
        return {'id': ex_id, 'sourceId': source_id, 'engine': engine, 'createdAt': ts, 'summary': summary}
    finally:
        c.close()


def get_extraction(ex_id):
    if not EXTRACTION_ID_RE.match(ex_id or ''):
        return None
    c = _conn()
    if c is None:
        return None
    try:
        r = c.execute('SELECT * FROM po_extractions WHERE id = ?', (ex_id,)).fetchone()
        if not r:
            return None
        return {'id': r['id'], 'sourceId': r['source_id'], 'engine': r['engine'], 'createdAt': r['created_at'],
                'summary': json.loads(r['summary_json']), 'data': json.loads(r['data_json'])}
    finally:
        c.close()


def list_extractions(source_id):
    c = _conn()
    if c is None:
        return []
    try:
        rows = c.execute('SELECT id, source_id, engine, created_at, summary_json FROM po_extractions '
                         'WHERE source_id = ? ORDER BY created_at, id', (source_id,)).fetchall()
        return [{'id': r['id'], 'sourceId': r['source_id'], 'engine': r['engine'], 'createdAt': r['created_at'],
                 'summary': json.loads(r['summary_json'])} for r in rows]
    finally:
        c.close()


# ── item photos (immutable) ────────────────────────────────────────────────

def photos_dir():
    return os.path.join(_data_dir(), 'photos')


def photo_id(source_id, page, region, kind):
    """Stable identifier: the same region of the same stored document always
    maps to the same photo, so re-running an extraction finds the existing
    photo (and the user's association with it) instead of creating a new one."""
    key = '%s|%d|%.1f|%.1f|%.1f|%.1f|%s' % (source_id, page, region['x'], region['y'], region['w'], region['h'], kind)
    return 'ph_' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]


def check_region(page, region):
    """Validated page number and region {x, y, w, h} in page points (rounded to 0.1)."""
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 10000:
        raise SourceInvalid('page must be a page number.')
    if not isinstance(region, dict) or set(region) != {'x', 'y', 'w', 'h'}:
        raise SourceInvalid('region must be {x, y, w, h}.')
    out = {}
    for k in ('x', 'y', 'w', 'h'):
        v = region[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v or not -1 <= v <= 20000:
            raise SourceInvalid('region values must be page coordinates.')
        out[k] = round(float(v), 1)
    if out['w'] < 2 or out['h'] < 2:
        raise SourceInvalid('region is too small.')
    return out


def _photo_row(r):
    return {'id': r['id'], 'sourceId': r['source_id'], 'page': r['page'], 'region': json.loads(r['region_json']),
            'kind': r['kind'], 'mime': r['mime'], 'size': r['size_bytes'], 'sha256': r['sha256'],
            'extractionId': r['extraction_id'], 'createdAt': r['created_at']}


def save_photo(source_id, page, region, kind, mime, data, extraction_id=None):
    """Store one photo image. Returns (meta, existing). The record for a given
    (source, page, region, kind) is written once. The same bytes again return the
    stored record; DIFFERENT bytes for that id raise PhotoConflict and change
    nothing. No code path replaces a stored photo: a better rendering must be
    stored as a new photo (new id) and adopted by a draft explicitly."""
    if kind not in PHOTO_KINDS:
        raise SourceInvalid('kind must be embedded or source_crop.')
    if mime not in PHOTO_TYPES:
        raise SourceInvalid('Only PNG and JPEG photos are accepted.')
    if not data:
        raise SourceInvalid('The image is empty.')
    if len(data) > PHOTO_MAX_BYTES:
        raise SourceInvalid(f'The image is larger than {PHOTO_MAX_BYTES // (1024 * 1024)} MB.')
    ext, magic = PHOTO_TYPES[mime]
    if not data.startswith(magic):
        raise SourceInvalid('The image content does not match its type (' + mime + ').')
    if extraction_id is not None and not EXTRACTION_ID_RE.match(extraction_id):
        raise SourceInvalid('Invalid extraction reference.')
    region = check_region(page, region)
    pid = photo_id(source_id, page, region, kind)
    sha = hashlib.sha256(data).hexdigest()
    stored = sha + ext
    c = _conn(create=True)
    try:
        c.execute('BEGIN IMMEDIATE')
        r = c.execute('SELECT * FROM po_photos WHERE id = ?', (pid,)).fetchone()
        if r:
            c.execute('ROLLBACK')
            if r['sha256'] != sha:
                # A photo id names one image forever: different bytes are refused and the stored
                # image stays as it is. A better rendering must become a NEW photo (new id).
                raise PhotoConflict(_photo_row(r))
            return _photo_row(r), True
        if not c.execute('SELECT 1 FROM po_sources WHERE id = ?', (source_id,)).fetchone():
            raise SourceInvalid('Source document not found.')
        os.makedirs(photos_dir(), exist_ok=True)
        path = os.path.join(photos_dir(), stored)
        if os.path.exists(path):
            with open(path, 'rb') as f:              # same content already stored: verify, never overwrite
                if hashlib.sha256(f.read()).hexdigest() != sha:
                    raise SourceIntegrityError('Stored photo does not match its hash.')
        else:
            tmp = path + '.' + secrets.token_hex(4) + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, path)
        c.execute('INSERT INTO po_photos (id, source_id, page, region_json, kind, mime, size_bytes, sha256, stored_name, '
                  'extraction_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  (pid, source_id, page, json.dumps(region), kind, mime, len(data), sha, stored, extraction_id, now_iso()))
        row = c.execute('SELECT * FROM po_photos WHERE id = ?', (pid,)).fetchone()
        c.execute('COMMIT')
        return _photo_row(row), False
    except Exception:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    finally:
        c.close()


def get_photo(photo_id_):
    if not PHOTO_ID_RE.match(photo_id_ or ''):
        return None
    c = _conn()
    if c is None:
        return None
    try:
        r = c.execute('SELECT * FROM po_photos WHERE id = ?', (photo_id_,)).fetchone()
        return _photo_row(r) if r else None
    finally:
        c.close()


def list_photos(source_id):
    c = _conn()
    if c is None:
        return []
    try:
        rows = c.execute('SELECT * FROM po_photos WHERE source_id = ? ORDER BY page, created_at, id', (source_id,)).fetchall()
        return [_photo_row(r) for r in rows]
    finally:
        c.close()


def read_photo_bytes(photo_id_):
    """(meta, bytes) or None; re-hashed on every read like the originals."""
    if not PHOTO_ID_RE.match(photo_id_ or ''):
        return None
    c = _conn()
    if c is None:
        return None
    try:
        r = c.execute('SELECT * FROM po_photos WHERE id = ?', (photo_id_,)).fetchone()
    finally:
        c.close()
    if not r:
        return None
    stored = r['stored_name']
    if not _PHOTO_STORED_RE.match(stored):
        raise SourceIntegrityError('Unexpected stored name.')
    base = os.path.realpath(photos_dir())
    path = os.path.realpath(os.path.join(base, stored))
    if os.path.dirname(path) != base or not os.path.isfile(path):
        raise SourceIntegrityError('Stored photo is missing.')
    with open(path, 'rb') as f:
        data = f.read()
    if hashlib.sha256(data).hexdigest() != r['sha256'] or len(data) != r['size_bytes']:
        raise SourceIntegrityError('Stored photo no longer matches its recorded hash.')
    return _photo_row(r), data
