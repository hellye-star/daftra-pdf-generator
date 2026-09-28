"""
Vista Platform — PO Generator storage API handlers.

Same shape as tp_storage_api.py. proxy.py binds 127.0.0.1, which is the
platform's access-control convention; because the proxy process can also be
reached through a local tunnel (the WhatsApp cloudflared task, currently
disabled) every PO request is additionally checked here:
    * client address must be loopback
    * Host header must be localhost / 127.0.0.1 (blocks DNS rebinding)
    * requests carrying proxy/tunnel forwarding headers are refused
    * writes must be same-origin (Origin / Sec-Fetch-Site) and JSON
No CORS headers are ever added — the PO page is same-origin.

Phase 1 endpoints:
    GET  /api/po/status
    GET  /api/po/drafts                  → summaries, newest first
    GET  /api/po/drafts/<id>             → {data, rev, computed, readiness}
    POST /api/po/drafts                  create {draft}; 409 if the id exists
    PUT  /api/po/drafts/<id>             REQUIRES If-Match: <rev>
                                           200 saved · 409 stale · 410 gone · 428 no rev
                                           400 {errors:[{path,message}]} invalid
No delete and no issuance endpoints in Phase 1.

Phase 2 endpoints (original documents + extraction results, all insert-only):
    POST /api/po/sources?draftId=<id>&name=<file name>
                                         raw bytes; Content-Type application/pdf | image/png | image/jpeg;
                                         ≤ 25 MB; leading bytes must match the type → 201 {meta} (200 if the
                                         same bytes were already uploaded for this draft)
    GET  /api/po/sources?draftId=<id>    → list of source metadata
    GET  /api/po/sources/<id>            → metadata
    GET  /api/po/sources/<id>/file       → the original bytes, as a DOWNLOAD (attachment, nosniff,
                                           sandbox CSP); re-hashed on every read; 500 if it changed
    POST /api/po/extractions             {sourceId, engine, result} ≤ 8 MB → 201 {id, summary}
    GET  /api/po/extractions?sourceId=<id>  → summaries
    GET  /api/po/extractions/<id>        → the stored result, exactly as saved
No endpoint updates or deletes a source or an extraction.

Item photos (image regions of an original document, insert-only):
    POST /api/po/photos?sourceId=<id>&page=<n>&x=&y=&w=&h=&kind=embedded|source_crop[&extractionId=<id>]
                                         raw PNG/JPEG ≤ 5 MB → 201 {meta}; the same bytes again → 200 with the
                                         stored record; DIFFERENT bytes for a stored photo → 409 photo_conflict
                                         with the stored record (never replaced)
    GET  /api/po/photos?sourceId=<id>    → list of photo metadata
    GET  /api/po/photos/<id>/file        → the image (inline, nosniff, sandbox CSP); re-hashed on every read
Which item a photo belongs to is part of the draft (draft.photos), saved with
the draft's revision check.

Issuance (explicit; never sends anything to a supplier):
    GET  /api/po/settings                → {data, rev} buyer details, numbering, approval (+ confirmations)
    PUT  /api/po/settings                REQUIRES If-Match: <rev>
    GET  /api/po/drafts/<id>/preview.pdf → draft PDF (watermarked DRAFT, rendered on demand, not stored)
    GET  /api/po/drafts/<id>/changes     → revision drafts: before/after list vs the issued version
    POST /api/po/drafts/<id>/issue       {idempotencyKey, approvedBy, reason} + If-Match: <rev>
                                           201 issued · 200 same key again (the same issue) · 409 stale /
                                           already issued / PO revised elsewhere · 422 {blockers}
    GET  /api/po/issues                  → every PO with all issued versions
    GET  /api/po/issues/<id>             → {meta, snapshot, current, events}
    GET  /api/po/issues/<id>/pdf         → the PDF stored at issue (re-hashed; never re-rendered)
    POST /api/po/issues/<id>/revise      {draftId} → 201 new revision draft of the CURRENT issue
"""
import json
import re
from urllib.parse import urlparse, parse_qs, quote

import po_db
import po_model

