"""
Vista Client Proposal Portal — the bridge (Phase 1, READ-ONLY portal).

Responsibilities:
  * build_snapshot(project_id)  -> a client-safe projection of ONE proposal
  * publish(project_id, ...)     -> push snapshot + photos to Supabase, grant a client user
  * pull(project_id)             -> pull the cloud audit log into the local mirror

Phase 1 does NOT implement: client edits, comments, proposed changes, approvals,
Accept/Reject. Those are Phases 2-4.

Config: config.json -> "vista_portal": {
    "identity":  { "id","name","email" },
    "supabase":  { "url","anon_key","service_role_key","project_ref" },
    "portal_url": "http://localhost:8080/client-portal.html",
    "storage_bucket": "proposal-photos"
}
The service_role_key is read from disk here and used only for server->Supabase
calls. It is never returned to any browser (see public_config()).
"""
import base64
import datetime
import hashlib
import json
import mimetypes
import os
import re
import time

import requests

import tp_db
import tp_portal_db

_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
_TIMEOUT = 30
DEFAULT_BUCKET = 'proposal-photos'
SNAPSHOT_SCHEMA = 1

# ---- status labels (mirror technical-proposal-builder.html `SL`) -------------
SL = {
    'pending': 'Pending', 'approved': 'Approved', 'revision_required': 'Revision Required',
    'rejected': 'Rejected', 'na': 'N/A', 'not_started': 'Not Started',
    'ready_for_production': 'Ready for Production', 'in_production': 'In Production',
    'ready_for_installation': 'Ready for Installation', 'installed': 'Installed',
}
# ---- preset groups (mirror dashboard PRESET_GROUPS + builder presetRows) -----
_PRESET_ROWS = [
    ('Branding / Logo', ['branding', 'letterBuild'], None),
    ('Fabrication',     ['fabrication'],             None),
    ('Main Material',   ['material'],                None),
    ('Finish',          ['finish'],                  None),
    ('Existing Work',   ['existingWork'],            'existingWorkNote'),
    ('Lighting',        ['lighting', 'power'],       None),
    ('Access',          ['access'],                  'accessNote'),
    ('Environment',     ['environment'],             None),
]
_SPEC_KEYS = ['branding', 'letterBuild', 'fabrication', 'material', 'finish',
              'existingWork', 'lighting', 'power', 'access', 'environment']


# ── config ──────────────────────────────────────────────────────────────────

class PortalConfigError(RuntimeError):
    pass


SECRET_ENV = 'VISTA_PORTAL_SUPABASE_SECRET_KEY'


