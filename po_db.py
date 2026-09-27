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

Phase 1 scope: drafts only (create / read / list / update with revision
check). No delete, no issuance, no source files. Reserved for later phases
and intentionally NOT created yet: source_files (original documents,
hash-addressed, never modified), extractions (immutable evidence), and
issued_pos (frozen snapshot, UNIQUE po_no, UNIQUE idempotency key, allocated
atomically inside one BEGIN IMMEDIATE transaction).

Revision protection mirrors tp_db.py: every draft has an integer `rev`; an
update must name the rev it was loaded at and succeeds only via
UPDATE ... WHERE id=? AND rev=?, so a stale tab can never overwrite a newer
save.
"""
import datetime
import json
import os
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
"""


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
