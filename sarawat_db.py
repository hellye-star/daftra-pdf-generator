"""
Vista Platform — Sarawat Quote Generator local database layer.

Mirrors tp_db.py's design exactly (same reasoning applies): each SAVED
QUOTATION is stored as ONE ROW holding its full JSON document verbatim —
a quotation series (stable internal id + printed quotation number) plus
an append-only revisions[] array. A handful of columns (no/client/
updated_at) are lifted out purely for listing/sorting — never a second
source of truth; the row's data_json is always authoritative.

Data lives under ~/.vista-platform/sarawat-quotations/, independent of
the Technical Proposal database (~/.vista-platform/technical-proposals/)
— a Sarawat quotation is not a technical-proposal project and must not
be forced into that table's shape.

Document shape (verbatim, as written by the browser):
  { id, no, client, clientVat, clientCr, createdAt, updatedAt,
    currentRevision, source, importKind,
    revisions: [ { rev, createdAt, label, quoteDate, client, clientVat,
      clientCr, vatRate, deliveryDays, globalMarkupPct, supplierNotes,
      items: [ { itemId, sno, itemName, description, photo, photoSize,
        tiers:[{qty,supplierUnitPrice,manualUnitPrice,unitPrice,total}] } ],
      subtotal, vatAmount, grandTotal } ],
    deliveries: [ ... ] }   # same shape as project.deliveries[] in tp_db.py

`client`/`clientVat`/`clientCr` at the top level are a cached copy of the
LATEST revision's values, kept only for the list view — reopening an
older revision always reads that revision's own snapshot, never the
top-level copy, so an old revision reproduces exactly what it was saved
with even after later revisions change the client.

Phase 2 adds quotation_index (see below): a derived, rebuildable summary
table used only by the Saved Quotations browser. It never replaces or
duplicates the documents.
"""
import base64
import datetime
import json
import os
import re
import sqlite3
import unicodedata

_DATA_DIR = os.path.join(os.path.expanduser('~'), '.vista-platform', 'sarawat-quotations')
_DB_PATH = os.path.join(_DATA_DIR, 'sarawat-quotations.db')

_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS quotations (
  id          TEXT PRIMARY KEY,
  no          TEXT,
  client      TEXT,
  created_at  TEXT,
  updated_at  TEXT NOT NULL,
  schema_ver  INTEGER NOT NULL DEFAULT 1,
  data_json   TEXT NOT NULL
);

