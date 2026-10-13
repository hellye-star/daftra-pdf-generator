"""
Vista Platform — Sarawat Quote Generator storage API handlers.

Mirrors tp_storage_api.py's route-table style for consistency with the
rest of this proxy. No loopback restriction here; proxy.py controls
network access (it binds 127.0.0.1 only).

Endpoints:
    GET    /api/sarawat/status
    GET    /api/sarawat/quotes
    GET    /api/sarawat/summary             lightweight list for the Saved Quotations browser
                                            ?q=&source=&status=&client=&from=&to=&recent=&sort=&limit=&offset=
                                            (served from the quotation_index table — no photos, no full documents)
    GET    /api/sarawat/quotes/<id>
    GET    /api/sarawat/quotes/<id>/thumb/<n>   small JPEG of the n-th photo of the latest revision
    PUT    /api/sarawat/quotes/<id>
    DELETE /api/sarawat/quotes/<id>
    POST   /api/sarawat/pdf          body {"html": "<!doctype html>..."} -> application/pdf
                                     (native selectable-text quotation PDF, see sarawat_pdf.py)
"""
import json
import re
from urllib.parse import urlparse, parse_qs

import sarawat_db

sarawat_db.init_db()

_QID = r'(?P<id>[A-Za-z0-9_.\-]+)'

_GET = [
    (re.compile(r'^/api/sarawat/status$'),                 '_status'),
    (re.compile(r'^/api/sarawat/quotes$'),                 '_list_quotes'),
    (re.compile(r'^/api/sarawat/summary$'),                '_summary'),
    (re.compile(r'^/api/sarawat/quotes/' + _QID + r'/thumb/(?P<n>\d{1,3})$'), '_thumb'),
    (re.compile(r'^/api/sarawat/quotes/' + _QID + r'$'),   '_get_quote'),
]
_PUT = [
    (re.compile(r'^/api/sarawat/quotes/' + _QID + r'$'),   '_put_quote'),
]
_POST = [
    (re.compile(r'^/api/sarawat/pdf$'),                    '_render_pdf'),
]
_DELETE = [
    (re.compile(r'^/api/sarawat/quotes/' + _QID + r'$'),   '_delete_quote'),
]


# ── helpers (same shape as tp_storage_api.py) ───────────────────────────────

def _parse(handler):
    p = urlparse(handler.path)
    return p.path, parse_qs(p.query)


def _ok(handler, data, status=200):
    body = json.dumps({'ok': True, 'data': data}, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(body)


def _err(handler, status, msg):
    body = json.dumps({'ok': False, 'error': msg}, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(body)


def _dispatch(handler, routes, path, qs):
    for pat, fn in routes:
        m = pat.match(path)
        if m:
            globals()[fn](handler, m.groupdict(), qs)
            return True
    return False


def _body(handler) -> bytes:
    n = int(handler.headers.get('Content-Length', 0) or 0)
    return handler.rfile.read(n) if n else b''


def _json_body(handler):
    raw = _body(handler)
    return json.loads(raw) if raw else {}


# ── public entry points ─────────────────────────────────────────────────────

def handle_get(handler):
    path, qs = _parse(handler)
    if not _dispatch(handler, _GET, path, qs):
        _err(handler, 404, 'Not found.')


def handle_put(handler):
    path, qs = _parse(handler)
    if not _dispatch(handler, _PUT, path, qs):
        _err(handler, 404, 'Not found.')


def handle_post(handler):
    path, qs = _parse(handler)
    if not _dispatch(handler, _POST, path, qs):
        _err(handler, 404, 'Not found.')


def handle_delete(handler):
    path, qs = _parse(handler)
    if not _dispatch(handler, _DELETE, path, qs):
        _err(handler, 404, 'Not found.')


# ── status ───────────────────────────────────────────────────────────────────

def _status(handler, params, qs):
    _ok(handler, {
        'connected': True,
        'dbPath': sarawat_db.db_path(),
        'counts': sarawat_db.counts(),
    })


# ── quotations ───────────────────────────────────────────────────────────────

def _list_quotes(handler, params, qs):
    _ok(handler, sarawat_db.list_quotations())


def _qs1(qs, key, default=''):
    v = qs.get(key)
    return v[0] if v else default


def _summary(handler, params, qs):
    try:
        limit = int(_qs1(qs, 'limit', '200') or 200)
        offset = int(_qs1(qs, 'offset', '0') or 0)
        recent = int(_qs1(qs, 'recent', '0') or 0)
    except ValueError:
        _err(handler, 400, 'limit/offset/recent must be numbers.')
        return
    _ok(handler, sarawat_db.search_quotations(
        q=_qs1(qs, 'q'), source=_qs1(qs, 'source'), status=_qs1(qs, 'status'),
        client=_qs1(qs, 'client'), date_from=_qs1(qs, 'from'), date_to=_qs1(qs, 'to'),
        recent_days=recent, sort=_qs1(qs, 'sort', 'newest'), limit=limit, offset=offset))


def _thumb(handler, params, qs):
    t = sarawat_db.get_thumbnail(params['id'], int(params['n']))
    if t is None:
        _err(handler, 404, 'Photo not found.')
        return
    data, mime, updated = t
    handler.send_response(200)
    handler.send_header('Content-Type', mime)
    handler.send_header('Content-Length', str(len(data)))
    # The page adds ?v=<updatedAt>, so a revised quotation gets a fresh URL.
    handler.send_header('Cache-Control', 'private, max-age=86400')
    handler.end_headers()
    handler.wfile.write(data)


def _get_quote(handler, params, qs):
    doc = sarawat_db.get_quotation(params['id'])
    if doc is None:
        _err(handler, 404, 'Quotation not found.')
        return
    _ok(handler, doc)


def _put_quote(handler, params, qs):
    try:
        doc = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    if not isinstance(doc, dict) or doc.get('id') != params['id']:
        _err(handler, 400, 'Body must be a quotation object whose id matches the URL.')
        return
    try:
        sarawat_db.upsert_quotation(doc)
    except ValueError as e:
        _err(handler, 400, str(e))
        return
    _ok(handler, {'id': params['id']})


def _delete_quote(handler, params, qs):
    sarawat_db.delete_quotation(params['id'])
    _ok(handler, {'deleted': params['id']})


# ── native PDF ───────────────────────────────────────────────────────────────

def _render_pdf(handler, params, qs):
    import sarawat_pdf
    try:
        body = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    try:
        pdf = sarawat_pdf.html_to_pdf((body or {}).get('html'))
    except sarawat_pdf.PdfRenderError as e:
        _err(handler, 500, str(e))
        return
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/pdf')
    handler.send_header('Content-Length', str(len(pdf)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(pdf)