# No storage is opened or created at import time — po_db initialises lazily
# on the first write (see po_db._conn).

MAX_BODY = 2 * 1024 * 1024
MAX_EXTRACTION_BODY = 8 * 1024 * 1024
_JSON = ('application/json',)
_BINARY = tuple(po_db.SOURCE_TYPES)
_ID = r'(?P<id>po_[A-Za-z0-9]{10,40})'
_SRC = r'(?P<id>src_[0-9a-f]{24})'
_EX = r'(?P<id>ex_[0-9a-f]{24})'
_PH = r'(?P<id>ph_[0-9a-f]{24})'
_ISS = r'(?P<id>poi_[0-9a-f]{24})'
_LOOPBACK = {'127.0.0.1', '::1', '::ffff:127.0.0.1'}
_LOCAL_HOSTNAMES = {'127.0.0.1', 'localhost', '[::1]'}
_FORWARD_HEADERS = ('X-Forwarded-For', 'X-Forwarded-Host', 'Forwarded', 'X-Real-IP',
                    'CF-Connecting-IP', 'CF-Ray', 'True-Client-IP')

_GET = [
    (re.compile(r'^/api/po/status$'), '_status'),
    (re.compile(r'^/api/po/drafts$'), '_list_drafts'),
    (re.compile(r'^/api/po/drafts/' + _ID + r'$'), '_get_draft'),
    (re.compile(r'^/api/po/sources$'), '_list_sources'),
    (re.compile(r'^/api/po/sources/' + _SRC + r'$'), '_get_source'),
    (re.compile(r'^/api/po/sources/' + _SRC + r'/file$'), '_get_source_file'),
    (re.compile(r'^/api/po/extractions$'), '_list_extractions'),
    (re.compile(r'^/api/po/extractions/' + _EX + r'$'), '_get_extraction'),
    (re.compile(r'^/api/po/photos$'), '_list_photos'),
    (re.compile(r'^/api/po/photos/' + _PH + r'/file$'), '_get_photo_file'),
    (re.compile(r'^/api/po/settings$'), '_get_settings'),
    (re.compile(r'^/api/po/drafts/' + _ID + r'/preview\.pdf$'), '_get_preview_pdf'),
    (re.compile(r'^/api/po/drafts/' + _ID + r'/changes$'), '_get_changes'),
    (re.compile(r'^/api/po/issues$'), '_list_issues'),
    (re.compile(r'^/api/po/issues/' + _ISS + r'$'), '_get_issue'),
    (re.compile(r'^/api/po/issues/' + _ISS + r'/pdf$'), '_get_issue_pdf'),
]
_POST = [  # (pattern, handler, accepted request content types)
    (re.compile(r'^/api/po/drafts$'), '_create_draft', _JSON),
    (re.compile(r'^/api/po/sources$'), '_post_source', _BINARY),
    (re.compile(r'^/api/po/extractions$'), '_post_extraction', _JSON),
    (re.compile(r'^/api/po/photos$'), '_post_photo', tuple(po_db.PHOTO_TYPES)),
    (re.compile(r'^/api/po/drafts/' + _ID + r'/issue$'), '_issue_draft', _JSON),
    (re.compile(r'^/api/po/issues/' + _ISS + r'/revise$'), '_revise_issue', _JSON),
]
_PUT = [
    (re.compile(r'^/api/po/drafts/' + _ID + r'$'), '_put_draft', _JSON),
    (re.compile(r'^/api/po/settings$'), '_put_settings', _JSON),
]


# ── helpers ────────────────────────────────────────────────────────────────

