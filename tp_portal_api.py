"""
proxy.py handlers for /api/tp/portal/*  (Phase 1 — read-only portal).

Routes (all localhost-only, same trust model as /api/tp/*):
  GET  /api/tp/portal/public-config      -> { url, anonKey, bucket }   (NO service key)
  GET  /api/tp/portal/status?project=ID  -> link + grants + revisions
  GET  /api/tp/portal/projects           -> central-DB projects (id,name,client,items,photos,link)
  POST /api/tp/portal/publish            -> { projectId, client, users:[{name,email}], title?, revision? }
  POST /api/tp/portal/pull               -> { projectId }
"""
import json
from urllib.parse import urlparse, parse_qs

import tp_db
import tp_portal
import tp_portal_db


def _send(handler, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(body)


def _err(handler, code, msg):
    _send(handler, {'ok': False, 'error': msg}, code)


def _body(handler):
    n = int(handler.headers.get('Content-Length') or 0)
    if not n:
        return {}
    return json.loads(handler.rfile.read(n).decode('utf-8'))


def handle_get(handler):
    u = urlparse(handler.path)
    path = u.path
    qs = parse_qs(u.query)
    try:
        if path == '/api/tp/portal/public-config':
            if not tp_portal.is_configured():
                return _err(handler, 503, 'vista_portal is not configured in config.json')
            data = tp_portal.public_config()
            data['secretConfigured'] = tp_portal.secret_available()   # non-secret boolean
            data['secretKind'] = tp_portal.secret_kind()              # 'secret'|'publishable'|'jwt'|'unknown'|'missing'
            return _send(handler, {'ok': True, 'data': data})

        if path == '/api/tp/portal/status':
            pid = (qs.get('project') or [''])[0]
            if not pid:
                return _err(handler, 400, 'project query param required')
            return _send(handler, {'ok': True, 'data': tp_portal.status(pid)})

        if path == '/api/tp/portal/projects':
            tp_portal_db.init_db()
            out = []
            for p in tp_db.list_projects():
                ph = tp_db.list_photos(p['id'])
                link = tp_portal_db.get_link(p['id'])
                out.append({
                    'id': p['id'], 'name': p.get('name'), 'client': p.get('client'),
                    'revision': p.get('revision'),
                    'items': len(p.get('items') or []), 'photos': len(ph),
                    'link': link,
                })
            return _send(handler, {'ok': True, 'data': out})

        if path == '/api/tp/portal/client-activity':
            pid = (qs.get('project') or [''])[0]
            if not pid:
                return _err(handler, 400, 'project query param required')
            return _send(handler, {'ok': True, 'data': tp_portal.client_activity(pid)})

        if path == '/api/tp/portal/audit':
            pid = (qs.get('project') or [''])[0]
            if not pid:
                return _err(handler, 400, 'project query param required')
            tp_portal_db.init_db()
            return _send(handler, {'ok': True, 'data': {'events': tp_portal_db.list_audit(pid)}})

        return _err(handler, 404, 'unknown portal route: ' + path)
    except tp_portal.PortalConfigError as e:
        return _err(handler, 503, str(e))
    except Exception as e:  # noqa: BLE001
        return _err(handler, 500, '%s: %s' % (type(e).__name__, e))


def handle_post(handler):
    u = urlparse(handler.path)
    path = u.path
    try:
        body = _body(handler)

        if path == '/api/tp/portal/publish':
            pid = body.get('projectId') or body.get('project_id')
            client = (body.get('client') or '').strip()
            users = body.get('users') or []
            if not pid or not client or not users:
                return _err(handler, 400, 'projectId, client and at least one user are required')
            clean_users = []
            for x in users:
                email = (x.get('email') or '').strip()
                if '@' not in email:
                    return _err(handler, 400, 'invalid email: %r' % email)
                clean_users.append({'email': email, 'name': (x.get('name') or '').strip()})
            grants = body.get('grants')  # {comment,propose,approve} or None
            res = tp_portal.publish(pid, client, clean_users,
                                    title=(body.get('title') or None),
                                    revision_label=(body.get('revision') or None),
                                    grants=grants)
            return _send(handler, {'ok': True, 'data': res})

        if path == '/api/tp/portal/pull':
            pid = body.get('projectId') or body.get('project_id')
            if not pid:
                return _err(handler, 400, 'projectId required')
            return _send(handler, {'ok': True, 'data': tp_portal.pull(pid)})

        if path == '/api/tp/portal/change-decision':
            pid = body.get('projectId')
            decision = body.get('decision')
            if not pid or decision not in ('accept', 'reject'):
                return _err(handler, 400, 'projectId + decision (accept|reject) required')
            if not body.get('changeRequestId'):
                return _err(handler, 400, 'changeRequestId required')
            return _send(handler, {'ok': True, 'data': tp_portal.decide_change(
                body['changeRequestId'], decision, note=body.get('note'),
                edited_value=body.get('editedValue'), _project_id=pid)})

        if path == '/api/tp/portal/change-set-decision':
            pid = body.get('projectId')
            decision = body.get('decision')
            if not pid or decision not in ('accept_all', 'reject_all'):
                return _err(handler, 400, 'projectId + decision (accept_all|reject_all) required')
            if not body.get('changeSetId'):
                return _err(handler, 400, 'changeSetId required')
            return _send(handler, {'ok': True, 'data': tp_portal.decide_change_set(
                body['changeSetId'], decision, _project_id=pid)})

        return _err(handler, 404, 'unknown portal route: ' + path)
    except tp_portal.PortalConfigError as e:
        return _err(handler, 503, str(e))
    except KeyError as e:
        return _err(handler, 404, str(e))
    except Exception as e:  # noqa: BLE001
        return _err(handler, 500, '%s: %s' % (type(e).__name__, e))