-- Phase 2 (Saved Quotations browser): a lightweight, DERIVED index of the
-- quotations table so the list/search/filter/sort never has to load or ship
-- full documents (which carry base64 photos). It is NOT a second store:
-- every column is recomputed from quotations.data_json (which stays the only
-- source of truth), it is rewritten in the same transaction as every save, and
-- it can be dropped and rebuilt at any time without losing anything.
CREATE TABLE IF NOT EXISTS quotation_index (
  id            TEXT PRIMARY KEY,
  idx_ver       INTEGER NOT NULL,
  src_updated   TEXT,              -- quotations.updated_at this row was built from (staleness check)
  no            TEXT,
  client        TEXT,
  quote_date    TEXT,              -- as typed on the quotation
  sort_date     TEXT,              -- ISO yyyy-mm-dd: parsed quote date, else the saved date
  created_at    TEXT,
  updated_at    TEXT,
  grand_total   REAL,
  item_count    INTEGER,
  rev           INTEGER,           -- latest revision number
  rev_count     INTEGER,
  status        TEXT,              -- quoted | partial | delivered (derived from deliveries[])
  source        TEXT,              -- manual | pdf | excel | image | saved_items | duplicate
  photo_count   INTEGER,
  preview_json  TEXT,              -- first item names of the latest revision
  search_text   TEXT,              -- normalised searchable text of the latest revision
  qstatus       TEXT,              -- manual quotation status (draft|sent|approved|cancelled|'' = not set)
  items_json    TEXT               -- lightweight per-item summaries of the latest revision (item picker)
);
CREATE INDEX IF NOT EXISTS idx_qi_sort_date ON quotation_index(sort_date);
CREATE INDEX IF NOT EXISTS idx_qi_updated   ON quotation_index(updated_at);
CREATE INDEX IF NOT EXISTS idx_qi_source    ON quotation_index(source);
CREATE INDEX IF NOT EXISTS idx_qi_status    ON quotation_index(status);
"""

# Bump when the derivation logic below changes — every index row built with an
# older version is rebuilt (from data_json) on the next start.
INDEX_VER = 2
PREVIEW_ITEMS = 4

SOURCE_LABELS = {'manual': 'Manual', 'pdf': 'PDF Import', 'excel': 'Excel Import',
                 'image': 'Image Import', 'saved_items': 'Saved Items', 'duplicate': 'Duplicate'}
STATUS_LABELS = {'quoted': 'Quoted', 'partial': 'Partly delivered', 'delivered': 'Delivered'}
# Manual quotation status (Phase 3) — independent of the delivery-derived status above.
# Stored on the quotation document as `quotationStatus`; absent = not set (older quotations).
QSTATUS_LABELS = {'draft': 'Draft', 'sent': 'Sent', 'approved': 'Approved', 'cancelled': 'Cancelled'}


def _conn():
    os.makedirs(_DATA_DIR, exist_ok=True)
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = _conn()
    try:
        c.executescript(_SCHEMA)
        _ensure_index_columns(c)
        c.commit()
        rebuild_index(c, only_stale=True)
    finally:
        c.close()


def _ensure_index_columns(c):
    """Backward-compatible migration: an index table created by Phase 2 lacks the Phase 3
    columns. They are added (nullable) in place; the INDEX_VER bump then rebuilds every row."""
    have = {r[1] for r in c.execute("PRAGMA table_info(quotation_index)")}
    for col in ('qstatus', 'items_json'):
        if col not in have:
            c.execute('ALTER TABLE quotation_index ADD COLUMN ' + col + ' TEXT')


def db_path():
    return _DB_PATH


def _now_iso():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


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


def list_quotations():
    c = _conn()
    try:
        rows = c.execute("SELECT data_json FROM quotations ORDER BY updated_at DESC").fetchall()
        return [json.loads(r['data_json']) for r in rows]
    finally:
        c.close()


def get_quotation(quotation_id):
    c = _conn()
    try:
        row = c.execute("SELECT data_json FROM quotations WHERE id=?", (quotation_id,)).fetchone()
        return json.loads(row['data_json']) if row else None
    finally:
        c.close()


def quotation_exists(quotation_id):
    c = _conn()
    try:
        return c.execute("SELECT 1 FROM quotations WHERE id=?", (quotation_id,)).fetchone() is not None
    finally:
        c.close()


def upsert_quotation(doc: dict):
    """Store the quotation document VERBATIM as JSON. id/no/client/updatedAt
    are also lifted into columns purely for listing. The derived browse index
    is refreshed in the same transaction (an index failure never blocks a save:
    it is logged and the row is simply rebuilt on the next start)."""
    qid = doc.get('id')
    if not qid:
        raise ValueError('quotation id is required')
    c = _conn()
    try:
        created_at = _stringify_ts(doc.get('createdAt'))
        updated_at = _stringify_ts(doc.get('updatedAt') or doc.get('createdAt')) or _now_iso()
        c.execute("""
            INSERT INTO quotations (id, no, client, created_at, updated_at, schema_ver, data_json)
            VALUES (:id, :no, :client, :created_at, :updated_at, :schema_ver, :data_json)
            ON CONFLICT(id) DO UPDATE SET
              no=excluded.no, client=excluded.client, updated_at=excluded.updated_at,
              schema_ver=excluded.schema_ver, data_json=excluded.data_json
        """, {
            'id': qid,
            'no': doc.get('no'),
            'client': doc.get('client'),
            'created_at': created_at,
            'updated_at': updated_at,
            'schema_ver': int(doc.get('schemaVer') or 1),
            'data_json': json.dumps(doc, ensure_ascii=False),
        })
        try:
            _write_index_row(c, doc, created_at, updated_at)
        except Exception as e:  # defensive — never block a save on the index
            print('[sarawat_db] index refresh failed for', qid, repr(e))
        c.commit()
    finally:
        c.close()


def update_meta(quotation_id, changes):
    """Header-level corrections and the manual status, applied atomically on the server so a
    quick edit never re-uploads (or races with) a whole document.

    changes may hold: client, no, quotationStatus.
      * quotationStatus / no are series-level fields — no revision snapshot contains them.
      * client lives in each revision snapshot; only the LATEST revision (the current one)
        is corrected, older revisions are never touched.
    Nothing is overwritten silently: every change appends {at, field, from, to[, rev]} to the
    document's append-only `corrections[]` (client / number) or `statusLog[]` (status).
    Items, prices, totals, photos, revision numbers and deliveries are untouched.
    Returns the updated document, or None if the quotation does not exist."""
    doc = get_quotation(quotation_id)
    if doc is None:
        return None
    now = int(datetime.datetime.utcnow().timestamp() * 1000)
    revs = doc.get('revisions') or []
    latest = revs[-1] if revs else None
    changed = False

    def clean(v, label):
        v = ' '.join(str(v).split())
        if len(v) > 200:
            raise ValueError(label + ' is too long (max 200 characters)')
        return v

    if 'client' in changes and latest is not None:
        new = clean(changes['client'], 'client')
        old = str(latest.get('client') or doc.get('client') or '')
        if new != old:
            doc.setdefault('corrections', []).append({'at': now, 'field': 'client', 'from': old, 'to': new, 'rev': latest.get('rev')})
            latest['client'] = new
            doc['client'] = new
            changed = True
    if 'no' in changes:
        new = clean(changes['no'], 'quotation number')
        old = str(doc.get('no') or '')
        if new != old:
            doc.setdefault('corrections', []).append({'at': now, 'field': 'quotationNo', 'from': old, 'to': new})
            doc['no'] = new
            changed = True
    if 'quotationStatus' in changes:
        new = changes['quotationStatus']
        if new not in QSTATUS_LABELS:
            raise ValueError('quotationStatus must be one of: ' + ', '.join(QSTATUS_LABELS))
        old = doc.get('quotationStatus') or ''
        if new != old:
            doc.setdefault('statusLog', []).append({'at': now, 'from': old, 'to': new})
            doc['quotationStatus'] = new
            changed = True
    if changed:
        doc['updatedAt'] = now
        upsert_quotation(doc)
    return doc


def delete_quotation(quotation_id):
    c = _conn()
    try:
        c.execute("DELETE FROM quotations WHERE id=?", (quotation_id,))
        c.execute("DELETE FROM quotation_index WHERE id=?", (quotation_id,))
        c.commit()
    finally:
        c.close()


def counts():
    c = _conn()
    try:
        revs = 0
        for (dj,) in c.execute("SELECT data_json FROM quotations"):
            try:
                revs += len(json.loads(dj).get('revisions') or [])
            except ValueError:
                pass
        return {
            'quotations': c.execute("SELECT COUNT(*) FROM quotations").fetchone()[0],
            'revisions': revs,
            'indexed': c.execute("SELECT COUNT(*) FROM quotation_index").fetchone()[0],
        }
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════════════════
# Browse index (Phase 2) — everything below only READS data_json and writes the
# derived quotation_index table. It never modifies a quotation document.
# ══════════════════════════════════════════════════════════════════════════════

_ARABIC_MARKS = re.compile('[ـً-ٰٟ]')   # tatweel + harakat
_WS = re.compile(r'\s+')


def norm_text(s):
    """Search normalisation shared by the index and the query: NFKC (turns the
    Arabic presentation forms found in PDF-extracted client names back into
    ordinary letters), lower-case, harakat/tatweel stripped, whitespace folded."""
    if s is None:
        return ''
    s = unicodedata.normalize('NFKC', str(s)).casefold()
    s = _ARABIC_MARKS.sub('', s)
    return _WS.sub(' ', s).strip()


_MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']


def parse_quote_date(raw):
    """Best-effort ISO date from the free-text quotation date ('7-october-2026',
    '9/15/2026', '07/01/2027', '2026-10-07'). Ambiguous d/m vs m/d is read
    day-first unless only month-first is valid. Returns '' if not understood."""
    s = str(raw or '').strip().lower()
    if not s:
        return ''
    try:
        m = re.match(r'^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$', s)
        if m:
            y, mo, d = int(m[1]), int(m[2]), int(m[3])
            return datetime.date(y, mo, d).isoformat()
        m = re.match(r'^(\d{1,2})[-/. ]+(\d{1,2})[-/. ]+(\d{2,4})$', s)
        if m:
            a, b, y = int(m[1]), int(m[2]), int(m[3])
            if y < 100:
                y += 2000
            d, mo = (a, b) if b <= 12 else (b, a)          # day-first unless impossible
            return datetime.date(y, mo, d).isoformat()
        m = re.match(r'^(\d{1,2})[-/. ]+([a-z]+)[-/. ,]+(\d{4})$', s)
        if m and m[2][:3] in _MONTHS:
            return datetime.date(int(m[3]), _MONTHS.index(m[2][:3]) + 1, int(m[1])).isoformat()
        m = re.match(r'^([a-z]+)[-/. ]+(\d{1,2})[-/. ,]+(\d{4})$', s)
        if m and m[1][:3] in _MONTHS:
            return datetime.date(int(m[3]), _MONTHS.index(m[1][:3]) + 1, int(m[2])).isoformat()
    except (ValueError, TypeError):
        pass
    return ''


def _derive_source(doc, revs):
    src = doc.get('source')
    if src == 'manual_existing':
        return 'manual'
    if src in ('saved_items', 'duplicate'):
        return src
    kind = doc.get('importKind')               # written by newer saves
    if kind in ('pdf', 'excel', 'image'):
        return kind
    # Older uploads did not record the file type. Spreadsheet imports always carry
    # a supplier-price currency (or the "Currency: …workbook" notice); PDFs never do.
    for r in revs:
        if r.get('sourceCurrency') or re.match(r'^currency:.*workbook', str(r.get('supplierNotes') or ''), re.I):
            return 'excel'
    return 'pdf'


def _derive_status(doc, latest_rev_no):
    recs = doc.get('deliveries') or []
    cur = next((d for d in recs if d.get('quotationRev') == latest_rev_no), None)
    pool = [cur] if cur else [d for d in recs if (d.get('events') or [])]
    if any(d.get('status') == 'closed' for d in pool):
        return 'delivered'
    if any((d.get('events') or []) for d in pool):
        return 'partial'
    return 'quoted'


def iter_photos(rev):
    """Flat, ordered list of a revision's photos (main photo then extras, item by
    item). The thumbnail endpoint addresses photos by position in this list."""
    out = []
    for it in rev.get('items') or []:
        if it.get('photo'):
            out.append(it['photo'])
        for ph in it.get('extraPhotos') or []:
            if ph:
                out.append(ph)
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _item_summaries(rev):
    """Small per-item records of a revision for the saved-item picker: no photos, only the
    position of the main photo in iter_photos() order (-1 = none) so a thumbnail can be
    requested for it. `q` is the normalised searchable text of the item."""
    out, pos = [], 0
    for i, it in enumerate(rev.get('items') or []):
        ph = -1
        if it.get('photo'):
            ph = pos
            pos += 1
        pos += sum(1 for x in (it.get('extraPhotos') or []) if x)
        name = str(it.get('itemName') or '').strip()
        desc = str(it.get('description') or '')
        if not (name or desc.strip()):
            continue
        tiers = it.get('tiers') or []
        t0 = tiers[0] if tiers else {}
        out.append({
            'i': i, 'sno': str(it.get('sno') or ''), 'name': name,
            'spec': _WS.sub(' ', desc).strip()[:220],
            'qty': _num(t0.get('qty')), 'unit': _num(t0.get('unitPrice')), 'tiers': len(tiers), 'ph': ph,
            'q': norm_text(' '.join([str(it.get('sno') or ''), name, desc])),
        })
    return out


def _index_values(doc, created_at, updated_at):
    revs = doc.get('revisions') or []
    rev = revs[-1] if revs else {}
    items = rev.get('items') or []
    names = [str(it.get('itemName')).strip() for it in items if str(it.get('itemName') or '').strip()]
    parts = [doc.get('no'), rev.get('client') or doc.get('client'), rev.get('quoteDate')]
    for it in items:
        parts += [it.get('sno'), it.get('itemName'), it.get('description')]
    source = _derive_source(doc, revs)
    status = _derive_status(doc, rev.get('rev'))
    iso = parse_quote_date(rev.get('quoteDate'))
    qstatus = doc.get('quotationStatus') if doc.get('quotationStatus') in QSTATUS_LABELS else ''
    parts += [iso, SOURCE_LABELS.get(source), STATUS_LABELS.get(status), QSTATUS_LABELS.get(qstatus)]
    sort_date = iso or (created_at or updated_at or '')[:10]
    try:
        total = float(rev.get('grandTotal') or 0)
    except (TypeError, ValueError):
        total = 0.0
    return {
        'id': doc['id'], 'idx_ver': INDEX_VER, 'src_updated': updated_at,
        'no': str(doc.get('no') or ''), 'client': str(rev.get('client') or doc.get('client') or ''),
        'quote_date': str(rev.get('quoteDate') or ''), 'sort_date': sort_date,
        'created_at': created_at, 'updated_at': updated_at,
        'grand_total': total, 'item_count': len(items),
        'rev': rev.get('rev') if rev.get('rev') is not None else 0, 'rev_count': len(revs),
        'status': status, 'source': source, 'photo_count': len(iter_photos(rev)),
        'preview_json': json.dumps(names[:PREVIEW_ITEMS], ensure_ascii=False),
        'search_text': norm_text(' \n '.join(str(p) for p in parts if p)),
        'qstatus': qstatus,
        'items_json': json.dumps(_item_summaries(rev), ensure_ascii=False),
    }


_INDEX_COLS = ['id', 'idx_ver', 'src_updated', 'no', 'client', 'quote_date', 'sort_date', 'created_at',
               'updated_at', 'grand_total', 'item_count', 'rev', 'rev_count', 'status', 'source',
               'photo_count', 'preview_json', 'search_text', 'qstatus', 'items_json']


def _write_index_row(c, doc, created_at, updated_at):
    v = _index_values(doc, created_at, updated_at)
    c.execute('INSERT OR REPLACE INTO quotation_index (' + ','.join(_INDEX_COLS) + ') VALUES (' +
              ','.join(':' + k for k in _INDEX_COLS) + ')', v)


def rebuild_index(c=None, only_stale=False):
    """(Re)build quotation_index from data_json. Idempotent; reads quotations,
    writes only the index. only_stale=True rebuilds just rows that are missing,
    built by an older INDEX_VER, or built from an older updated_at; index rows
    whose quotation no longer exists are dropped. Returns rows (re)built."""
    own = c is None
    if own:
        c = _conn()
    built = 0
    try:
        c.execute("DELETE FROM quotation_index WHERE id NOT IN (SELECT id FROM quotations)")
        if only_stale:
            rows = c.execute("""
                SELECT q.id, q.created_at, q.updated_at, q.data_json FROM quotations q
                LEFT JOIN quotation_index x ON x.id = q.id
                WHERE x.id IS NULL OR x.idx_ver != ? OR IFNULL(x.src_updated,'') != IFNULL(q.updated_at,'')
            """, (INDEX_VER,)).fetchall()
        else:
            rows = c.execute("SELECT id, created_at, updated_at, data_json FROM quotations").fetchall()
        for r in rows:
            try:
                doc = json.loads(r['data_json'])
                doc.setdefault('id', r['id'])
                _write_index_row(c, doc, r['created_at'], r['updated_at'])
                built += 1
            except Exception as e:  # one bad row must not stop the rest
                print('[sarawat_db] could not index', r['id'], repr(e))
        c.commit()
    finally:
        if own:
            c.close()
    return built


def search_quotations(q='', source='', status='', client='', date_from='', date_to='',
                      recent_days=0, sort='newest', limit=200, offset=0, qstatus=''):
    """Filtered/sorted lightweight summaries straight from quotation_index —
    never touches data_json. Every search word must match (in any field)."""
    where, args = [], []
    for tok in norm_text(q).split(' '):
        if tok:
            esc = tok.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where.append("search_text LIKE ? ESCAPE '\\'")
            args.append('%' + esc + '%')
    if source:
        where.append('source = ?'); args.append(source)
    if status:
        where.append('status = ?'); args.append(status)
    if client:
        where.append('client = ?'); args.append(client)
    if qstatus:
        where.append('qstatus = ?'); args.append(qstatus)
    if date_from:
        where.append('sort_date >= ?'); args.append(date_from)
    if date_to:
        where.append('sort_date <= ?'); args.append(date_to)
    if recent_days:
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=int(recent_days))).strftime('%Y-%m-%dT%H:%M:%SZ')
        where.append("(IFNULL(updated_at,'') >= ? OR IFNULL(created_at,'') >= ?)"); args += [cutoff, cutoff]
    order = {
        'newest':   'created_at DESC, updated_at DESC',
        'oldest':   'created_at ASC, updated_at ASC',
        'client':   "(client = '') ASC, client COLLATE NOCASE ASC, created_at DESC",
        'highest':  'grand_total DESC, created_at DESC',
        'lowest':   'grand_total ASC, created_at DESC',
        'modified': 'updated_at DESC, created_at DESC',
    }.get(sort, 'created_at DESC, updated_at DESC')
    sql_where = (' WHERE ' + ' AND '.join(where)) if where else ''
    c = _conn()
    try:
        total = c.execute('SELECT COUNT(*) FROM quotation_index' + sql_where, args).fetchone()[0]
        rows = c.execute('SELECT id,no,client,quote_date,sort_date,created_at,updated_at,grand_total,item_count,'
                         'rev,rev_count,status,source,photo_count,preview_json,qstatus FROM quotation_index' +
                         sql_where + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?',
                         args + [max(1, min(int(limit), 500)), max(0, int(offset))]).fetchall()
        items = []
        for r in rows:
            names = json.loads(r['preview_json'] or '[]')
            items.append({
                'id': r['id'], 'no': r['no'], 'client': r['client'], 'quoteDate': r['quote_date'],
                'sortDate': r['sort_date'], 'createdAt': r['created_at'], 'updatedAt': r['updated_at'],
                'grandTotal': r['grand_total'], 'itemCount': r['item_count'], 'rev': r['rev'],
                'revCount': r['rev_count'], 'status': r['status'],
                'statusLabel': STATUS_LABELS.get(r['status'], r['status']),
                'qstatus': r['qstatus'] or '', 'qstatusLabel': QSTATUS_LABELS.get(r['qstatus'] or '', ''),
                'source': r['source'], 'sourceLabel': SOURCE_LABELS.get(r['source'], r['source']),
                'photoCount': r['photo_count'], 'preview': names,
                'moreItems': max(0, r['item_count'] - len(names)),
            })
        facets = {
            'clients': [r[0] for r in c.execute(
                "SELECT DISTINCT client FROM quotation_index WHERE client != '' ORDER BY client COLLATE NOCASE")],
            'sources': {r[0]: r[1] for r in c.execute('SELECT source, COUNT(*) FROM quotation_index GROUP BY source')},
            'statuses': {r[0]: r[1] for r in c.execute('SELECT status, COUNT(*) FROM quotation_index GROUP BY status')},
            'qstatuses': {r[0]: r[1] for r in c.execute(
                "SELECT qstatus, COUNT(*) FROM quotation_index WHERE IFNULL(qstatus,'') != '' GROUP BY qstatus")},
            'all': c.execute('SELECT COUNT(*) FROM quotation_index').fetchone()[0],
        }
        return {'items': items, 'total': total, 'facets': facets}
    finally:
        c.close()


def search_items(q='', limit=60):
    """Saved-item picker search, served from quotation_index only (no documents, no photos).
    Every word must match the item's own text (code, name, specification) or its quotation's
    number / client / date. Identical code+name+specification appears once, newest quotation
    first. Returns {items, matched, truncated}."""
    toks = [t for t in norm_text(q).split(' ') if t]
    limit = max(1, min(int(limit), 200))
    c = _conn()
    try:
        rows = c.execute('SELECT id,no,client,quote_date,updated_at,rev,items_json FROM quotation_index '
                         'ORDER BY created_at DESC, updated_at DESC').fetchall()
    finally:
        c.close()
    seen, out, matched = set(), [], 0
    for r in rows:
        head = norm_text(' '.join([r['no'] or '', r['client'] or '', r['quote_date'] or '']))
        try:
            items = json.loads(r['items_json'] or '[]')
        except ValueError:
            continue
        for it in items:
            key = (it['sno'], it['name'], it['spec'])
            if key in seen:
                continue
            hay = head + ' ' + it['q']
            if not all(t in hay for t in toks):
                continue
            seen.add(key)
            matched += 1
            if len(out) < limit:
                out.append({'qid': r['id'], 'i': it['i'], 'sno': it['sno'], 'name': it['name'], 'spec': it['spec'],
                            'qty': it['qty'], 'unit': it['unit'], 'tiers': it['tiers'], 'ph': it['ph'],
                            'no': r['no'] or '', 'client': r['client'] or '', 'quoteDate': r['quote_date'] or '',
                            'rev': r['rev'], 'v': r['updated_at']})
    return {'items': out, 'matched': matched, 'truncated': matched > len(out)}


def get_items(quotation_id, indexes):
    """Full items (with photos) of a quotation's LATEST revision by position — fetched only
    for the items the user actually picked. Returns [] for unknown quotations."""
    doc = get_quotation(quotation_id)
    if not doc or not doc.get('revisions'):
        return []
    items = doc['revisions'][-1].get('items') or []
    return [{'i': i, 'item': items[i]} for i in indexes if 0 <= i < len(items)]


_THUMB_CACHE = {}          # (id, n, updated_at) -> (bytes, mime); a few KB each
_THUMB_CACHE_MAX = 400


def get_thumbnail(quotation_id, n, size=120):
    """Small JPEG (or the original bytes if it cannot be re-encoded) for the n-th
    photo of the quotation's latest revision. Returns (bytes, mime, updated_at) or None.
    Decoded once per (quotation, photo, updated_at) and kept in memory — a revised
    quotation has a new updated_at, so it can never serve a stale picture."""
    c = _conn()
    try:
        row = c.execute("SELECT updated_at FROM quotations WHERE id=?", (quotation_id,)).fetchone()
        if not row:
            return None
        hit = _THUMB_CACHE.get((quotation_id, n, row['updated_at']))
        if hit:
            return hit[0], hit[1], row['updated_at']
        row = c.execute("SELECT data_json, updated_at FROM quotations WHERE id=?", (quotation_id,)).fetchone()
    finally:
        c.close()
    if not row:
        return None
    res = _make_thumbnail(row['data_json'], n, size)
    if res is None:
        return None
    if len(_THUMB_CACHE) >= _THUMB_CACHE_MAX:
        _THUMB_CACHE.pop(next(iter(_THUMB_CACHE)))
    _THUMB_CACHE[(quotation_id, n, row['updated_at'])] = res
    return res[0], res[1], row['updated_at']


def _make_thumbnail(data_json, n, size):
    doc = json.loads(data_json)
    revs = doc.get('revisions') or []
    photos = iter_photos(revs[-1]) if revs else []
    if n < 0 or n >= len(photos):
        return None
    m = re.match(r'^data:(image/([a-z0-9.+-]+));base64,(.*)$', photos[n], re.S | re.I)
    if not m:
        return None
    mime, ext, raw = m.group(1).lower(), m.group(2).lower(), base64.b64decode(m.group(3))
    try:
        import fitz   # PyMuPDF — already a dependency of the purchasing tools
        d = fitz.open(stream=raw, filetype=ext)
        try:
            page = d[0]
            scale = min(1.0, size / float(max(page.rect.width, page.rect.height)))
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)   # transparent -> white
            return pix.tobytes('jpeg', 80), 'image/jpeg'
        finally:
            d.close()
    except Exception:
        return raw, mime