def _send(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.end_headers()
    handler.wfile.write(body)


def _ok(handler, data, status=200, extra=None):
    payload = {'ok': True, 'data': data}
    if extra:
        payload.update(extra)
    _send(handler, status, payload)


def _err(handler, status, msg, extra=None):
    payload = {'ok': False, 'error': msg}
    if extra:
        payload.update(extra)
    _send(handler, status, payload)


def _hostname(value):
    value = (value or '').strip().lower()
    if value.startswith('['):
        return value.split(']')[0] + ']'
    return value.split(':')[0]


def _drain(handler, limit=MAX_BODY):
    """Read (and discard) a small unread request body before an early error
    response, so the client is not reset while still sending. Bodies larger
    than `limit` are never read."""
    try:
        n = int(handler.headers.get('Content-Length', 0) or 0)
    except ValueError:
        return
    if 0 < n <= limit:
        handler.rfile.read(n)


def _drain_briefly(handler, cap=1024 * 1024, seconds=0.5):
    """Before refusing an oversized body: read what arrives quickly (≤ cap,
    ≤ `seconds`) so the error response is delivered instead of the client
    seeing a connection reset. The body itself is never stored."""
    sock = getattr(handler, 'connection', None)
    if sock is None:
        return
    old = sock.gettimeout()
    try:
        sock.settimeout(seconds)
        got = 0
        while got < cap:
            chunk = handler.rfile.read1(min(65536, cap - got))
            if not chunk:
                break
            got += len(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            sock.settimeout(old)
        except OSError:
            pass


def _reject(handler, status, msg, extra=None):
    """Early error before the body was read: drain a small body first."""
    _drain(handler)
    _err(handler, status, msg, extra)


def _guard(handler, ctypes):
    """True if the request may proceed; otherwise responds 403/415 and returns
    False. ctypes: None for reads, else the accepted request content types."""
    if handler.client_address[0] not in _LOOPBACK:
        _reject(handler, 403, 'PO data is only available on this PC.')
        return False
    if any(handler.headers.get(h) for h in _FORWARD_HEADERS):
        _reject(handler, 403, 'Forwarded / tunnelled requests cannot access PO data.')
        return False
    if _hostname(handler.headers.get('Host')) not in _LOCAL_HOSTNAMES:
        _reject(handler, 403, 'Unexpected Host header.')
        return False
    if (handler.headers.get('Sec-Fetch-Site') or '').lower() == 'cross-site':
        _reject(handler, 403, 'Cross-site requests are not allowed.')
        return False
    origin = handler.headers.get('Origin')
    if origin is not None:
        o = urlparse(origin)
        if o.scheme != 'http' or _hostname(o.netloc) not in _LOCAL_HOSTNAMES:
            _reject(handler, 403, 'Cross-origin requests are not allowed.')
            return False
    if ctypes is not None:
        ctype = (handler.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if ctype not in ctypes:
            _reject(handler, 415, 'Content-Type must be one of: ' + ', '.join(ctypes) + '.')
            return False
    return True


def _dispatch(handler, routes, write):
    p = urlparse(handler.path)
    for route in routes:
        pat, fn = route[0], route[1]
        m = pat.match(p.path)
        if m:
            if not _guard(handler, (route[2] if len(route) > 2 else _JSON) if write else None):
                return
            try:
                globals()[fn](handler, m.groupdict(), parse_qs(p.query))
            except Exception:  # noqa: BLE001 — never leak tracebacks/paths to the browser
                _err(handler, 500, 'PO storage error. Nothing further was saved.')
            return
    if _guard(handler, None):
        _reject(handler, 404, 'Not found.')


def _content_length(handler):
    try:
        return max(0, int(handler.headers.get('Content-Length', 0) or 0))
    except ValueError:
        return -1


def _json_body(handler, limit=MAX_BODY):
    n = _content_length(handler)
    if n < 0 or n > limit:
        raise ValueError('too large')
    raw = handler.rfile.read(n) if n else b''
    return json.loads(raw.decode('utf-8')) if raw else None


def _q(qs, key):
    return (qs.get(key, [''])[0] or '').strip()


def _if_match(handler):
    raw = (handler.headers.get('If-Match') or '').strip()
    if raw.startswith('W/'):
        raw = raw[2:]
    raw = raw.strip('"')
    return int(raw) if raw.isdigit() else None


def _full(doc, rev):
    computed = po_model.compute(doc)
    settings = po_db.get_settings()[0]
    return {'data': doc, 'rev': rev, 'computed': computed,
            'readiness': po_model.readiness(doc, computed, settings),
            'issued': po_db.issue_for_draft(doc['id'])}


def _read_valid(handler):
    """Parsed + validated draft, or None after responding with the error."""
    try:
        doc = _json_body(handler)
    except ValueError:
        _err(handler, 400, 'Malformed or oversized JSON body.')
        return None
    try:
        return po_model.validate_draft(doc)
    except po_model.DraftInvalid as e:
        _err(handler, 400, 'The draft was not saved: some values are invalid.',
             extra={'code': 'invalid', 'errors': e.errors[:100]})
        return None


# ── public entry points (called from proxy.py) ─────────────────────────────

def handle_get(handler):
    _dispatch(handler, _GET, write=False)


def handle_post(handler):
    _dispatch(handler, _POST, write=True)


def handle_put(handler):
    _dispatch(handler, _PUT, write=True)


# ── routes ─────────────────────────────────────────────────────────────────

def _status(handler, params, qs):
    settings = po_db.get_settings()[0]
    _ok(handler, {'connected': True, 'counts': po_db.counts(), 'phase': 3,
                  'issuanceAvailable': bool(settings.get('buyerConfirmed') and settings.get('numberingConfirmed')
                                            and settings.get('approvalConfirmed')),
                  'testMode': bool(settings.get('testMode')),
                  'defaults': {'newItemTax': po_model.DEFAULT_ITEM_TAX},
                  'uploads': {'maxBytes': po_db.SOURCE_MAX_BYTES, 'types': list(po_db.SOURCE_TYPES)},
                  'photos': {'maxBytes': po_db.PHOTO_MAX_BYTES, 'types': list(po_db.PHOTO_TYPES)}})


def _list_drafts(handler, params, qs):
    _ok(handler, po_db.list_drafts())


def _get_draft(handler, params, qs):
    got = po_db.get_draft(params['id'])
    if got is None:
        _err(handler, 404, 'Draft not found.', extra={'code': 'not_found'})
        return
    _ok(handler, _full(*got))


def _create_draft(handler, params, qs):
    doc = _read_valid(handler)
    if doc is None:
        return
    if doc.get('revision'):
        _err(handler, 400, 'Revision drafts are created from an issued PO (Revise). Nothing was saved.')
        return
    try:
        doc, rev = po_db.create_draft(doc)
    except po_db.DraftExists:
        _err(handler, 409, 'A draft with this id already exists.', extra={'code': 'exists'})
        return
    _ok(handler, _full(doc, rev), status=201)


def _put_draft(handler, params, qs):
    expected = _if_match(handler)
    if expected is None:
        _reject(handler, 428, 'This tab did not send the draft revision. Reload the page. Nothing was saved.',
                extra={'code': 'rev_required'})
        return
    doc = _read_valid(handler)
    if doc is None:
        return
    if doc['id'] != params['id']:
        _err(handler, 400, 'Draft id does not match the URL. Nothing was saved.')
        return
    try:
        doc, rev = po_db.update_draft(doc, expected)
    except po_db.DraftGone:
        _err(handler, 410, 'This draft no longer exists. Nothing was saved.', extra={'code': 'gone'})
        return
    except po_db.RevConflict as e:
        _err(handler, 409, 'This draft was changed in another tab or session. Nothing was overwritten.',
             extra={'code': 'rev_conflict', 'currentRev': e.current})
        return
    except po_db.DraftIssued as e:
        _err(handler, 409, f'This draft was issued as {e.issue["displayNo"]} and can no longer be changed. '
             'Revise the issued PO to make changes. Nothing was saved.', extra={'code': 'issued', 'issue': e.issue})
        return
    except ValueError as e:
        _err(handler, 400, str(e) + '. Nothing was saved.')
        return
    _ok(handler, _full(doc, rev))


# ── original supplier documents ────────────────────────────────────────────

def _post_source(handler, params, qs):
    draft_id = _q(qs, 'draftId')
    n = _content_length(handler)
    if n > po_db.SOURCE_MAX_BYTES:
        _drain_briefly(handler)
        _err(handler, 413, f'The file is larger than {po_db.SOURCE_MAX_BYTES // (1024 * 1024)} MB. Nothing was stored.',
             extra={'code': 'too_large'})
        return
    if not po_model.DRAFT_ID_RE.match(draft_id) or po_db.get_draft(draft_id) is None:
        _drain(handler, po_db.SOURCE_MAX_BYTES)
        _err(handler, 404, 'Upload refused: the draft does not exist (save it first).', extra={'code': 'no_draft'})
        return
    if n <= 0:
        _err(handler, 400, 'The file is empty.')
        return
    data = handler.rfile.read(n)
    mime = (handler.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    try:
        meta, existing = po_db.save_source(draft_id, _q(qs, 'name'), mime, data)
    except po_db.SourceInvalid as e:
        _err(handler, 400, str(e) + ' Nothing was stored.', extra={'code': 'invalid_file'})
        return
    _ok(handler, meta, status=200 if existing else 201, extra={'existing': existing})


def _list_sources(handler, params, qs):
    draft_id = _q(qs, 'draftId')
    if not po_model.DRAFT_ID_RE.match(draft_id):
        _err(handler, 400, 'draftId is required.')
        return
    _ok(handler, po_db.list_sources(draft_id))


def _get_source(handler, params, qs):
    meta = po_db.get_source(params['id'])
    if not meta:
        _err(handler, 404, 'Source document not found.')
        return
    _ok(handler, meta)


def _get_source_file(handler, params, qs):
    try:
        got = po_db.read_source_bytes(params['id'])
    except po_db.SourceIntegrityError:
        _err(handler, 500, 'The stored original no longer matches its recorded hash (or is missing). It was not served.',
             extra={'code': 'integrity'})
        return
    if not got:
        _err(handler, 404, 'Source document not found.')
        return
    meta, data = got
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', '_', meta['name'])[:120] or 'document'
    handler.send_response(200)
    handler.send_header('Content-Type', meta['mime'])
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Content-Disposition', f'attachment; filename="{ascii_name}"; '
                        f"filename*=UTF-8''{quote(meta['name'], safe='')}")
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Security-Policy', "sandbox; default-src 'none'")
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Source-SHA256', meta['sha256'])
    handler.end_headers()
    handler.wfile.write(data)


# ── extraction results ─────────────────────────────────────────────────────

def _check_json_shape(v, depth=0):
    """Bounded, plain-JSON check for a client-produced extraction result."""
    if depth > 14:
        return 'nested too deeply'
    if isinstance(v, str):
        return 'text value too long' if len(v) > 20000 else ('contains a NUL character' if '\x00' in v else None)
    if isinstance(v, (int, float, bool)) or v is None:
        return None
    if isinstance(v, list):
        if len(v) > 20000:
            return 'list too long'
        for x in v:
            e = _check_json_shape(x, depth + 1)
            if e:
                return e
        return None
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str) or len(k) > 80:
                return 'invalid key'
            e = _check_json_shape(x, depth + 1)
            if e:
                return e
        return None
    return 'unsupported value'


def _post_extraction(handler, params, qs):
    try:
        body = _json_body(handler, MAX_EXTRACTION_BODY)
    except ValueError:
        _err(handler, 400, 'Malformed or oversized extraction (limit 8 MB). Nothing was stored.')
        return
    if not isinstance(body, dict):
        _err(handler, 400, 'Body must be {sourceId, engine, result}.')
        return
    src = po_db.get_source(body.get('sourceId') if isinstance(body.get('sourceId'), str) else '')
    if not src:
        _err(handler, 404, 'Source document not found. Nothing was stored.')
        return
    engine = body.get('engine')
    result = body.get('result')
    if not isinstance(engine, str) or not engine or len(engine) > 200:
        _err(handler, 400, 'engine must be a short text.')
        return
    if not isinstance(result, dict) or not isinstance(result.get('version'), str) \
            or not all(isinstance(result.get(k), list) for k in ('rows', 'otherRows', 'pages', 'warnings')) \
            or not isinstance(result.get('header'), dict) or not isinstance(result.get('totals'), dict):
        _err(handler, 400, 'result is not a PO extraction (version, header, rows, otherRows, totals, pages, warnings).')
        return
    problem = _check_json_shape(result)
    if problem:
        _err(handler, 400, 'Extraction rejected: ' + problem + '. Nothing was stored.')
        return
    doc = result.get('document') if isinstance(result.get('document'), dict) else {}
    summary = {'version': result['version'], 'rows': len(result['rows']), 'otherRows': len(result['otherRows']),
               'pages': len(result['pages']), 'warnings': len(result['warnings']),
               'totalPages': doc.get('totalPages') if isinstance(doc.get('totalPages'), int) else None,
               'complete': doc.get('complete') if isinstance(doc.get('complete'), bool) else None,
               'methods': sorted({str(p.get('method')) for p in result['pages'] if isinstance(p, dict)})}
    saved = po_db.save_extraction(src['id'], engine, result, summary)
    _ok(handler, saved, status=201)


def _list_extractions(handler, params, qs):
    sid = _q(qs, 'sourceId')
    if not po_db.SOURCE_ID_RE.match(sid):
        _err(handler, 400, 'sourceId is required.')
        return
    _ok(handler, po_db.list_extractions(sid))


def _get_extraction(handler, params, qs):
    ex = po_db.get_extraction(params['id'])
    if not ex:
        _err(handler, 404, 'Extraction not found.')
        return
    _ok(handler, ex)


# ── item photos ────────────────────────────────────────────────────────────

def _num(qs, key):
    v = _q(qs, key)
    try:
        f = float(v)
    except ValueError:
        return None
    return f if f == f else None


def _post_photo(handler, params, qs):
    n = _content_length(handler)
    if n > po_db.PHOTO_MAX_BYTES:
        _drain_briefly(handler)
        _err(handler, 413, f'The image is larger than {po_db.PHOTO_MAX_BYTES // (1024 * 1024)} MB. Nothing was stored.',
             extra={'code': 'too_large'})
        return
    src = po_db.get_source(_q(qs, 'sourceId'))
    if not src:
        _drain(handler, po_db.PHOTO_MAX_BYTES)
        _err(handler, 404, 'Source document not found. Nothing was stored.')
        return
    page = _q(qs, 'page')
    region = {k: _num(qs, k) for k in ('x', 'y', 'w', 'h')}
    if not page.isdigit() or any(v is None for v in region.values()):
        _drain(handler, po_db.PHOTO_MAX_BYTES)
        _err(handler, 400, 'page and region (x, y, w, h) are required. Nothing was stored.')
        return
    if n <= 0:
        _err(handler, 400, 'The image is empty.')
        return
    data = handler.rfile.read(n)
    mime = (handler.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    try:
        meta, existing = po_db.save_photo(src['id'], int(page), region, _q(qs, 'kind'), mime, data,
                                          _q(qs, 'extractionId') or None)
    except po_db.SourceInvalid as e:
        _err(handler, 400, str(e) + ' Nothing was stored.', extra={'code': 'invalid_photo'})
        return
    except po_db.PhotoConflict as e:
        _err(handler, 409, 'A different image is already stored for this photo. The existing photo was kept unchanged; '
             'nothing was replaced.', extra={'code': 'photo_conflict', 'existing': e.existing})
        return
    _ok(handler, meta, status=200 if existing else 201, extra={'existing': existing})


def _list_photos(handler, params, qs):
    sid = _q(qs, 'sourceId')
    if not po_db.SOURCE_ID_RE.match(sid):
        _err(handler, 400, 'sourceId is required.')
        return
    _ok(handler, po_db.list_photos(sid))


def _get_photo_file(handler, params, qs):
    try:
        got = po_db.read_photo_bytes(params['id'])
    except po_db.SourceIntegrityError:
        _err(handler, 500, 'The stored photo no longer matches its recorded hash (or is missing). It was not served.',
             extra={'code': 'integrity'})
        return
    if not got:
        _err(handler, 404, 'Photo not found.')
        return
    meta, data = got
    handler.send_response(200)
    handler.send_header('Content-Type', meta['mime'])
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Security-Policy', "sandbox; default-src 'none'")
    handler.send_header('Cache-Control', 'private, max-age=31536000, immutable')   # content never changes for an id
    handler.end_headers()
    handler.wfile.write(data)


# ── settings, PDFs, issuance, revisions ────────────────────────────────────

def _actor():
    import getpass
    try:
        return (getpass.getuser() or 'local user')[:100]
    except Exception:  # noqa: BLE001
        return 'local user'


def _send_pdf(handler, data, filename, cache):
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', '_', filename)[:120] or 'purchase-order.pdf'
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/pdf')
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Content-Disposition', f'inline; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename, safe="")}')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Cache-Control', cache)
    handler.end_headers()
    handler.wfile.write(data)


