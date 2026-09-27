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
"""
import json
import re
from urllib.parse import urlparse, parse_qs

import po_db
import po_model

# No storage is opened or created at import time — po_db initialises lazily
# on the first write (see po_db._conn).

MAX_BODY = 2 * 1024 * 1024
_ID = r'(?P<id>po_[A-Za-z0-9]{10,40})'
_LOOPBACK = {'127.0.0.1', '::1', '::ffff:127.0.0.1'}
_LOCAL_HOSTNAMES = {'127.0.0.1', 'localhost', '[::1]'}
_FORWARD_HEADERS = ('X-Forwarded-For', 'X-Forwarded-Host', 'Forwarded', 'X-Real-IP',
                    'CF-Connecting-IP', 'CF-Ray', 'True-Client-IP')

_GET = [
    (re.compile(r'^/api/po/status$'), '_status'),
    (re.compile(r'^/api/po/drafts$'), '_list_drafts'),
    (re.compile(r'^/api/po/drafts/' + _ID + r'$'), '_get_draft'),
]
_POST = [
    (re.compile(r'^/api/po/drafts$'), '_create_draft'),
]
_PUT = [
    (re.compile(r'^/api/po/drafts/' + _ID + r'$'), '_put_draft'),
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


def _guard(handler, write):
    """True if the request may proceed; otherwise responds 403 and returns False."""
    if handler.client_address[0] not in _LOOPBACK:
        _err(handler, 403, 'PO data is only available on this PC.')
        return False
    if any(handler.headers.get(h) for h in _FORWARD_HEADERS):
        _err(handler, 403, 'Forwarded / tunnelled requests cannot access PO data.')
        return False
    if _hostname(handler.headers.get('Host')) not in _LOCAL_HOSTNAMES:
        _err(handler, 403, 'Unexpected Host header.')
        return False
    if (handler.headers.get('Sec-Fetch-Site') or '').lower() == 'cross-site':
        _err(handler, 403, 'Cross-site requests are not allowed.')
        return False
    origin = handler.headers.get('Origin')
    if origin is not None:
        o = urlparse(origin)
        if o.scheme != 'http' or _hostname(o.netloc) not in _LOCAL_HOSTNAMES:
            _err(handler, 403, 'Cross-origin requests are not allowed.')
            return False
    if write:
        ctype = (handler.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if ctype != 'application/json':
            _err(handler, 415, 'Content-Type must be application/json.')
            return False
    return True


def _dispatch(handler, routes, write):
    p = urlparse(handler.path)
    for pat, fn in routes:
        m = pat.match(p.path)
        if m:
            if not _guard(handler, write):
                return
            try:
                globals()[fn](handler, m.groupdict(), parse_qs(p.query))
            except Exception:  # noqa: BLE001 — never leak tracebacks/paths to the browser
                _err(handler, 500, 'PO storage error. Nothing further was saved.')
            return
    if _guard(handler, False):
        _err(handler, 404, 'Not found.')


def _json_body(handler):
    n = int(handler.headers.get('Content-Length', 0) or 0)
    if n > MAX_BODY:
        raise ValueError('too large')
    raw = handler.rfile.read(n) if n else b''
    return json.loads(raw.decode('utf-8')) if raw else None


def _if_match(handler):
    raw = (handler.headers.get('If-Match') or '').strip()
    if raw.startswith('W/'):
        raw = raw[2:]
    raw = raw.strip('"')
    return int(raw) if raw.isdigit() else None


def _full(doc, rev):
    computed = po_model.compute(doc)
    return {'data': doc, 'rev': rev, 'computed': computed,
            'readiness': po_model.readiness(doc, computed)}


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
    _ok(handler, {'connected': True, 'counts': po_db.counts(), 'phase': 1,
                  'issuanceAvailable': False,
                  'defaults': {'newItemTax': po_model.DEFAULT_ITEM_TAX}})


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
    try:
        doc, rev = po_db.create_draft(doc)
    except po_db.DraftExists:
        _err(handler, 409, 'A draft with this id already exists.', extra={'code': 'exists'})
        return
    _ok(handler, _full(doc, rev), status=201)


def _put_draft(handler, params, qs):
    expected = _if_match(handler)
    if expected is None:
        _err(handler, 428, 'This tab did not send the draft revision. Reload the page. Nothing was saved.',
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
    _ok(handler, _full(doc, rev))
