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

-- PO settings (buyer identity, numbering, approval): one row, revision-checked.
CREATE TABLE IF NOT EXISTS po_settings (
  id          INTEGER PRIMARY KEY CHECK (id = 1),
  rev         INTEGER NOT NULL,
  updated_at  TEXT NOT NULL,
  data_json   TEXT NOT NULL
);
-- next sequence number per number prefix (e.g. per year); advanced only inside an issue transaction
CREATE TABLE IF NOT EXISTS po_counters (
  key   TEXT PRIMARY KEY,
  next  INTEGER NOT NULL
);
-- one row per PO: the stable base number and which issued version is current
CREATE TABLE IF NOT EXISTS po_bases (
  id                TEXT PRIMARY KEY,
  base_no           TEXT NOT NULL UNIQUE,
  created_at        TEXT NOT NULL,
  current_issue_id  TEXT
);
CREATE TRIGGER IF NOT EXISTS po_bases_identity_u BEFORE UPDATE OF id, base_no, created_at ON po_bases
  BEGIN SELECT RAISE(ABORT, 'po_bases identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS po_bases_immutable_d BEFORE DELETE ON po_bases
  BEGIN SELECT RAISE(ABORT, 'po_bases rows are immutable'); END;
-- issued versions: frozen snapshot + the rendered PDF (content-addressed, re-hashed on read)
CREATE TABLE IF NOT EXISTS po_issues (
  id                TEXT PRIMARY KEY,
  base_id           TEXT NOT NULL,
  revision_no       INTEGER NOT NULL,
  display_no        TEXT NOT NULL UNIQUE,
  issued_at         TEXT NOT NULL,
  issued_by         TEXT NOT NULL,
  approved_by       TEXT NOT NULL,
  reason            TEXT NOT NULL,
  previous_issue_id TEXT,
  source_draft_id   TEXT NOT NULL UNIQUE,
  idempotency_key   TEXT NOT NULL UNIQUE,
  snapshot_json     TEXT NOT NULL,
  snapshot_sha256   TEXT NOT NULL,
  pdf_sha256        TEXT NOT NULL,
  pdf_size          INTEGER NOT NULL,
  pdf_stored_name   TEXT NOT NULL,
  template_version  TEXT NOT NULL,
  test_mode         INTEGER NOT NULL,
  UNIQUE (base_id, revision_no)
);
CREATE INDEX IF NOT EXISTS idx_po_issues_base ON po_issues(base_id);
CREATE TRIGGER IF NOT EXISTS po_issues_immutable_u BEFORE UPDATE ON po_issues
  BEGIN SELECT RAISE(ABORT, 'po_issues rows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS po_issues_immutable_d BEFORE DELETE ON po_issues
  BEGIN SELECT RAISE(ABORT, 'po_issues rows are immutable'); END;
-- append-only history: issued / superseded
CREATE TABLE IF NOT EXISTS po_issue_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_id  TEXT NOT NULL,
  event     TEXT NOT NULL CHECK (event IN ('issued', 'superseded')),
  at        TEXT NOT NULL,
  detail    TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS po_issue_events_u BEFORE UPDATE ON po_issue_events
  BEGIN SELECT RAISE(ABORT, 'po_issue_events rows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS po_issue_events_d BEFORE DELETE ON po_issue_events
  BEGIN SELECT RAISE(ABORT, 'po_issue_events rows are immutable'); END;
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


class DraftIssued(Exception):
    """The draft was issued; it can no longer be edited (revise the issued PO instead)."""
    def __init__(self, issue):
        super().__init__('draft already issued')
        self.issue = issue


class NotReady(Exception):
    def __init__(self, blockers):
        super().__init__('the draft cannot be issued yet')
        self.blockers = blockers


class StaleRevision(Exception):
    """The PO was issued again (or revised elsewhere) since this revision draft was made."""
    def __init__(self, current_issue):
        super().__init__('the issued PO changed since this revision was started')
        self.current = current_issue


class OpenRevisionExists(Exception):
    def __init__(self, draft_id):
        super().__init__('an open revision draft already exists')
        self.draft_id = draft_id


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
            'SELECT d.id, d.status, d.title, d.supplier_id, d.supplier_name, d.currency, d.item_count, '
            'd.created_at, d.updated_at, d.rev, i.display_no AS issued_no, '
            "json_extract(d.data_json, '$.revision.baseNo') AS rev_base "
            'FROM po_drafts d LEFT JOIN po_issues i ON i.source_draft_id = d.id '
            'ORDER BY d.updated_at DESC, d.id').fetchall()
        return [{
            'id': r['id'], 'status': 'issued' if r['issued_no'] else r['status'], 'title': r['title'] or '',
            'supplierId': r['supplier_id'], 'supplierName': r['supplier_name'],
            'currency': r['currency'] or '', 'itemCount': r['item_count'],
            'createdAt': r['created_at'], 'updatedAt': r['updated_at'], 'rev': r['rev'],
            'issuedNo': r['issued_no'], 'revisionOf': r['rev_base'],
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
    if doc.get('revision'):         # revision drafts are created only by revise_issue()
        raise ValueError('revision drafts are created from an issued PO')
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
        r = c.execute('SELECT rev, created_at, data_json FROM po_drafts WHERE id = ?', (doc['id'],)).fetchone()
        if r is None:
            c.execute('ROLLBACK')
            raise DraftGone(doc['id'])
        issued = c.execute('SELECT * FROM po_issues WHERE source_draft_id = ?', (doc['id'],)).fetchone()
        if issued:
            c.execute('ROLLBACK')
            raise DraftIssued(_issue_row(issued))
        if r['rev'] != expected_rev:
            c.execute('ROLLBACK')
            raise RevConflict(r['rev'])
        if (json.loads(r['data_json']).get('revision') or None) != (doc.get('revision') or None):
            c.execute('ROLLBACK')           # which issued PO a revision revises is fixed by the server
            raise ValueError('the revision reference of a draft cannot be changed')
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
    except (DraftGone, RevConflict, DraftIssued):
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


# ── PO settings ────────────────────────────────────────────────────────────

def get_settings():
    """(settings, rev). Defaults (nothing confirmed, rev 0) when never saved."""
    import po_model
    c = _conn()
    if c is None:
        return po_model.default_settings(), 0
    try:
        r = c.execute('SELECT data_json, rev FROM po_settings WHERE id = 1').fetchone()
        return (json.loads(r['data_json']), r['rev']) if r else (po_model.default_settings(), 0)
    finally:
        c.close()


def put_settings(doc, expected_rev):
    """Compare-and-swap save of the validated settings. Returns (doc, rev)."""
    c = _conn(create=True)
    try:
        c.execute('BEGIN IMMEDIATE')
        r = c.execute('SELECT rev FROM po_settings WHERE id = 1').fetchone()
        current = r['rev'] if r else 0
        if current != expected_rev:
            c.execute('ROLLBACK')
            raise RevConflict(current)
        ts = now_iso()
        doc = dict(doc, updatedAt=ts)
        if r:
            c.execute('UPDATE po_settings SET rev = rev + 1, updated_at = ?, data_json = ? WHERE id = 1 AND rev = ?',
                      (ts, json.dumps(doc, ensure_ascii=False), expected_rev))
        else:
            c.execute('INSERT INTO po_settings (id, rev, updated_at, data_json) VALUES (1, 1, ?, ?)',
                      (ts, json.dumps(doc, ensure_ascii=False)))
        c.execute('COMMIT')
        return doc, current + 1
    except RevConflict:
        raise
    except Exception:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    finally:
        c.close()


# ── issued POs (immutable) ─────────────────────────────────────────────────

IDEMPOTENCY_RE = re.compile(r'^[A-Za-z0-9_-]{16,64}$')
ISSUE_ID_RE = re.compile(r'^poi_[0-9a-f]{24}$')
_PDF_STORED_RE = re.compile(r'^[0-9a-f]{64}\.pdf$')


def issued_dir():
    return os.path.join(_data_dir(), 'issued')


def _issue_row(r):
    return {'id': r['id'], 'baseId': r['base_id'], 'revisionNo': r['revision_no'], 'displayNo': r['display_no'],
            'issuedAt': r['issued_at'], 'issuedBy': r['issued_by'], 'approvedBy': r['approved_by'], 'reason': r['reason'],
            'previousIssueId': r['previous_issue_id'], 'sourceDraftId': r['source_draft_id'],
            'snapshotSha256': r['snapshot_sha256'], 'pdfSha256': r['pdf_sha256'], 'pdfSize': r['pdf_size'],
            'templateVersion': r['template_version'], 'testMode': bool(r['test_mode'])}


def _write_once(directory, name, data, sha):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    if os.path.exists(path):
        with open(path, 'rb') as f:
            if hashlib.sha256(f.read()).hexdigest() != sha:
                raise SourceIntegrityError('Stored file does not match its hash.')
        return
    tmp = path + '.' + secrets.token_hex(4) + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def issue_draft(draft_id, expected_rev, idempotency_key, reason, approved_by, actor, render, supplier_check=None):
    """Issue one draft, atomically. Returns (issue_meta, duplicate).

    Inside ONE `BEGIN IMMEDIATE` transaction: the idempotency key is checked (a
    repeated request returns the issue it already created), the draft's save
    revision is checked, the draft is validated and every issuance blocker is
    re-checked against the confirmed settings, the number is allocated (a new
    base number, or the next revision number of the PO being revised — only if
    that PO's current issue is still the one the revision was started from),
    the snapshot is frozen with the hashes of the printed photos, the PDF is
    rendered from that snapshot and stored content-addressed, and the issue,
    its events and the base pointer are written. Any failure rolls everything
    back — no number is consumed and nothing is stored.

    render(doc, computed, settings, issue, photo_bytes) -> PDF bytes."""
    import po_model
    if not IDEMPOTENCY_RE.match(idempotency_key or ''):
        raise ValueError('idempotency key required')
    c = _conn(create=True)
    try:
        c.execute('BEGIN IMMEDIATE')
        dup = c.execute('SELECT * FROM po_issues WHERE idempotency_key = ?', (idempotency_key,)).fetchone()
        if dup:
            c.execute('ROLLBACK')
            if dup['source_draft_id'] != draft_id:
                raise ValueError('idempotency key already used for another draft')
            return _issue_row(dup), True
        r = c.execute('SELECT data_json, rev FROM po_drafts WHERE id = ?', (draft_id,)).fetchone()
        if r is None:
            raise DraftGone(draft_id)
        done = c.execute('SELECT * FROM po_issues WHERE source_draft_id = ?', (draft_id,)).fetchone()
        if done:
            raise DraftIssued(_issue_row(done))
        if r['rev'] != expected_rev:
            raise RevConflict(r['rev'])
        doc = po_model.validate_draft(json.loads(r['data_json']))
        computed = po_model.compute(doc)
        srow = c.execute('SELECT data_json FROM po_settings WHERE id = 1').fetchone()
        settings = json.loads(srow['data_json']) if srow else po_model.default_settings()
        blockers = po_model.readiness(doc, computed, settings)
        approver = (settings.get('approval') or {}).get('approverName', '').strip()
        approved_by = (approved_by or '').strip()
        if settings.get('approval', {}).get('required'):
            if not approved_by or approved_by.casefold() != approver.casefold():
                blockers.append({'code': 'approval_missing', 'message': f'Approval by {approver or "the configured approver"} is required to issue.', 'essential': True})
        else:
            approved_by = approved_by[:200]
        # supplier verified in Daftra for THIS draft revision (done by the caller just before issuing).
        # Real issuance requires 'verified'; with fictional TEST settings a missing / unreachable
        # supplier is recorded instead. There is no override.
        chk = supplier_check or {}
        test = bool(settings.get('testMode'))
        sup_ok = (chk.get('status') == 'verified' or (test and chk.get('status') in ('not_found', 'unavailable'))) \
            and chk.get('draftRev') == r['rev'] and chk.get('daftraId') == str((doc.get('supplier') or {}).get('daftraId') or '')
        if doc.get('supplier') and not sup_ok:
            blockers.append({'code': 'supplier_unverified', 'essential': True,
                             'message': (chk.get('detail') or 'The supplier was not verified in Daftra.') + ' Nothing was issued; the draft is unchanged.'})
        rev_info = doc.get('revision')
        reason = (reason or '').strip()
        if rev_info and len(reason) < 5:
            blockers.append({'code': 'revision_reason_missing', 'message': 'A revision needs a reason (at least 5 characters).', 'essential': True})
        if blockers:
            raise NotReady(blockers)

        ts = now_iso()
        previous = None
        if rev_info:
            base = c.execute('SELECT * FROM po_bases WHERE id = ?', (rev_info['baseId'],)).fetchone()
            if base is None or base['current_issue_id'] != rev_info['basedOnIssueId']:
                cur = c.execute('SELECT * FROM po_issues WHERE id = ?', (base['current_issue_id'] if base else '',)).fetchone()
                raise StaleRevision(_issue_row(cur) if cur else None)
            previous = c.execute('SELECT * FROM po_issues WHERE id = ?', (base['current_issue_id'],)).fetchone()
            revision_no = c.execute('SELECT MAX(revision_no) FROM po_issues WHERE base_id = ?', (base['id'],)).fetchone()[0] + 1
            base_id, base_no = base['id'], base['base_no']
        else:
            n = settings['numbering']
            year = int(ts[:4])
            # one counter per rendered prefix: a {YYYY} pattern restarts its sequence each year
            key = n['pattern'].replace('{YYYY}', '%04d' % year).replace('{YY}', '%02d' % (year % 100))
            row = c.execute('SELECT next FROM po_counters WHERE key = ?', (key,)).fetchone()
            seq = max(n['start'], row['next'] if row else 1)
            while True:
                base_no = po_model.format_po_number(n['pattern'], n['seqWidth'], year, seq)
                if not c.execute('SELECT 1 FROM po_bases WHERE base_no = ?', (base_no,)).fetchone():
                    break
                seq += 1
            c.execute('INSERT INTO po_counters (key, next) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET next = excluded.next',
                      (key, seq + 1))
            base_id, revision_no = 'pob_' + secrets.token_hex(12), 0
            c.execute('INSERT INTO po_bases (id, base_no, created_at, current_issue_id) VALUES (?,?,?,NULL)', (base_id, base_no, ts))
        display_no = po_model.revision_label(base_no, revision_no)

        # printed photos: their stored bytes, re-hashed now; the snapshot keeps each hash
        photo_bytes, photo_list = {}, []
        for a in doc.get('photos', []):
            if a['status'] == 'removed' or not a['includeInPdf'] or not a['target']:
                continue
            got = read_photo_bytes(a['photoId'])
            if not got:
                raise NotReady([{'code': 'photo_missing', 'message': f'Photo {a["photoId"]} is not stored.', 'essential': True}])
            meta, data = got
            photo_bytes[a['photoId']] = (meta['mime'], data)
            photo_list.append({'photoId': a['photoId'], 'sha256': meta['sha256'], 'mime': meta['mime'], 'sourceId': meta['sourceId'],
                               'page': meta['page'], 'region': meta['region'], 'kind': meta['kind'], 'target': a['target']})
        prev_info = None
        chg = []
        if previous is not None:
            prev_snap = json.loads(previous['snapshot_json'])
            prev_info = {'issueId': previous['id'], 'displayNo': previous['display_no'], 'issuedAt': previous['issued_at']}
            chg = po_model.changes(prev_snap['draft'], doc, prev_snap['computed'], computed)
        issue_view = {'displayNo': display_no, 'baseNo': base_no, 'revisionNo': revision_no, 'issuedAt': ts,
                      'approvedBy': approved_by, 'reason': reason, 'previous': prev_info}
        pdf = render(doc, computed, settings, issue_view, photo_bytes)
        if not isinstance(pdf, bytes) or not pdf.startswith(b'%PDF-'):
            raise ValueError('the renderer did not return a PDF')
        import po_pdf
        snapshot = {'schema': 1, 'issue': issue_view, 'issuedBy': actor, 'draft': doc, 'computed': computed,
                    'supplierCheck': supplier_check,   # Daftra re-check made just before issuing (as reported by the page)
                    'settings': {'buyer': settings['buyer'], 'approval': settings['approval'], 'numbering': settings['numbering'],
                                 'testMode': bool(settings.get('testMode'))},
                    'photos': photo_list, 'changes': chg, 'templateVersion': po_pdf.TEMPLATE_VERSION}
        snap_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        pdf_sha = hashlib.sha256(pdf).hexdigest()
        _write_once(issued_dir(), pdf_sha + '.pdf', pdf, pdf_sha)
        iid = 'poi_' + secrets.token_hex(12)
        c.execute('INSERT INTO po_issues (id, base_id, revision_no, display_no, issued_at, issued_by, approved_by, reason, '
                  'previous_issue_id, source_draft_id, idempotency_key, snapshot_json, snapshot_sha256, pdf_sha256, pdf_size, '
                  'pdf_stored_name, template_version, test_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (iid, base_id, revision_no, display_no, ts, actor, approved_by, reason, previous['id'] if previous else None,
                   draft_id, idempotency_key, snap_json, hashlib.sha256(snap_json.encode('utf-8')).hexdigest(), pdf_sha, len(pdf),
                   pdf_sha + '.pdf', po_pdf.TEMPLATE_VERSION, 1 if settings.get('testMode') else 0))
        if previous is not None:
            c.execute('INSERT INTO po_issue_events (issue_id, event, at, detail) VALUES (?,?,?,?)',
                      (previous['id'], 'superseded', ts, f'superseded by {display_no}'))
        c.execute('INSERT INTO po_issue_events (issue_id, event, at, detail) VALUES (?,?,?,?)',
                  (iid, 'issued', ts, f'issued by {actor}' + (f'; reason: {reason}' if reason else '')))
        c.execute('UPDATE po_bases SET current_issue_id = ? WHERE id = ?', (iid, base_id))
        row = c.execute('SELECT * FROM po_issues WHERE id = ?', (iid,)).fetchone()
        c.execute('COMMIT')
        return _issue_row(row), False
    except Exception:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    finally:
        c.close()


def _base_state(c, base_id):
    b = c.execute('SELECT * FROM po_bases WHERE id = ?', (base_id,)).fetchone()
    return b['current_issue_id'] if b else None


def get_issue(issue_id):
    """{meta, snapshot, current, events} or None."""
    if not ISSUE_ID_RE.match(issue_id or ''):
        return None
    c = _conn()
    if c is None:
        return None
    try:
        r = c.execute('SELECT * FROM po_issues WHERE id = ?', (issue_id,)).fetchone()
        if not r:
            return None
        ev = [{'event': x['event'], 'at': x['at'], 'detail': x['detail']} for x in
              c.execute('SELECT * FROM po_issue_events WHERE issue_id = ? ORDER BY id', (issue_id,))]
        return {'meta': _issue_row(r), 'snapshot': json.loads(r['snapshot_json']),
                'current': _base_state(c, r['base_id']) == r['id'], 'events': ev}
    finally:
        c.close()


def list_issues():
    """Issued POs: one entry per base number with every issued version (newest first)."""
    c = _conn()
    if c is None:
        return []
    try:
        bases = c.execute('SELECT * FROM po_bases ORDER BY created_at DESC, id').fetchall()
        out = []
        for b in bases:
            rows = c.execute('SELECT * FROM po_issues WHERE base_id = ? ORDER BY revision_no DESC', (b['id'],)).fetchall()
            open_rev = c.execute("SELECT d.id FROM po_drafts d LEFT JOIN po_issues i ON i.source_draft_id = d.id "
                                 "WHERE json_extract(d.data_json, '$.revision.baseId') = ? AND i.id IS NULL", (b['id'],)).fetchone()
            out.append({'baseId': b['id'], 'baseNo': b['base_no'], 'currentIssueId': b['current_issue_id'],
                        'openRevisionDraftId': open_rev['id'] if open_rev else None,
                        'issues': [dict(_issue_row(r), current=r['id'] == b['current_issue_id'],
                                        title=(json.loads(r['snapshot_json'])['draft'].get('title') or ''),
                                        supplierName=((json.loads(r['snapshot_json'])['draft'].get('supplier') or {}).get('name') or ''),
                                        gross=json.loads(r['snapshot_json'])['computed']['totals'].get('gross'),
                                        currency=json.loads(r['snapshot_json'])['draft'].get('currency') or '') for r in rows]})
        return out
    finally:
        c.close()


def issue_for_key(idempotency_key):
    if not IDEMPOTENCY_RE.match(idempotency_key or ''):
        return None
    c = _conn()
    if c is None:
        return None
    try:
        r = c.execute('SELECT * FROM po_issues WHERE idempotency_key = ?', (idempotency_key,)).fetchone()
        return _issue_row(r) if r else None
    finally:
        c.close()


def issue_for_draft(draft_id):
    c = _conn()
    if c is None:
        return None
    try:
        r = c.execute('SELECT * FROM po_issues WHERE source_draft_id = ?', (draft_id,)).fetchone()
        return _issue_row(r) if r else None
    finally:
        c.close()


def read_issue_pdf(issue_id):
    """(meta, bytes) of the PDF stored when the PO was issued — re-hashed; never re-rendered."""
    if not ISSUE_ID_RE.match(issue_id or ''):
        return None
    c = _conn()
    if c is None:
        return None
    try:
        r = c.execute('SELECT * FROM po_issues WHERE id = ?', (issue_id,)).fetchone()
    finally:
        c.close()
    if not r:
        return None
    if not _PDF_STORED_RE.match(r['pdf_stored_name']):
        raise SourceIntegrityError('Unexpected stored name.')
    base = os.path.realpath(issued_dir())
    path = os.path.realpath(os.path.join(base, r['pdf_stored_name']))
    if os.path.dirname(path) != base or not os.path.isfile(path):
        raise SourceIntegrityError('Stored PDF is missing.')
    with open(path, 'rb') as f:
        data = f.read()
    if hashlib.sha256(data).hexdigest() != r['pdf_sha256'] or len(data) != r['pdf_size']:
        raise SourceIntegrityError('Stored PDF no longer matches its recorded hash.')
    return _issue_row(r), data


def revise_issue(issue_id, new_draft_id):
    """Open a revision draft from an issued PO's frozen snapshot. Only the CURRENT
    issue can be revised, and only one open revision draft per PO. The issued
    version stays current until the revision itself is issued."""
    import po_model
    c = _conn(create=True)
    try:
        c.execute('BEGIN IMMEDIATE')
        r = c.execute('SELECT * FROM po_issues WHERE id = ?', (issue_id,)).fetchone()
        if r is None:
            raise DraftGone(issue_id)
        if _base_state(c, r['base_id']) != r['id']:
            cur = c.execute('SELECT * FROM po_issues WHERE id = ?', (_base_state(c, r['base_id']),)).fetchone()
            raise StaleRevision(_issue_row(cur) if cur else None)
        open_rev = c.execute("SELECT d.id FROM po_drafts d LEFT JOIN po_issues i ON i.source_draft_id = d.id "
                             "WHERE json_extract(d.data_json, '$.revision.baseId') = ? AND i.id IS NULL", (r['base_id'],)).fetchone()
        if open_rev:
            raise OpenRevisionExists(open_rev['id'])
        base_no = c.execute('SELECT base_no FROM po_bases WHERE id = ?', (r['base_id'],)).fetchone()['base_no']
        snap = json.loads(r['snapshot_json'])
        doc = dict(snap['draft'])
        ts = now_iso()
        doc.update({'id': new_draft_id, 'status': 'draft', 'createdAt': ts, 'updatedAt': ts,
                    'revision': {'baseId': r['base_id'], 'baseNo': base_no, 'basedOnIssueId': r['id'], 'basedOnRevision': r['revision_no']},
                    'reconciliation': dict(doc.get('reconciliation') or {}, acceptedTotals=None, acceptedAt='')})
        doc = po_model.validate_draft(doc)
        title, sid, sname, cur, n = _summary_cols(doc)
        c.execute('INSERT INTO po_drafts (id, status, title, supplier_id, supplier_name, currency, item_count, '
                  'created_at, updated_at, schema_ver, rev, data_json) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)',
                  (new_draft_id, 'draft', title, sid, sname, cur, n, ts, ts, doc.get('schema', 1), json.dumps(doc, ensure_ascii=False)))
        c.execute('COMMIT')
        return doc, 1
    except Exception:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    finally:
        c.close()