def _get_settings(handler, params, qs):
    doc, rev = po_db.get_settings()
    _ok(handler, {'data': doc, 'rev': rev})


def _put_settings(handler, params, qs):
    expected = _if_match(handler)
    if expected is None:
        _reject(handler, 428, 'This tab did not send the settings revision. Reload the page. Nothing was saved.', extra={'code': 'rev_required'})
        return
    try:
        body = _json_body(handler)
    except ValueError:
        _err(handler, 400, 'Malformed or oversized JSON body.')
        return
    try:
        doc = po_model.validate_settings(body)
    except po_model.DraftInvalid as e:
        _err(handler, 400, 'The settings were not saved: some values are invalid.', extra={'code': 'invalid', 'errors': e.errors[:50]})
        return
    try:
        doc, rev = po_db.put_settings(doc, expected)
    except po_db.RevConflict as e:
        _err(handler, 409, 'The settings were changed in another tab. Nothing was overwritten.', extra={'code': 'rev_conflict', 'currentRev': e.current})
        return
    _ok(handler, {'data': doc, 'rev': rev})


def _photo_bytes_for(doc):
    out = {}
    for a in doc.get('photos', []):
        if a['status'] != 'removed' and a['includeInPdf'] and a['target']:
            got = po_db.read_photo_bytes(a['photoId'])
            if got:
                out[a['photoId']] = (got[0]['mime'], got[1])
    return out


