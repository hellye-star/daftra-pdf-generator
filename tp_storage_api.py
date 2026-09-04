"""
Vista Platform — Technical Proposals central storage API handlers.

Mirrors sqi_storage_api.py / catalogue_api.py in style. No loopback
restriction here; proxy.py owns network access (it binds 127.0.0.1 only,
so this database is never reachable off this PC).

Phase 1 endpoints:
    GET    /api/tp/status
    GET    /api/tp/projects
    GET    /api/tp/projects/<id>
    PUT    /api/tp/projects/<id>            (?mode=skip-existing → never overwrite)
    POST   /api/tp/projects/<id>/duplicate  {name, revision} → clone w/ new IDs + own photo files
    DELETE /api/tp/projects/<id>
    GET    /api/tp/photos?projectId=<id>
    POST   /api/tp/photos                   (?overwrite=1)
    GET    /api/tp/photos/<id>              raw bytes  (used as <img src>)
    GET    /api/tp/photos/<id>/meta
    DELETE /api/tp/photos/<id>
    POST   /api/tp/migrate                  (?overwrite=1) idempotent bundle import
    GET    /api/tp/backup                   streams vista-tp-backup-<ts>.zip
"""
import base64
import json
import re
from urllib.parse import urlparse, parse_qs

import tp_db

tp_db.init_db()

_PID = r'(?P<id>[A-Za-z0-9_.\-]+)'

_GET = [
    (re.compile(r'^/api/tp/status$'),                       '_status'),
    (re.compile(r'^/api/tp/settings/(?P<key>[A-Za-z0-9_.\-]+)$'), '_get_setting'),
    (re.compile(r'^/api/tp/projects$'),                     '_list_projects'),
    (re.compile(r'^/api/tp/projects/' + _PID + r'$'),       '_get_project'),
    (re.compile(r'^/api/tp/photos$'),                       '_list_photos'),
    (re.compile(r'^/api/tp/photos/' + _PID + r'/meta$'),    '_get_photo_meta'),
    (re.compile(r'^/api/tp/photos/' + _PID + r'$'),         '_get_photo_bytes'),
    (re.compile(r'^/api/tp/backup$'),                       '_backup'),
]
_POST = [
    (re.compile(r'^/api/tp/projects/' + _PID + r'/duplicate$'), '_duplicate_project'),
    (re.compile(r'^/api/tp/photos$'),   '_post_photo'),
    (re.compile(r'^/api/tp/migrate$'),  '_migrate'),
]
_PUT = [
    (re.compile(r'^/api/tp/projects/' + _PID + r'$'), '_put_project'),
    (re.compile(r'^/api/tp/settings/(?P<key>[A-Za-z0-9_.\-]+)$'), '_put_setting'),
]
_DELETE = [
    (re.compile(r'^/api/tp/projects/' + _PID + r'$'), '_delete_project'),
    (re.compile(r'^/api/tp/photos/' + _PID + r'$'),   '_delete_photo'),
]


# ── helpers (same shape as sqi_storage_api.py) ──────────────────────────────

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


# ── status ─────────────────────────────────────────────────────────────────

def _status(handler, params, qs):
    _ok(handler, {
        'connected': True,
        'dbPath': tp_db.db_path(),
        'counts': tp_db.counts(),
        'lastBackup': tp_db.get_setting('lastBackupAt'),
        'platformStorage': tp_db.get_setting('platformStorage'),
    })


def _get_setting(handler, params, qs):
    _ok(handler, tp_db.get_setting(params['key']))


def _put_setting(handler, params, qs):
    try:
        value = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    tp_db.set_setting(params['key'], value)
    _ok(handler, {'key': params['key']})


# ── projects ───────────────────────────────────────────────────────────────

def _list_projects(handler, params, qs):
    _ok(handler, tp_db.list_projects())


def _get_project(handler, params, qs):
    doc = tp_db.get_project(params['id'])
    if doc is None:
        _err(handler, 404, 'Project not found.')
        return
    _ok(handler, doc)


def _put_project(handler, params, qs):
    try:
        doc = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    if not isinstance(doc, dict) or doc.get('id') != params['id']:
        _err(handler, 400, 'Body must be a project object whose id matches the URL.')
        return
    skip_existing = (qs.get('mode', [''])[0] == 'skip-existing')
    if skip_existing and tp_db.project_exists(params['id']):
        _ok(handler, {'id': params['id'], 'skipped': True})
        return
    try:
        tp_db.upsert_project(doc)
    except ValueError as e:
        _err(handler, 400, str(e))
        return
    _ok(handler, {'id': params['id'], 'skipped': False})


