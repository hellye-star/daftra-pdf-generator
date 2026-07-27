"""
Supplier Quotation Intelligence — persistent storage API handlers.
No loopback restriction here; proxy.py controls network access. Mirrors
catalogue_api.py's route-table style for consistency with the rest of
this proxy.

This module — and sqi_db.py underneath it — never sees, stores, or
forwards ANTHROPIC_API_KEY or any other secret. AI assessments are
persisted as their already-rendered result JSON (narrative, recommended
supplier, risks, etc), never the key used to produce them.
"""
import json
import re

import sqi_db

sqi_db.init_db()

# ── Route tables ──────────────────────────────────────────────────────────────

_GET = [
    (re.compile(r'^/api/sqi/status$'),                        '_status'),
    (re.compile(r'^/api/sqi/evaluations$'),                   '_list_evaluations'),
    (re.compile(r'^/api/sqi/evaluations/(?P<id>[^/]+)$'),      '_get_evaluation'),
    (re.compile(r'^/api/sqi/history$'),                        '_list_history'),
    (re.compile(r'^/api/sqi/settings/(?P<key>[^/]+)$'),        '_get_setting'),
    (re.compile(r'^/api/sqi/files/(?P<id>[^/]+)/meta$'),       '_get_file_meta'),
    (re.compile(r'^/api/sqi/files/(?P<id>[^/]+)$'),            '_get_file_bytes'),
    (re.compile(r'^/api/sqi/extractions/(?P<id>[^/]+)$'),      '_get_extraction'),
    (re.compile(r'^/api/sqi/export$'),                         '_export'),
]
_POST = [
    (re.compile(r'^/api/sqi/files$'),      '_save_file'),
    (re.compile(r'^/api/sqi/migrate$'),    '_migrate'),
    (re.compile(r'^/api/sqi/import$'),     '_import'),
]
_PUT = [
    (re.compile(r'^/api/sqi/evaluations/(?P<id>[^/]+)$'), '_put_evaluation'),
    (re.compile(r'^/api/sqi/history$'),                    '_put_history'),
    (re.compile(r'^/api/sqi/settings/(?P<key>[^/]+)$'),    '_put_setting'),
    (re.compile(r'^/api/sqi/extractions/(?P<id>[^/]+)$'),  '_put_extraction'),
]
_DELETE = [
    (re.compile(r'^/api/sqi/evaluations/(?P<id>[^/]+)$'), '_delete_evaluation'),
]


# ── Utilities (same shape as catalogue_api.py) ──────────────────────────────

def _parse(handler):
    from urllib.parse import urlparse, parse_qs
    p = urlparse(handler.path)
    return p.path, parse_qs(p.query)