def _get_preview_pdf(handler, params, qs):
    import po_pdf
    got = po_db.get_draft(params['id'])
    if got is None:
        _err(handler, 404, 'Draft not found.')
        return
    try:
        doc = po_model.validate_draft(got[0])
        pdf = po_pdf.render(doc, po_model.compute(doc), po_db.get_settings()[0], None, _photo_bytes_for(doc))
    except po_model.DraftInvalid:
        _err(handler, 400, 'The saved draft is not valid; open and save it first.')
        return
    except po_pdf.PdfRenderError as e:
        _err(handler, 503, str(e), extra={'code': 'pdf_unavailable'})
        return
    _send_pdf(handler, pdf, f'DRAFT {doc.get("title") or doc["id"]}.pdf', 'no-store')


def _get_changes(handler, params, qs):
    got = po_db.get_draft(params['id'])
    if got is None:
        _err(handler, 404, 'Draft not found.')
        return
    doc = po_model.validate_draft(got[0])
    rev = doc.get('revision')
    if not rev:
        _ok(handler, {'revision': None, 'changes': []})
        return
    issue = po_db.get_issue(rev['basedOnIssueId'])
    snap = issue['snapshot']
    _ok(handler, {'revision': rev, 'basedOn': issue['meta'], 'basedOnCurrent': issue['current'],
                  'changes': po_model.changes(snap['draft'], doc, snap['computed'], po_model.compute(doc))})