def _delete_project(handler, params, qs):
    tp_db.delete_project(params['id'])
    _ok(handler, {'deleted': params['id']})


def _duplicate_project(handler, params, qs):
    """Server-side clone: new project + item + photo IDs, own copied photo
    files, all internal references rewritten. Source is only read. On any
    failure the partial duplicate is removed."""
    try:
        body = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    if not isinstance(body, dict):
        body = {}
    try:
        doc = tp_db.duplicate_project(
            params['id'],
            name=body.get('name'),
            revision=body.get('revision'),
        )
    except KeyError:
        _err(handler, 404, 'Source project not found.')
        return
    except Exception as e:  # noqa: BLE001
        _err(handler, 500, 'Duplicate failed: ' + str(e))
        return
    _ok(handler, doc, 201)


# ── photos ─────────────────────────────────────────────────────────────────

def _list_photos(handler, params, qs):
    project_id = (qs.get('projectId', [''])[0] or '').strip() or None
    rows = tp_db.list_photos(project_id)
    out = [{
        'id': r['id'], 'projectId': r['project_id'], 'itemId': r['item_id'],
        'slot': r['slot'], 'mime': r['mime'], 'w': r['w'], 'h': r['h'],
        'bytes': r['bytes'], 'sha256': r['sha256'], 'keepPage': r['keep_page'],
        'createdAt': r['created_at'], 'url': '/api/tp/photos/' + r['id'],
    } for r in rows]
    _ok(handler, out)


def _get_photo_meta(handler, params, qs):
    m = tp_db.get_photo_meta(params['id'])
    if not m:
        _err(handler, 404, 'Photo not found.')
        return
    _ok(handler, {
        'id': m['id'], 'projectId': m['project_id'], 'itemId': m['item_id'],
        'slot': m['slot'], 'mime': m['mime'], 'w': m['w'], 'h': m['h'],
        'bytes': m['bytes'], 'sha256': m['sha256'], 'keepPage': m['keep_page'],
        'createdAt': m['created_at'],
    })


def _get_photo_bytes(handler, params, qs):
    got = tp_db.get_photo_bytes(params['id'])
    if not got:
        _err(handler, 404, 'Photo not found.')
        return
    data, mime, _meta = got
    handler.send_response(200)
    handler.send_header('Content-Type', mime or 'application/octet-stream')
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Cache-Control', 'private, max-age=86400')
    handler.end_headers()
    handler.wfile.write(data)


def _post_photo(handler, params, qs):
    try:
        body = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    if not isinstance(body, dict) or not body.get('id') or not body.get('projectId'):
        _err(handler, 400, 'id and projectId are required.')
        return
    try:
        data = base64.b64decode(body.get('dataBase64') or '')
    except Exception:  # noqa: BLE001
        _err(handler, 400, 'dataBase64 could not be decoded.')
        return
    overwrite = (qs.get('overwrite', [''])[0] in ('1', 'true'))
    try:
        r = tp_db.save_photo(body, data, overwrite=overwrite)
    except ValueError as e:
        _err(handler, 400, str(e))
        return
    _ok(handler, r, 201 if not r.get('skipped') else 200)


def _delete_photo(handler, params, qs):
    if not tp_db.delete_photo(params['id']):
        _err(handler, 404, 'Photo not found.')
        return
    _ok(handler, {'deleted': params['id']})


# ── idempotent bulk migration ──────────────────────────────────────────────

def _migrate(handler, params, qs):
    try:
        payload = _json_body(handler)
    except (ValueError, json.JSONDecodeError):
        _err(handler, 400, 'Malformed JSON body.')
        return
    if not isinstance(payload, dict):
        _err(handler, 400, 'Body must be a JSON object {projects:[...], photos:[...]}.')
        return
    overwrite = (qs.get('overwrite', [''])[0] in ('1', 'true'))
    _ok(handler, tp_db.migrate_bundle(payload, overwrite=overwrite))


# ── manual backup ──────────────────────────────────────────────────────────

def _backup(handler, params, qs):
    fname, data = tp_db.make_backup_zip_bytes()
    tp_db.set_setting('lastBackupAt', tp_db._now_iso())
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/zip')
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Content-Disposition', 'attachment; filename="' + fname + '"')
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(data)