def load_config():
    """
    config.json holds ONLY the non-secret portal config:
        "vista_portal": { "supabase_url", "publishable_key", "bucket" }

    The Supabase secret key is NEVER stored in config.json — it is read only
    from the environment variable VISTA_PORTAL_SUPABASE_SECRET_KEY.

    Optional environment overrides (all non-secret):
        VISTA_PORTAL_URL, VISTA_PORTAL_IDENTITY_ID / _NAME / _EMAIL
    """
    try:
        with open(_CFG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        raise PortalConfigError('config.json could not be read: %s' % e)
    vp = cfg.get('vista_portal')
    if not isinstance(vp, dict):
        raise PortalConfigError(
            'config.json has no "vista_portal" block. Add it (see VISTA_PORTAL_PHASE1_SETUP.md).')

    url = vp.get('supabase_url') or vp.get('url')
    pub = vp.get('publishable_key') or vp.get('anon_key')
    if not url:
        raise PortalConfigError('vista_portal.supabase_url is missing in config.json')
    if not pub:
        raise PortalConfigError('vista_portal.publishable_key is missing in config.json')

    return {
        'url': url.rstrip('/'),
        'publishable_key': pub,
        'secret_key': os.environ.get(SECRET_ENV) or None,   # env-only; may be None
        'bucket': vp.get('bucket') or DEFAULT_BUCKET,
        'portal_url': (os.environ.get('VISTA_PORTAL_URL') or vp.get('portal_url')
                       or 'http://localhost:8080/client-portal.html'),
        'identity': {
            'id': os.environ.get('VISTA_PORTAL_IDENTITY_ID') or vp.get('identity_id') or 'vista:local',
            'name': os.environ.get('VISTA_PORTAL_IDENTITY_NAME') or vp.get('identity_name') or 'Vista United',
            'email': os.environ.get('VISTA_PORTAL_IDENTITY_EMAIL') or vp.get('identity_email') or 'portal@vista.local',
        },
    }


def require_secret(cfg):
    """The backend Supabase secret key — env var only, never config.json."""
    k = cfg.get('secret_key')
    if not k:
        raise PortalConfigError(
            'The Supabase secret key is not available. Set the environment variable %s '
            'in the shell that runs proxy.py (it is never read from config.json).' % SECRET_ENV)
    if k.startswith('sb_publishable_') or k == cfg.get('publishable_key'):
        raise PortalConfigError(
            '%s currently holds the PUBLISHABLE key. It must be the SECRET / service_role key '
            '(prefix "sb_secret_", or a legacy service_role JWT starting "eyJ").' % SECRET_ENV)
    return k


def secret_kind():
    """Classify the env secret WITHOUT revealing it: 'secret' | 'publishable' | 'jwt' | 'unknown' | 'missing'."""
    k = os.environ.get(SECRET_ENV) or ''
    if not k:
        return 'missing'
    if k.startswith('sb_secret_'):
        return 'secret'
    if k.startswith('sb_publishable_'):
        return 'publishable'
    if k.startswith('eyJ'):
        return 'jwt'
    return 'unknown'


def public_config():
    """The ONLY portal-facing config. Contains no secret of any kind.
    `redirectUrl` is the explicit magic-link target the portal passes as
    emailRedirectTo — set via config.json `portal_url` or env VISTA_PORTAL_URL."""
    c = load_config()
    return {'url': c['url'], 'publishableKey': c['publishable_key'], 'bucket': c['bucket'],
            'redirectUrl': c['portal_url']}


def is_configured():
    """True when the portal can at least boot (url + publishable key present).
    Privileged operations additionally require the secret env var (require_secret)."""
    try:
        load_config()
        return True
    except PortalConfigError:
        return False


def secret_available():
    """True only when the env var holds something usable as a backend secret key
    (a publishable key does not count)."""
    return secret_kind() in ('secret', 'jwt', 'unknown')


# ── Supabase REST client (backend secret key) ──────────────────────────────

def _req(method, url, *, retries=3, timeout=_TIMEOUT, **kw):
    """requests wrapper with a few retries for transient network / 5xx errors."""
    last = None
    for attempt in range(retries):
        try:
            r = requests.request(method, url, timeout=timeout, **kw)
            if r.status_code in (429, 502, 503, 504) and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            return r
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last


class Supa:
    def __init__(self, cfg):
        self.base = cfg['url'].rstrip('/')
        self.key = require_secret(cfg)
        self.bucket = cfg.get('bucket', DEFAULT_BUCKET)
        self.h = {'apikey': self.key, 'Authorization': 'Bearer ' + self.key}

    # -- PostgREST --
    def rest(self, method, table, *, params=None, json_body=None, prefer=None):
        headers = dict(self.h)
        headers['Content-Type'] = 'application/json'
        if prefer:
            headers['Prefer'] = prefer
        r = _req(method, '%s/rest/v1/%s' % (self.base, table),
                 headers=headers, params=params, json=json_body)
        if r.status_code >= 300:
            raise RuntimeError('Supabase %s %s -> %s %s' % (method, table, r.status_code, r.text[:600]))
        if r.text.strip():
            try:
                return r.json()
            except ValueError:
                return None
        return None

    def insert(self, table, row, prefer='return=representation'):
        out = self.rest('POST', table, json_body=row, prefer=prefer)
        return out[0] if isinstance(out, list) and out else out

    def upsert(self, table, row, on_conflict):
        out = self.rest('POST', table, params={'on_conflict': on_conflict}, json_body=row,
                        prefer='return=representation,resolution=merge-duplicates')
        return out[0] if isinstance(out, list) and out else out

    def select(self, table, *, params=None):
        return self.rest('GET', table, params=params) or []

    def rpc(self, fn, args):
        headers = dict(self.h)
        headers['Content-Type'] = 'application/json'
        r = requests.post('%s/rest/v1/rpc/%s' % (self.base, fn), headers=headers,
                          json=args, timeout=_TIMEOUT)
        if r.status_code >= 300:
            raise RuntimeError('Supabase rpc %s -> %s %s' % (fn, r.status_code, r.text[:400]))
        return r.json() if r.text.strip() else None

    # -- Auth admin --
    def admin_get_user_by_email(self, email):
        r = requests.get('%s/auth/v1/admin/users' % self.base, headers=self.h,
                         params={'page': 1, 'per_page': 200}, timeout=_TIMEOUT)
        r.raise_for_status()
        users = r.json().get('users', r.json()) if isinstance(r.json(), dict) else r.json()
        for u in users:
            if (u.get('email') or '').lower() == email.lower():
                return u
        return None

    def admin_create_user(self, email, full_name):
        existing = self.admin_get_user_by_email(email)
        if existing:
            return existing, False
        r = requests.post('%s/auth/v1/admin/users' % self.base, headers=self.h, timeout=_TIMEOUT,
                          json={'email': email, 'email_confirm': True,
                                'user_metadata': {'full_name': full_name}})
        if r.status_code >= 300:
            raise RuntimeError('create user %s -> %s %s' % (email, r.status_code, r.text[:400]))
        return r.json(), True

    def admin_magiclink(self, email, redirect_to=None):
        body = {'type': 'magiclink', 'email': email}
        if redirect_to:
            body['options'] = {'redirect_to': redirect_to}
        r = requests.post('%s/auth/v1/admin/generate_link' % self.base, headers=self.h,
                          json=body, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # -- Storage --
    def storage_upload(self, path, data, content_type):
        url = '%s/storage/v1/object/%s/%s' % (self.base, self.bucket, path)
        headers = dict(self.h)
        headers['Content-Type'] = content_type or 'application/octet-stream'
        headers['x-upsert'] = 'true'
        r = _req('POST', url, headers=headers, data=data, timeout=90, retries=4)
        if r.status_code >= 300:
            raise RuntimeError('storage upload %s -> %s %s' % (path, r.status_code, r.text[:300]))
        return path

    def storage_list_paths(self, prefix):
        url = '%s/storage/v1/object/list/%s' % (self.base, self.bucket)
        r = requests.post(url, headers={**self.h, 'Content-Type': 'application/json'},
                          json={'prefix': prefix, 'limit': 1000}, timeout=_TIMEOUT)
        r.raise_for_status()
        return [prefix.rstrip('/') + '/' + o['name'] for o in r.json() if o.get('name')]


# ── helpers ─────────────────────────────────────────────────────────────────

def _has(v):
    return v is not None and str(v).strip() != ''


def _nn(v):
    return str(v).strip() if _has(v) else ''


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def content_hash(snapshot):
    return hashlib.sha256(canonical_json(snapshot).encode('utf-8')).hexdigest()


def _qty(q):
    q = q or {}
    return {'value': q.get('value') if q.get('value') not in (None, '') else None,
            'unit': _nn(q.get('unit'))}


def _dims_line(d):
    d = d or {}
    axes = [str(d[k]).strip() for k in ('w', 'h', 'd') if _has(d.get(k))]
    parts = []
    if axes:
        parts.append(' × '.join(axes) + ' mm')
    if _has(d.get('mountingHeight')):
        parts.append('mounting height %s mm' % _nn(d['mountingHeight']))
    if _has(d.get('projection')):
        parts.append('projection %s mm' % _nn(d['projection']))
    return ' · '.join(parts)


def _spec_rows(presets):
    P = presets or {}

    def gv(k):
        return [x for x in ((P.get(k) or {}).get('values') or []) if _has(x)]

    def go(k):
        return _nn((P.get(k) or {}).get('other'))

    rows = []
    for label, keys, note_key in _PRESET_ROWS:
        vals = []
        for k in keys:
            vals += gv(k)
        others = [go(k) for k in keys if go(k)]
        note = _nn(P.get(note_key)) if note_key else ''
        bits = list(vals)
        if note:
            bits.append(note)
        if others:
            bits.append(' / '.join(others))
        if bits:
            rows.append({'key': keys[0], 'label': label, 'value': ' · '.join(bits)})
    return rows


# ── snapshot (client-safe projection) ──────────────────────────────────────

# fields that must NEVER appear in a snapshot — asserted by build_snapshot
_FORBIDDEN_SUBSTRINGS = (
    'unitPrice', 'currency', 'lineTotal', 'pricing', 'supplier', 'quotation',
    'criticalInstaller', 'sampleBy', 'sampleComment', 'designComment', 'sampleRev',
    'sampleDate', 'impactIfLate', 'ruleKey', 'dismissedRuleKeys', 'execution',
    'riskBuffer', 'activities', 'dependsOn', 'duplicatedFrom', 'keepSource', 'notes.site',
)


def build_snapshot(project_id):
    project = tp_db.get_project(project_id)
    if not project:
        raise KeyError('project %r not found in central DB' % project_id)
    photos = tp_db.list_photos(project_id)
    photo_by_id = {p['id']: p for p in photos}

    rev = _nn(project.get('revision')) or 'R1'
    client = _nn(project.get('client'))
    name = _nn(project.get('name')) or 'Untitled Project'
    doc_ref = _nn(project.get('docRef')) or (
        'VU-' + (re.sub(r'[^A-Z0-9]+', '', (client or name).upper())[:6] or 'PROJ') + '-TP-' + rev)
    date_iss = _nn(project.get('dateOfIssue'))

    items_out = []
    photo_manifest = []  # (item_ref, slot, ordinal, photo_row)

    for it in (project.get('items') or []):
        ref = _nn(it.get('number')) or str(len(items_out) + 1).zfill(2)
        st = it.get('status') or {}
        cins = []
        for i, c in enumerate([c for c in (it.get('clientInputs') or []) if _has(c.get('text'))]):
            cins.append({
                'ref': 'ci#%d' % i,
                'priority': c.get('priority') or 'required',
                'text': _nn(c.get('text')),
                'requiredByDay': c.get('requiredByDay'),
                'clientAction': _nn(c.get('clientAction')),
                'clientActionAr': _nn(c.get('clientActionAr')),
            })

        # photos
        ex_ids = it.get('existingPhotoIds') or []
        if not ex_ids and (it.get('photoIds') or {}).get('existing'):
            ex_ids = [it['photoIds']['existing']]
        pj = it.get('photoIds') or {}
        pics = {'existing': [], 'simulation': None, 'sample': None}
        for ord_i, pid in enumerate(ex_ids):
            row = photo_by_id.get(pid)
            if row:
                pics['existing'].append(_photo_ref(ref, 'existing', ord_i, row))
                photo_manifest.append((ref, 'existing', ord_i, row))
        if pj.get('simulation') and photo_by_id.get(pj['simulation']):
            row = photo_by_id[pj['simulation']]
            pics['simulation'] = _photo_ref(ref, 'simulation', 0, row)
            photo_manifest.append((ref, 'simulation', 0, row))
        if (st.get('sample') == 'approved') and pj.get('sample') and photo_by_id.get(pj['sample']):
            row = photo_by_id[pj['sample']]
            pics['sample'] = _photo_ref(ref, 'sample', 0, row)
            photo_manifest.append((ref, 'sample', 0, row))

        items_out.append({
            'ref': ref,
            'name': _nn(it.get('name')) or 'Untitled item',
            'locationRef': _nn(it.get('locationRef')),
            'qty': _qty(it.get('qty')),
            'dims': {k: _nn((it.get('dims') or {}).get(k))
                     for k in ('w', 'h', 'd', 'mountingHeight', 'projection')},
            'dimsLine': _dims_line(it.get('dims')),
            'scope': _nn((it.get('notes') or {}).get('scope')),
            'technical': _nn((it.get('notes') or {}).get('technical')),
            'specifications': _spec_rows(it.get('presets')),
            'status': {
                'design': SL.get(st.get('design', 'pending'), 'Pending'),
                'sample': SL.get(st.get('sample', 'na'), 'N/A'),
                'production': SL.get(st.get('production', 'not_started'), 'Not Started'),
            },
            'clientInputs': cins,
            'photos': pics,
        })

    snapshot = {
        'snapshotSchema': SNAPSHOT_SCHEMA,
        'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'project': {
            'name': name,
            'descriptor': _nn(project.get('descriptor')) or name,
            'client': client,
            'location': _nn(project.get('location')),
            'revision': rev,
            'dateOfIssue': date_iss,
            'docRef': doc_ref,
            'preparedBy': _nn(project.get('preparedBy')) or 'Vista United — Projects & Technical Department',
        },
        'items': items_out,
        'approvalTargets': [],  # Phase 1 read-only: no approval yet
    }

    # hard safety net — the projection is allow-list built above, this just proves it
    blob = canonical_json(snapshot)
    leaked = [s for s in _FORBIDDEN_SUBSTRINGS if s.split('.')[-1].lower() in blob.lower()
              and s in ('supplier', 'quotation')]  # only flag the unambiguous ones
    if leaked:
        raise RuntimeError('snapshot projection leak: %s' % leaked)
    return snapshot, photo_manifest


def _photo_ref(item_ref, slot, ordinal, row):
    ext = mimetypes.guess_extension(row.get('mime') or '') or '.jpg'
    if ext == '.jpe':
        ext = '.jpg'
    return {
        'ref': '%s-%s-%d' % (item_ref, slot, ordinal),
        'slot': slot, 'ordinal': ordinal,
        'w': row.get('w'), 'h': row.get('h'), 'mime': row.get('mime'),
        'ext': ext,
    }


# ── field-token whitelist (Phase 0 spec §9) ────────────────────────────────
# The ONLY tokens a client may ever propose against. Generated per revision
# from the snapshot; frozen into portal_field_defs. A token absent here cannot
# be submitted (the Postgres validation trigger rejects it).

def build_field_defs(revision_id, snapshot, allow_edits=True):
    """Emit portal_field_defs rows for one revision.
       allow_edits=False -> everything is 'view' (Phase 1 behaviour).
       allow_edits=True  -> the Phase 2 proposable + commentable surface."""
    defs = []

    def add(token, perm, dtype, label, item_ref, current, constraints):
        defs.append({'revision_id': revision_id, 'field_token': token, 'permission': perm,
                     'data_type': dtype, 'label': label, 'item_ref': item_ref,
                     'current_value': current, 'constraints': constraints or {}})

    perm = 'propose' if allow_edits else 'view'
    for it in snapshot['items']:
        r = it['ref']
        nm = 'Item %s' % r
        add('item.%s' % r, 'comment' if allow_edits else 'view', 'string',
            nm, r, None, {})
        # quantity
        qv = it['qty'].get('value')
        add('item.%s.qty.value' % r, perm, 'integer', '%s — Quantity' % nm, r,
            (int(qv) if isinstance(qv, (int, float)) or (isinstance(qv, str) and qv.strip().lstrip('-').isdigit()) else qv),
            {'min': 1, 'max': 100000})
        # dimensions
        for axis, alabel in (('w', 'Width'), ('h', 'Height'), ('d', 'Depth'),
                             ('mountingHeight', 'Mounting height'), ('projection', 'Projection')):
            add('item.%s.dims.%s' % (r, axis), perm, 'string',
                '%s — %s (mm)' % (nm, alabel), r, it['dims'].get(axis, ''), {'maxLength': 40})
        # scope + free technical note
        add('item.%s.scope' % r, perm, 'string', '%s — Scope' % nm, r, it.get('scope', ''), {'maxLength': 4000})
        add('item.%s.technical' % r, perm, 'string', '%s — Technical notes' % nm, r,
            it.get('technical', ''), {'maxLength': 4000})
        # per-spec-row wording
        for sp in it.get('specifications', []):
            add('item.%s.spec.%s' % (r, sp['key']), perm, 'string',
                '%s — %s' % (nm, sp['label']), r, sp['value'], {'maxLength': 400})
        # client inputs: response + client-action wording
        for c in it.get('clientInputs', []):
            cr = c['ref']  # 'ci#0'
            add('item.%s.clientInput.%s.response' % (r, cr), perm, 'string',
                '%s — Response: %s' % (nm, _clip(c['text'], 60)), r, '', {'maxLength': 4000})
            add('item.%s.clientInput.%s.clientAction' % (r, cr), perm, 'string',
                '%s — Client action wording' % nm, r, c.get('clientAction', ''), {'maxLength': 600})
    return defs


def _clip(s, n):
    s = str(s or '')
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'


def approval_targets(snapshot):
    """One approvable target per client-input that carries a client action."""
    out = []
    for it in snapshot['items']:
        for c in it.get('clientInputs', []):
            if _has(c.get('clientAction')):
                out.append('approval.item.%s.%s' % (it['ref'], c['ref']))
    return out


# ── apply resolver — the ONLY path from an accepted change into the master ──
# Static regex -> handler. Capture groups feed explicit lookups; the assignment
# target is always a literal property. No arbitrary path traversal anywhere.

def _item_by_number(proj, ref):
    for it in (proj.get('items') or []):
        if _nn(it.get('number')) == ref:
            return it
    raise KeyError('item %s not found in the internal project' % ref)


def _ci_by_index(item, n):
    kept = [c for c in (item.get('clientInputs') or []) if _has(c.get('text'))]
    if n < 0 or n >= len(kept):
        raise KeyError('client input ci#%d not found' % n)
    return kept[n]


def _apply_qty(proj, m, v):
    it = _item_by_number(proj, m['ref'])
    n = int(round(float(v)))
    if not (1 <= n <= 100000):
        raise ValueError('quantity %s out of range' % n)
    it.setdefault('qty', {})['value'] = n


def _apply_dim(proj, m, v):
    _item_by_number(proj, m['ref']).setdefault('dims', {})[m['axis']] = str(v)[:40]


def _apply_scope(proj, m, v):
    _item_by_number(proj, m['ref']).setdefault('notes', {})['scope'] = str(v)[:4000]


def _apply_technical(proj, m, v):
    _item_by_number(proj, m['ref']).setdefault('notes', {})['technical'] = str(v)[:4000]


def _apply_spec(proj, m, v):
    it = _item_by_number(proj, m['ref'])
    grp = it.setdefault('presets', {}).setdefault(m['key'], {'values': [], 'other': ''})
    grp['other'] = str(v)[:400]


def _apply_ci_response(proj, m, v):
    c = _ci_by_index(_item_by_number(proj, m['ref']), int(m['n']))
    c['clientResponse'] = str(v)[:4000]
    c['clientResponseAt'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _apply_ci_action(proj, m, v):
    _ci_by_index(_item_by_number(proj, m['ref']), int(m['n']))['clientAction'] = str(v)[:600]


_APPLY_HANDLERS = [
    (re.compile(r'^item\.(?P<ref>\d{2,3})\.qty\.value$'), _apply_qty),
    (re.compile(r'^item\.(?P<ref>\d{2,3})\.dims\.(?P<axis>w|h|d|mountingHeight|projection)$'), _apply_dim),
    (re.compile(r'^item\.(?P<ref>\d{2,3})\.scope$'), _apply_scope),
    (re.compile(r'^item\.(?P<ref>\d{2,3})\.technical$'), _apply_technical),
    (re.compile(r'^item\.(?P<ref>\d{2,3})\.spec\.(?P<key>[A-Za-z]+)$'), _apply_spec),
    (re.compile(r'^item\.(?P<ref>\d{2,3})\.clientInput\.ci#(?P<n>\d{1,3})\.response$'), _apply_ci_response),
    (re.compile(r'^item\.(?P<ref>\d{2,3})\.clientInput\.ci#(?P<n>\d{1,3})\.clientAction$'), _apply_ci_action),
]


def _resolve_handler(field_token):
    for rx, fn in _APPLY_HANDLERS:
        mm = rx.match(field_token or '')
        if mm:
            return fn, mm.groupdict()
    return None, None


# ── publish ─────────────────────────────────────────────────────────────────

def publish(project_id, client_name, users, *, title=None, revision_label=None, actor=None,
            grants=None):
    """
    users:  list of {"email": ..., "name": ...}
    grants: {"comment": bool, "propose": bool, "approve": bool} — the edit surface
            offered to every granted user. Default: all False (read-only, Phase 1).
    Returns a summary dict.
    """
    cfg = load_config()
    supa = Supa(cfg)
    ident = actor or cfg['identity']
    identity_email = ident.get('email', 'portal@vista.local')
    identity_name = ident.get('name', 'Vista United')
    portal_url = cfg.get('portal_url') or 'http://localhost:8080/client-portal.html'
    grants = grants or {}
    g_comment = bool(grants.get('comment'))
    g_propose = bool(grants.get('propose'))
    g_approve = bool(grants.get('approve'))
    allow_edits = g_propose or g_approve or g_comment

    snapshot, manifest = build_snapshot(project_id)
    if allow_edits:
        snapshot['approvalTargets'] = approval_targets(snapshot) if g_approve else []
    title = title or snapshot['project']['name']
    revision_label = revision_label or snapshot['project']['revision'] or 'R1'
    chash = content_hash(snapshot)

    tp_portal_db.init_db()
    link = tp_portal_db.get_link(project_id) or {}

    # 1) client org
    client_id = link.get('cloud_client_id')
    if not client_id:
        existing = supa.select('portal_clients', params={'name': 'eq.%s' % client_name, 'select': 'id', 'limit': 1})
        if existing:
            client_id = existing[0]['id']
        else:
            row = supa.insert('portal_clients', {'name': client_name, 'created_by': identity_email})
            client_id = row['id']

    # 2) client users (auth + portal_client_users)
    user_rows = []
    for u in users:
        email = u['email'].strip().lower()
        full_name = u.get('name') or email.split('@')[0]
        auth_user, created = supa.admin_create_user(email, full_name)
        uid = auth_user['id']
        supa.upsert('portal_client_users', {
            'id': uid, 'client_id': client_id, 'email': email, 'full_name': full_name,
            'role': 'viewer', 'status': 'invited', 'invited_by': identity_email,
        }, on_conflict='id')
        link_out = None
        try:
            link_out = supa.admin_magiclink(email, redirect_to=portal_url).get('action_link')
        except Exception:  # noqa: BLE001
            link_out = None
        user_rows.append({'id': uid, 'email': email, 'name': full_name,
                          'created': created, 'magic_link': link_out})

    # 3) proposal
    proposal_id = link.get('cloud_proposal_id')
    if not proposal_id:
        existing = supa.select('portal_proposals',
                               params={'internal_project_id': 'eq.%s' % project_id, 'select': 'id', 'limit': 1})
        if existing:
            proposal_id = existing[0]['id']
        else:
            row = supa.insert('portal_proposals', {
                'internal_project_id': project_id, 'client_id': client_id,
                'title': title, 'status': 'active', 'created_by': identity_email})
            proposal_id = row['id']

    supersedes = None
    prev = supa.select('portal_revisions', params={
        'proposal_id': 'eq.%s' % proposal_id, 'select': 'id,revision_label',
        'order': 'published_at.desc', 'limit': 1})
    if prev:
        supersedes = prev[0]['id']
        # auto-suffix if the label collides
        taken = {r['revision_label'] for r in supa.select('portal_revisions', params={
            'proposal_id': 'eq.%s' % proposal_id, 'select': 'revision_label'})}
        if revision_label in taken:
            n = 2
            while '%s-%s' % (revision_label, chr(95 + n)) in taken:
                n += 1
            revision_label = '%s-%s' % (revision_label, chr(95 + n))  # R1-b, R1-c ...

    revision = supa.insert('portal_revisions', {
        'proposal_id': proposal_id, 'revision_label': revision_label,
        'snapshot_schema': SNAPSHOT_SCHEMA, 'snapshot_jsonb': snapshot,
        'content_hash': chash, 'supersedes_revision_id': supersedes,
        'published_by': identity_email, 'published_by_name': identity_name})
    revision_id = revision['id']

    # 4) photos -> storage + portal_revision_photos
    photo_rows = []
    for (item_ref, slot, ordinal, prow) in manifest:
        got = tp_db.get_photo_bytes(prow['id'])
        if not got:
            continue
        data, mime, _meta = got
        ext = mimetypes.guess_extension(mime or '') or '.jpg'
        if ext == '.jpe':
            ext = '.jpg'
        path = '%s/%s/%s-%s-%d%s' % (proposal_id, revision_id, item_ref, slot, ordinal, ext)
        supa.storage_upload(path, data, mime)
        photo_rows.append({
            'revision_id': revision_id, 'item_ref': item_ref, 'slot': slot, 'ordinal': ordinal,
            'storage_path': path, 'mime': mime, 'w': prow.get('w'), 'h': prow.get('h'),
            'sha256': hashlib.sha256(data).hexdigest()})
    if photo_rows:
        supa.rest('POST', 'portal_revision_photos', json_body=photo_rows, prefer='return=minimal')

    # 5) field defs (whitelist for this revision)
    fds = build_field_defs(revision_id, snapshot, allow_edits=allow_edits)
    if fds:
        supa.rest('POST', 'portal_field_defs',
                  json_body=[{**d, 'constraints': d['constraints']} for d in fds],
                  prefer='return=minimal')

    # 6) proposal pointer + grants
    supa.rest('PATCH', 'portal_proposals', params={'id': 'eq.%s' % proposal_id},
              json_body={'current_revision_id': revision_id, 'title': title}, prefer='return=minimal')
    for ur in user_rows:
        supa.upsert('portal_proposal_grants', {
            'proposal_id': proposal_id, 'client_user_id': ur['id'],
            'can_comment': g_comment, 'can_propose': g_propose, 'can_approve': g_approve,
            'granted_by': identity_email, 'revoked_at': None},
            on_conflict='proposal_id,client_user_id')

    # 7) audit
    def log(event, **kw):
        try:
            supa.rpc('portal_log_event', {
                'p_proposal': proposal_id, 'p_revision': revision_id,
                'p_actor_type': 'vista', 'p_actor_id': identity_email, 'p_actor_name': identity_name,
                'p_event': event, **kw})
        except Exception:  # noqa: BLE001
            pass

    log('revision.published', p_note='%s %s' % (title, revision_label))
    for ur in user_rows:
        if ur['created']:
            log('client.invited', p_object_type='client_user', p_object_id=ur['id'], p_note=ur['email'])
        log('grant.added', p_object_type='grant', p_object_id=ur['id'], p_note=ur['email'])

    # 8) internal link + publish history
    now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    tp_portal_db.upsert_link(project_id, cloud_proposal_id=proposal_id, cloud_client_id=client_id,
                             portal_status='published', last_published_revision=revision_label,
                             last_published_at=now)
    _append_publish_history(project_id, revision_label, revision_id, now, identity_email)

    return {
        'ok': True, 'proposalId': proposal_id, 'revisionId': revision_id,
        'revisionLabel': revision_label, 'contentHash': chash,
        'photos': len(photo_rows), 'items': len(snapshot['items']),
        'portalUrl': portal_url,
        'users': [{'email': u['email'], 'name': u['name'], 'new': u['created'],
                   'magicLink': u['magic_link']} for u in user_rows],
    }


def _append_publish_history(project_id, revision_label, cloud_revision_id, when, by):
    proj = tp_db.get_project(project_id)
    if not proj:
        return
    hist = proj.get('publishHistory')
    if not isinstance(hist, list):
        hist = []
    hist.append({'revision': revision_label, 'cloudRevisionId': cloud_revision_id,
                 'publishedAt': when, 'publishedBy': by})
    proj['publishHistory'] = hist
    proj['updatedAt'] = int(datetime.datetime.utcnow().timestamp() * 1000)
    tp_db.upsert_project(proj)


# ── pull (Phase 1: audit mirror only) ──────────────────────────────────────

def pull(project_id):
    supa = Supa(load_config())
    link = tp_portal_db.get_link(project_id)
    if not link or not link.get('cloud_proposal_id'):
        return {'ok': True, 'pulled': 0, 'note': 'not published'}
    rows = supa.select('portal_audit_events', params={
        'proposal_id': 'eq.%s' % link['cloud_proposal_id'],
        'order': 'id.asc', 'limit': 5000})
    n = tp_portal_db.mirror_audit_rows(rows, internal_project_id=project_id)
    tp_portal_db.touch_pull(project_id)
    return {'ok': True, 'pulled': len(rows), 'mirrored_new': n}


def status(project_id):
    link = tp_portal_db.get_link(project_id)
    out = {'configured': is_configured(), 'secretAvailable': secret_available(), 'link': link}
    if not link or not is_configured() or not secret_available():
        return out
    try:
        supa = Supa(load_config())
        pid = link.get('cloud_proposal_id')
        if pid:
            grants = supa.select('portal_proposal_grants', params={
                'proposal_id': 'eq.%s' % pid, 'select': 'client_user_id,revoked_at,granted_at'})
            users = supa.select('portal_client_users', params={
                'select': 'id,email,full_name,status', 'limit': 500})
            umap = {u['id']: u for u in users}
            out['grants'] = [{
                'email': umap.get(g['client_user_id'], {}).get('email'),
                'name': umap.get(g['client_user_id'], {}).get('full_name'),
                'status': umap.get(g['client_user_id'], {}).get('status'),
                'revoked': bool(g.get('revoked_at')),
            } for g in grants]
            revs = supa.select('portal_revisions', params={
                'proposal_id': 'eq.%s' % pid, 'select': 'revision_label,published_at,published_by_name,content_hash',
                'order': 'published_at.desc'})
            out['revisions'] = revs
    except Exception as e:  # noqa: BLE001
        out['error'] = str(e)
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 2 — client change sets: Vista-side review + accept/reject
# ══════════════════════════════════════════════════════════════════════════════

def client_activity(project_id):
    """Live view of submitted client change sets for one internal project."""
    supa = Supa(load_config())
    link = tp_portal_db.get_link(project_id)
    if not link or not link.get('cloud_proposal_id'):
        return {'ok': True, 'changeSets': [], 'note': 'not published'}
    pid = link['cloud_proposal_id']

    css = supa.select('portal_change_sets', params={
        'proposal_id': 'eq.%s' % pid, 'order': 'submitted_at.desc.nullslast,created_at.desc'})
    users = {u['id']: u for u in supa.select('portal_client_users', params={'select': 'id,email,full_name'})}
    revs = {r['id']: r['revision_label'] for r in supa.select('portal_revisions', params={
        'proposal_id': 'eq.%s' % pid, 'select': 'id,revision_label'})}
    crs = supa.select('portal_change_requests', params={
        'proposal_id': 'eq.%s' % pid, 'order': 'submitted_at.asc'})
    coms = supa.select('portal_comments', params={
        'proposal_id': 'eq.%s' % pid, 'order': 'created_at.asc'})
    aps = supa.select('portal_approvals', params={
        'proposal_id': 'eq.%s' % pid, 'order': 'signed_at.asc'})

    by_cs_cr, by_cs_com, by_cs_ap = {}, {}, {}
    for x in crs:
        by_cs_cr.setdefault(x.get('change_set_id'), []).append(x)
    for x in coms:
        by_cs_com.setdefault(x.get('change_set_id'), []).append(x)
    for x in aps:
        by_cs_ap.setdefault(x.get('change_set_id'), []).append(x)

    out = []
    for cs in css:
        if cs['status'] == 'draft':
            continue
        u = users.get(cs['client_user_id'], {})
        out.append({
            'id': cs['id'], 'csNumber': cs['cs_number'], 'status': cs['status'],
            'revisionLabel': revs.get(cs['revision_id'], '?'),
            'clientName': u.get('full_name'), 'clientEmail': u.get('email'),
            'signerName': cs.get('signer_name'), 'signerTitle': cs.get('signer_title'),
            'signerCompany': cs.get('signer_company'), 'signatureType': cs.get('signature_type'),
            'signed': bool(cs.get('signed_at')), 'signedAt': cs.get('signed_at'),
            'submittedAt': cs.get('submitted_at'), 'confirmed': cs.get('confirmed'),
            'ip': cs.get('ip'), 'userAgent': cs.get('user_agent'),
            'decidedBy': cs.get('decided_by_name'), 'decidedAt': cs.get('decided_at'),
            'changes': [{
                'id': c['id'], 'fieldToken': c['field_token'], 'itemRef': c.get('item_ref'),
                'label': c['field_label'], 'kind': c.get('kind'),
                'oldValue': c.get('old_value'), 'newValue': c.get('new_value'),
                'clientNote': c.get('client_note'), 'state': c['state'],
                'decidedBy': c.get('decided_by_name'), 'decisionNote': c.get('decision_note'),
                'appliedAt': c.get('applied_to_internal_at'),
            } for c in by_cs_cr.get(cs['id'], [])],
            'comments': [{
                'id': c['id'], 'body': c['body'], 'targetToken': c.get('target_token'),
                'itemRef': c.get('item_ref'), 'authorType': c['author_type'],
                'authorName': c['author_name'], 'createdAt': c['created_at'],
            } for c in by_cs_com.get(cs['id'], [])],
            'approvals': [{
                'id': a['id'], 'token': a['approval_token'], 'itemRef': a.get('item_ref'),
                'decision': a['decision'], 'signedName': a['signed_name'],
                'statement': a.get('statement_shown'), 'signedAt': a['signed_at'],
            } for a in by_cs_ap.get(cs['id'], [])],
        })
    return {'ok': True, 'changeSets': out}


def _cr_row(supa, cr_id):
    rows = supa.select('portal_change_requests', params={'id': 'eq.%s' % cr_id, 'limit': 1})
    if not rows:
        raise KeyError('change request %s not found' % cr_id)
    return rows[0]


def _log_vista(supa, proposal_id, revision_id, event, **kw):
    cfg = load_config()
    try:
        supa.rpc('portal_log_event', {
            'p_proposal': proposal_id, 'p_revision': revision_id, 'p_actor_type': 'vista',
            'p_actor_id': cfg['identity']['email'], 'p_actor_name': cfg['identity']['name'],
            'p_event': event, **kw})
    except Exception:  # noqa: BLE001
        pass


def decide_change(cr_id, decision, note=None, edited_value=None, _project_id=None):
    """decision: 'accept' | 'reject'. On accept the value is written into the
    Vista central DB through the static resolver (validated again). edited_value
    (Vista's 'edit before accept') overrides the client's proposed value."""
    cfg = load_config()
    supa = Supa(cfg)
    cr = _cr_row(supa, cr_id)
    if cr['state'] != 'pending':
        return {'ok': False, 'error': 'change is already %s' % cr['state']}

    ident = cfg['identity']
    now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    project_id = _project_id or _internal_project_for(supa, cr['proposal_id'])

    if decision == 'reject':
        supa.rest('PATCH', 'portal_change_requests', params={'id': 'eq.%s' % cr_id},
                  json_body={'state': 'rejected', 'decided_by': ident['email'],
                             'decided_by_name': ident['name'], 'decided_at': now,
                             'decision_note': note or ''}, prefer='return=minimal')
        _log_vista(supa, cr['proposal_id'], cr['revision_id'], 'change_request.rejected',
                   p_object_type='change_request', p_object_id=cr_id,
                   p_field_token=cr['field_token'], p_note=note or '')
        tp_portal_db.record_inbound(cr_id, project_id, cr, state='rejected', decided_by=ident['name'])
        if cr.get('change_set_id'):
            _finalise_change_set(supa, cr['change_set_id'])
        return {'ok': True, 'state': 'rejected'}

    if decision != 'accept':
        return {'ok': False, 'error': 'unknown decision %r' % decision}

    # ACCEPT -> apply to the master
    fn, m = _resolve_handler(cr['field_token'])
    if not fn:
        return {'ok': False, 'error': 'no resolver for token %s (refused)' % cr['field_token']}
    value = edited_value if edited_value is not None else _json_scalar(cr['new_value'])

    proj = tp_db.get_project(project_id)
    if not proj:
        return {'ok': False, 'error': 'internal project %s not found' % project_id}
    old_master = _current_master_value(proj, cr['field_token'])
    try:
        fn(proj, m, value)
    except (KeyError, ValueError) as e:
        return {'ok': False, 'error': 'apply failed: %s' % e}
    proj['updatedAt'] = int(datetime.datetime.utcnow().timestamp() * 1000)
    tp_db.upsert_project(proj)

    supa.rest('PATCH', 'portal_change_requests', params={'id': 'eq.%s' % cr_id},
              json_body={'state': 'accepted', 'decided_by': ident['email'],
                         'decided_by_name': ident['name'], 'decided_at': now,
                         'decision_note': note or '', 'applied_to_internal_at': now,
                         'new_value': json.loads(json.dumps(value)) if not isinstance(value, str) else value},
              prefer='return=minimal')
    _log_vista(supa, cr['proposal_id'], cr['revision_id'], 'change_request.accepted',
               p_object_type='change_request', p_object_id=cr_id, p_field_token=cr['field_token'],
               p_old=cr['old_value'], p_new=json.loads(json.dumps(value)) if not isinstance(value, str) else value,
               p_note=note or '')
    _log_vista(supa, cr['proposal_id'], cr['revision_id'], 'change.applied_to_internal',
               p_field_token=cr['field_token'],
               p_old=json.loads(json.dumps(old_master)) if not isinstance(old_master, str) else old_master,
               p_new=json.loads(json.dumps(value)) if not isinstance(value, str) else value,
               p_note='Vista master updated')
    tp_portal_db.record_inbound(cr_id, project_id, cr, state='accepted', decided_by=ident['name'])
    if cr.get('change_set_id'):
        _finalise_change_set(supa, cr['change_set_id'])
    return {'ok': True, 'state': 'accepted', 'appliedValue': value}


def decide_change_set(cs_id, decision, _project_id=None):
    """decision: 'accept_all' | 'reject_all'."""
    cfg = load_config()
    supa = Supa(cfg)
    css = supa.select('portal_change_sets', params={'id': 'eq.%s' % cs_id, 'limit': 1})
    if not css:
        raise KeyError('change set %s not found' % cs_id)
    cs = css[0]
    project_id = _project_id or _internal_project_for(supa, cs['proposal_id'])
    crs = supa.select('portal_change_requests', params={
        'change_set_id': 'eq.%s' % cs_id, 'state': 'eq.pending', 'select': 'id'})
    per = []
    for c in crs:
        per.append(decide_change(c['id'], 'accept' if decision == 'accept_all' else 'reject',
                                 note='(%s)' % decision, _project_id=project_id))
    accepted = sum(1 for r in per if r.get('state') == 'accepted')
    rejected = sum(1 for r in per if r.get('state') == 'rejected')
    _finalise_change_set(supa, cs_id)
    return {'ok': True, 'accepted': accepted, 'rejected': rejected,
            'errors': [r for r in per if not r.get('ok')]}


def _finalise_change_set(supa, cs_id):
    """Move a change set to its terminal status once every child change is decided."""
    cfg = load_config()
    crs = supa.select('portal_change_requests', params={
        'change_set_id': 'eq.%s' % cs_id, 'select': 'state'})
    states = {c['state'] for c in crs}
    if 'pending' in states:
        return
    if not crs:
        final = 'accepted'
    elif states == {'accepted'}:
        final = 'accepted'
    elif states == {'rejected'}:
        final = 'rejected'
    else:
        final = 'partially_accepted'
    now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    supa.rest('PATCH', 'portal_change_sets', params={'id': 'eq.%s' % cs_id},
              json_body={'status': final, 'decided_at': now,
                         'decided_by': cfg['identity']['email'],
                         'decided_by_name': cfg['identity']['name']}, prefer='return=minimal')


def _internal_project_for(supa, proposal_id):
    rows = supa.select('portal_proposals', params={
        'id': 'eq.%s' % proposal_id, 'select': 'internal_project_id', 'limit': 1})
    if not rows:
        raise KeyError('cloud proposal %s not found' % proposal_id)
    return rows[0]['internal_project_id']


def _json_scalar(v):
    """A jsonb scalar coming back from PostgREST is already a py value."""
    return v


def _current_master_value(proj, field_token):
    fn, m = _resolve_handler(field_token)
    if not fn:
        return None
    try:
        if 'qty' in field_token:
            return _item_by_number(proj, m['ref']).get('qty', {}).get('value')
        if '.dims.' in field_token:
            return _item_by_number(proj, m['ref']).get('dims', {}).get(m['axis'])
        if field_token.endswith('.scope'):
            return _item_by_number(proj, m['ref']).get('notes', {}).get('scope')
        if field_token.endswith('.technical'):
            return _item_by_number(proj, m['ref']).get('notes', {}).get('technical')
        if '.spec.' in field_token:
            return (_item_by_number(proj, m['ref']).get('presets', {}).get(m['key'], {}) or {}).get('other')
        if field_token.endswith('.response'):
            return _ci_by_index(_item_by_number(proj, m['ref']), int(m['n'])).get('clientResponse')
        if field_token.endswith('.clientAction'):
            return _ci_by_index(_item_by_number(proj, m['ref']), int(m['n'])).get('clientAction')
    except (KeyError, ValueError):
        return None
    return None


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Vista portal bridge (Phase 1)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('snapshot'); s.add_argument('project_id')
    s = sub.add_parser('publish')
    s.add_argument('project_id'); s.add_argument('--client', required=True)
    s.add_argument('--user', action='append', required=True,
                   help='"Full Name <email@x.com>"  (repeatable)')
    s.add_argument('--title'); s.add_argument('--revision')
    s.add_argument('--allow-edits', action='store_true',
                   help='Phase 2: grant comment + propose + approve to every user')
    s.add_argument('--allow', default='', help='csv of comment,propose,approve (overrides --allow-edits)')
    s = sub.add_parser('pull'); s.add_argument('project_id')
    s = sub.add_parser('status'); s.add_argument('project_id')
    s = sub.add_parser('activity'); s.add_argument('project_id')
    s = sub.add_parser('decide')
    s.add_argument('project_id'); s.add_argument('--cr'); s.add_argument('--cs')
    s.add_argument('--decision', required=True,
                   choices=['accept', 'reject', 'accept_all', 'reject_all'])
    s.add_argument('--note', default=None); s.add_argument('--edit', default=None)

    a = ap.parse_args()
    if a.cmd == 'snapshot':
        snap, man = build_snapshot(a.project_id)
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        print('\nphotos to upload:', len(man))
    elif a.cmd == 'publish':
        users = []
        for u in a.user:
            m = re.match(r'\s*(.*?)\s*<\s*([^>]+)\s*>\s*$', u) or re.match(r'^\s*()(\S+@\S+)\s*$', u)
            if not m:
                raise SystemExit('bad --user %r (use "Name <email>")' % u)
            users.append({'name': m.group(1).strip(), 'email': m.group(2).strip()})
        if a.allow:
            allow = {k.strip(): True for k in a.allow.split(',') if k.strip()}
            grants = {'comment': allow.get('comment', False), 'propose': allow.get('propose', False),
                      'approve': allow.get('approve', False)}
        elif a.allow_edits:
            grants = {'comment': True, 'propose': True, 'approve': True}
        else:
            grants = None
        print(json.dumps(publish(a.project_id, a.client, users, title=a.title,
                                 revision_label=a.revision, grants=grants), indent=2, ensure_ascii=False))
    elif a.cmd == 'pull':
        print(json.dumps(pull(a.project_id), indent=2))
    elif a.cmd == 'status':
        print(json.dumps(status(a.project_id), indent=2, ensure_ascii=False))
    elif a.cmd == 'activity':
        print(json.dumps(client_activity(a.project_id), indent=2, ensure_ascii=False))
    elif a.cmd == 'decide':
        if a.decision in ('accept', 'reject'):
            if not a.cr:
                raise SystemExit('--cr <change_request_id> required')
            print(json.dumps(decide_change(a.cr, a.decision, note=a.note, edited_value=a.edit,
                                           _project_id=a.project_id), indent=2, ensure_ascii=False))
        else:
            if not a.cs:
                raise SystemExit('--cs <change_set_id> required')
            print(json.dumps(decide_change_set(a.cs, a.decision, _project_id=a.project_id),
                             indent=2, ensure_ascii=False))