def _issue_draft(handler, params, qs):
    import po_pdf
    expected = _if_match(handler)
    if expected is None:
        _reject(handler, 428, 'This tab did not send the draft revision. Reload the page. Nothing was issued.', extra={'code': 'rev_required'})
        return
    try:
        body = _json_body(handler) or {}
    except ValueError:
        _err(handler, 400, 'Malformed JSON body.')
        return
    if not isinstance(body, dict):
        _err(handler, 400, 'Body must be {idempotencyKey, approvedBy, reason}.')
        return
    key = body.get('idempotencyKey') if isinstance(body.get('idempotencyKey'), str) else ''
    if not po_db.IDEMPOTENCY_RE.match(key):
        _err(handler, 400, 'An idempotency key (16-64 letters, digits, - or _) is required. Nothing was issued.')
        return
    reason = body.get('reason') if isinstance(body.get('reason'), str) else ''
    approved = body.get('approvedBy') if isinstance(body.get('approvedBy'), str) else ''
    if len(reason) > po_model.LIMITS['reason'] or len(approved) > 200:
        _err(handler, 400, 'Reason or approver name is too long. Nothing was issued.')
        return
    sc = body.get('supplierCheck')
    supplier_check = None
    if isinstance(sc, dict) and sc.get('status') in ('verified', 'not_found', 'unavailable', 'acknowledged_unavailable', 'test_not_found'):
        supplier_check = {'status': sc['status'], 'at': str(sc.get('at') or '')[:40], 'daftraId': str(sc.get('daftraId') or '')[:20]}
    try:
        meta, duplicate = po_db.issue_draft(params['id'], expected, key, reason, approved, _actor(), po_pdf.render, supplier_check)
    except po_db.DraftGone:
        _err(handler, 404, 'Draft not found. Nothing was issued.')
        return
    except po_db.RevConflict as e:
        _err(handler, 409, 'This draft was changed since you opened it. Reload, review and issue again. Nothing was issued.',
             extra={'code': 'rev_conflict', 'currentRev': e.current})
        return
    except po_db.DraftIssued as e:
        _err(handler, 409, f'This draft was already issued as {e.issue["displayNo"]}.', extra={'code': 'issued', 'issue': e.issue})
        return
    except po_db.StaleRevision as e:
        _err(handler, 409, 'This PO was issued again since this revision was started — the revision is based on an older version. '
             'Nothing was issued.', extra={'code': 'stale_revision', 'current': e.current})
        return
    except po_db.NotReady as e:
        _err(handler, 422, 'The PO cannot be issued yet. Nothing was issued and no number was used.',
             extra={'code': 'not_ready', 'blockers': e.blockers})
        return
    except po_pdf.PdfRenderError as e:
        _err(handler, 503, str(e) + ' Nothing was issued and no number was used.', extra={'code': 'pdf_unavailable'})
        return
    _ok(handler, meta, status=200 if duplicate else 201, extra={'duplicate': duplicate})