def _ok(handler, data, status=200):
    body = json.dumps({'ok': True, 'data': data}, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _err(handler, status, msg):
    body = json.dumps({'ok': False, 'error': msg}, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
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
    n = int(handler.headers.get('Content-Length', 0))
    return handler.rfile.read(n) if n else b''


def _json_body(handler):
    raw = _body(handler)
    if not raw:
        return {}
    return json.loads(raw)


# ── Public entry points ───────────────────────────────────────────────────────

def handle_get(handler):
    path, qs = _parse(handler)
    if not _dispatch(handler, _GET, path, qs):
        _err(handler, 404, 'Not found.')


def handle_post(handler):
    path, qs = _parse(handler)
    if not _dispatch(handler, _POST, path, qs):
        _err(handler, 404, 'Not found.')


def handle_put(handler):
    path, qs = _parse(handler)
    if not _dispatch(handler, _PUT, path, qs):
        _err(handler, 404, 'Not found.')


def handle_delete(handler):
    path, qs = _parse(handler)
    if not _dispatch(handler, _DELETE, path, qs):
        _err(handler, 404, 'Not found.')


# ── Status ────────────────────────────────────────────────────────────────────

def _status(handler, params, qs):
    _ok(handler, {'connected': True, 'dbPath': sqi_db.db_path(), 'counts': sqi_db.counts()})


# ── Evaluations ───────────────────────────────────────────────────────────────

def _list_evaluations(handler, params, qs):
    _ok(handler, sqi_db.list_evaluations())


def _get_evaluation(handler, params, qs):
    ev = sqi_db.get_evaluation(params['id'])
    if ev is None:
        _err(handler, 404, 'Evaluation not found.')
        return
    _ok(handler, ev)


def _put_evaluation(handler, params, qs):
    try:
        ev = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    if not isinstance(ev, dict) or ev.get('id') != params['id']:
        _err(handler, 400, 'Body must be an evaluation object whose id matches the URL.')
        return
    sqi_db.upsert_evaluation(ev)
    _ok(handler, {'id': ev['id']})


def _delete_evaluation(handler, params, qs):
    sqi_db.delete_evaluation(params['id'])
    _ok(handler, {'deleted': params['id']})


# ── Historical pricing records ───────────────────────────────────────────────

def _list_history(handler, params, qs):
    _ok(handler, sqi_db.list_history())


def _put_history(handler, params, qs):
    try:
        records = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    if not isinstance(records, list):
        _err(handler, 400, 'Body must be a JSON array.')
        return
    sqi_db.save_history(records)
    _ok(handler, {'count': len(records)})


# ── Settings (aiConfig, rowClassCorrections, seeded flag, etc) ──────────────

def _get_setting(handler, params, qs):
    value = sqi_db.get_setting(params['key'])
    _ok(handler, value)


def _put_setting(handler, params, qs):
    try:
        value = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    sqi_db.set_setting(params['key'], value)
    _ok(handler, {'key': params['key']})


# ── Files ─────────────────────────────────────────────────────────────────────

def _save_file(handler, params, qs):
    import base64
    try:
        body = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    file_id = body.get('id')
    if not file_id:
        _err(handler, 400, 'id is required.')
        return
    try:
        data = base64.b64decode(body.get('dataBase64') or '')
    except Exception:  # noqa: BLE001
        _err(handler, 400, 'dataBase64 could not be decoded.')
        return
    try:
        meta = sqi_db.save_file(file_id, body.get('evaluationId'), body.get('originalName') or file_id,
                                 body.get('contentType'), data)
    except ValueError as e:
        _err(handler, 400, str(e))
        return
    _ok(handler, meta, 201)


def _get_file_meta(handler, params, qs):
    meta = sqi_db.get_file_meta(params['id'])
    if not meta:
        _err(handler, 404, 'File not found.')
        return
    _ok(handler, {'id': meta['id'], 'evaluationId': meta['evaluation_id'], 'originalName': meta['original_name'],
                  'contentType': meta['content_type'], 'sizeBytes': meta['size_bytes'], 'createdAt': meta['created_at']})


def _get_file_bytes(handler, params, qs):
    got = sqi_db.get_file_bytes(params['id'])
    if not got:
        _err(handler, 404, 'File not found.')
        return
    data, content_type, original_name = got
    handler.send_response(200)
    handler.send_header('Content-Type', content_type or 'application/octet-stream')
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Content-Disposition', 'inline; filename="' + (original_name or 'file').replace('"', '') + '"')
    handler.send_header('Cache-Control', 'private, max-age=3600')
    handler.end_headers()
    handler.wfile.write(data)


# ── Extractions ───────────────────────────────────────────────────────────────

def _get_extraction(handler, params, qs):
    data = sqi_db.get_extraction(params['id'])
    if data is None:
        _err(handler, 404, 'Extraction not found.')
        return
    _ok(handler, data)


def _put_extraction(handler, params, qs):
    try:
        body = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    evaluation_id = body.get('evaluationId') if isinstance(body, dict) else None
    data = body.get('data') if isinstance(body, dict) and 'data' in body else body
    sqi_db.save_extraction(params['id'], evaluation_id, data)
    _ok(handler, {'id': params['id']})


# ── Backup export / import / migration ───────────────────────────────────────

def _export(handler, params, qs):
    _ok(handler, sqi_db.export_all())


def _migrate(handler, params, qs):
    """One-shot, idempotent import of the browser's existing
    localStorage/IndexedDB data — never overwrites anything already in
    the backend (an id collision is skipped, not clobbered), so this is
    always safe to call again."""
    try:
        payload = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    if not isinstance(payload, dict):
        _err(handler, 400, 'Body must be a JSON object.')
        return
    result = sqi_db.import_all(payload, overwrite=False)
    _ok(handler, result)


def _import(handler, params, qs):
    """Explicit user-triggered restore from a downloaded backup — always
    overwrites any existing backend copy of the same id."""
    try:
        payload = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    if not isinstance(payload, dict):
        _err(handler, 400, 'Body must be a JSON object.')
        return
    result = sqi_db.import_all(payload, overwrite=True)
    _ok(handler, result)