def _list_issues(handler, params, qs):
    _ok(handler, po_db.list_issues())


def _get_issue(handler, params, qs):
    got = po_db.get_issue(params['id'])
    if not got:
        _err(handler, 404, 'Issued PO not found.')
        return
    _ok(handler, got)


def _get_issue_pdf(handler, params, qs):
    try:
        got = po_db.read_issue_pdf(params['id'])
    except po_db.SourceIntegrityError:
        _err(handler, 500, 'The stored PDF no longer matches its recorded hash (or is missing). It was not served.', extra={'code': 'integrity'})
        return
    if not got:
        _err(handler, 404, 'Issued PO not found.')
        return
    meta, data = got
    _send_pdf(handler, data, f'{meta["displayNo"]}.pdf', 'private, max-age=31536000, immutable')


def _revise_issue(handler, params, qs):
    try:
        body = _json_body(handler) or {}
    except ValueError:
        _err(handler, 400, 'Malformed JSON body.')
        return
    new_id = body.get('draftId') if isinstance(body, dict) else None
    if not isinstance(new_id, str) or not po_model.DRAFT_ID_RE.match(new_id) or po_db.get_draft(new_id) is not None:
        _err(handler, 400, 'A new, unused draft id is required.')
        return
    try:
        doc, rev = po_db.revise_issue(params['id'], new_id)
    except po_db.DraftGone:
        _err(handler, 404, 'Issued PO not found.')
        return
    except po_db.StaleRevision as e:
        _err(handler, 409, 'Only the current issued version can be revised.', extra={'code': 'not_current', 'current': e.current})
        return
    except po_db.OpenRevisionExists as e:
        _err(handler, 409, 'This PO already has an open revision draft.', extra={'code': 'open_revision', 'draftId': e.draft_id})
        return
    _ok(handler, _full(doc, rev), status=201)
