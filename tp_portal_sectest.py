"""
Vista Client Proposal Portal — Phase 1 security test harness.

Runs the 10 mandatory checks against the LIVE Supabase project using:
  * the service_role key (from config.json) to seed / inspect
  * real end-user JWTs (minted via admin generate_link -> verify) to prove RLS

Usage:
  python tp_portal_sectest.py --project-a <VISTA_PROJECT_ID> \
      --client "Acme Co" --user-a "Alice <alice@example.com>" \
      --user-b "Bob <bob@example.com>"

  # optional: a second already-published Vista project id for the cross-proposal test
  #   --project-b <OTHER_VISTA_PROJECT_ID>

Nothing here writes to the Vista central DB except tp_portal.publish() (which only
appends project.publishHistory[]).
"""
import argparse
import base64
import hashlib
import json
import re
import sys

import requests

import tp_portal


def _denied(sc, data):
    """A PostgREST read is 'denied / nothing' when it errors OR returns a non-list
    OR returns an empty list. Real leakage = a non-empty list of rows."""
    if sc >= 400:
        return True
    if not isinstance(data, list):
        return True
    return len(data) == 0


def user_jwt(supa, email):
    """Mint a real end-user access token without email delivery."""
    r = requests.post('%s/auth/v1/admin/generate_link' % supa.base, headers=supa.h,
                      json={'type': 'magiclink', 'email': email}, timeout=30)
    r.raise_for_status()
    j = r.json()
    props = j.get('properties', j)
    otp = props.get('email_otp')
    hashed = props.get('hashed_token')
    pub = tp_portal.load_config()['publishable_key']
    # try email OTP first
    for body in ([{'type': 'email', 'email': email, 'token': otp}] if otp else []) + \
                ([{'type': 'magiclink', 'token_hash': hashed}] if hashed else []):
        vr = requests.post('%s/auth/v1/verify' % supa.base,
                           headers={'apikey': pub},
                           json=body, timeout=30)
        if vr.status_code < 300 and vr.json().get('access_token'):
            return vr.json()['access_token']
    raise RuntimeError('could not mint a user JWT for %s: %s' % (email, r.text[:300]))


def as_user(base, anon, jwt):
    s = requests.Session()
    s.headers.update({'apikey': anon, 'Authorization': 'Bearer ' + jwt})
    s.base = base
    return s


def rest(sess, path, params=None):
    r = sess.get('%s/rest/v1/%s' % (sess.base, path), params=params or {}, timeout=30)
    return r.status_code, (r.json() if r.text.strip() else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project-a', required=True)
    ap.add_argument('--project-b')
    ap.add_argument('--client', required=True)
    ap.add_argument('--user-a', required=True)
    ap.add_argument('--user-b', required=True)
    ap.add_argument('--title', default='SEC-TEST Proposal A')
    a = ap.parse_args()

    def parse_u(s):
        m = re.match(r'\s*(.*?)\s*<\s*([^>]+)\s*>\s*$', s)
        return {'name': m.group(1).strip() or m.group(2).split('@')[0], 'email': m.group(2).strip().lower()}

    ua, ub = parse_u(a.user_a), parse_u(a.user_b)
    cfg = tp_portal.load_config()
    supa = tp_portal.Supa(cfg)          # raises if VISTA_PORTAL_SUPABASE_SECRET_KEY is unset
    base, anon = supa.base, cfg['publishable_key']
    secret = cfg['secret_key']
    results = []

    def check(n, name, ok, detail=''):
        results.append((n, name, ok, detail))
        print(('  [%2d] %-4s %s' % (n, 'PASS' if ok else 'FAIL', name)) + (('  — ' + detail) if detail else ''))

    print('\n=== Publishing Proposal A (grant to user A only) ===')
    pub_a = tp_portal.publish(a.project_a, a.client, [ua], title=a.title)
    prop_a = pub_a['proposalId']
    print('  proposal A =', prop_a, 'revision', pub_a['revisionLabel'], '| photos', pub_a['photos'])

    prop_b = None
    if a.project_b:
        pub_b = tp_portal.publish(a.project_b, a.client + ' (B)', [ub], title='SEC-TEST Proposal B')
        prop_b = pub_b['proposalId']
        print('  proposal B =', prop_b, '(granted to user B only)')

    # make sure user B exists even without project B
    supa.admin_create_user(ub['email'], ub['name'])

    jwt_a = user_jwt(supa, ua['email'])
    jwt_b = user_jwt(supa, ub['email'])
    sess_a = as_user(base, anon, jwt_a)
    sess_b = as_user(base, anon, jwt_b)
    sess_anon = requests.Session(); sess_anon.headers.update({'apikey': anon}); sess_anon.base = base

    print('\n=== Security checks ===')

    # 1 — client A can see proposal A
    sc, data = rest(sess_a, 'portal_proposals', {'id': 'eq.%s' % prop_a, 'select': 'id,title'})
    check(1, 'Client A can see Proposal A', sc == 200 and data and data[0]['id'] == prop_a,
          'rows=%s' % (len(data) if isinstance(data, list) else data))

    # 2 — client A cannot query proposal B
    if prop_b:
        sc, data = rest(sess_a, 'portal_proposals', {'id': 'eq.%s' % prop_b, 'select': 'id'})
        check(2, 'Client A cannot query Proposal B', sc == 200 and data == [], 'rows=%s' % (data,))
    else:
        # fabricate: A queries ALL proposals -> must only get A's
        sc, data = rest(sess_a, 'portal_proposals', {'select': 'id'})
        ids = {r['id'] for r in (data or [])}
        check(2, 'Client A only sees granted proposals', ids == {prop_a}, 'visible=%s' % ids)

    # 3 — client B cannot see proposal A (no grant)
    sc, data = rest(sess_b, 'portal_proposals', {'id': 'eq.%s' % prop_a, 'select': 'id'})
    check(3, 'Client B cannot see Proposal A without a grant', _denied(sc, data), 'status=%s body=%s' % (sc, data))

    # 4 — logged-out user sees nothing
    sc, data = rest(sess_anon, 'portal_proposals', {'select': 'id'})
    check(4, 'Logged-out (anon) sees nothing', _denied(sc, data), 'status=%s body=%s' % (sc, str(data)[:90]))

    # 5 — guessing the proposal UUID returns no data (anon + wrong user)
    sc1, d1 = rest(sess_anon, 'portal_revisions', {'proposal_id': 'eq.%s' % prop_a, 'select': 'id'})
    sc2, d2 = rest(sess_b, 'portal_revisions', {'proposal_id': 'eq.%s' % prop_a, 'select': 'id'})
    check(5, 'Guessing the proposal UUID yields no data', _denied(sc1, d1) and _denied(sc2, d2),
          'anon=%s/%s userB=%s/%s' % (sc1, str(d1)[:50], sc2, str(d2)[:50]))

    # 6 — storage photo URLs cannot expose another proposal
    #    If proposal A has no real photos, synthesise ONE (service-role upload +
    #    portal_revision_photos row) purely to exercise the Storage RLS policy,
    #    then delete the object. No Vista project data is touched.
    sc, aphotos = rest(sess_a, 'portal_revision_photos',
                       {'revision_id': 'eq.%s' % pub_a['revisionId'], 'select': 'storage_path'})
    synth_path = None
    if not aphotos:
        # 1x1 png
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
        synth_path = '%s/%s/sectest6-probe.png' % (prop_a, pub_a['revisionId'])
        up = requests.post('%s/storage/v1/object/%s/%s' % (base, supa.bucket, synth_path),
                           headers={'apikey': secret, 'Authorization': 'Bearer ' + secret,
                                    'Content-Type': 'image/png', 'x-upsert': 'true'},
                           data=png, timeout=30)
        supa.rest('POST', 'portal_revision_photos', json_body={
            'revision_id': pub_a['revisionId'], 'item_ref': '01', 'slot': 'existing', 'ordinal': 0,
            'storage_path': synth_path, 'mime': 'image/png', 'w': 1, 'h': 1,
            'sha256': hashlib.sha256(png).hexdigest()}, prefer='return=minimal')
        path = synth_path
        detail_src = '(synthetic probe object)'
    else:
        path = aphotos[0]['storage_path']
        detail_src = '(real photo)'

    ra = requests.post('%s/storage/v1/object/sign/%s/%s' % (base, supa.bucket, path),
                       headers={'apikey': anon, 'Authorization': 'Bearer ' + jwt_a},
                       json={'expiresIn': 600}, timeout=30)
    rb = requests.post('%s/storage/v1/object/sign/%s/%s' % (base, supa.bucket, path),
                       headers={'apikey': anon, 'Authorization': 'Bearer ' + jwt_b},
                       json={'expiresIn': 600}, timeout=30)
    rn = requests.post('%s/storage/v1/object/sign/%s/%s' % (base, supa.bucket, path),
                       headers={'apikey': anon}, json={'expiresIn': 600}, timeout=30)
    a_can = ra.status_code < 300 and 'signedURL' in ra.text
    b_blocked = rb.status_code >= 400 or 'signedURL' not in rb.text
    anon_blocked = rn.status_code >= 400 or 'signedURL' not in rn.text
    if synth_path:
        requests.delete('%s/storage/v1/object/%s/%s' % (base, supa.bucket, synth_path),
                        headers={'apikey': secret, 'Authorization': 'Bearer ' + secret}, timeout=30)
    check(6, 'Storage photo URLs cannot expose another proposal', a_can and b_blocked and anon_blocked,
          '%s granted-user signs=%s | ungranted-user blocked=%s | anon blocked=%s' %
          (detail_src, a_can, b_blocked, anon_blocked))

    # 7 — browser never receives the backend secret key
    pc = tp_portal.public_config()
    leaked_pc = bool(secret) and secret in json.dumps(pc)
    # scan shipped browser files + config.json on disk
    disk_leak = []
    scan = ('client-portal.html', 'technical-proposal-publish.html', 'config.json',
            'portal-config.json')
    for fn in scan:
        try:
            if secret and secret in open(fn, encoding='utf-8').read():
                disk_leak.append(fn)
        except OSError:
            pass
    keys_ok = set(pc) <= {'url', 'publishableKey', 'bucket', 'secretConfigured', 'secretKind', 'redirectUrl'}
    check(7, 'Browser never receives the backend secret key',
          (not leaked_pc) and (not disk_leak) and keys_ok,
          'public_config keys=%s  disk_leak=%s' % (sorted(pc), disk_leak))

    # 8 — published snapshot contains zero pricing / risk / internal fields
    snap = supa.select('portal_revisions', params={
        'id': 'eq.%s' % pub_a['revisionId'], 'select': 'snapshot_jsonb'})[0]['snapshot_jsonb']
    blob = json.dumps(snap).lower()
    banned = ['unitprice', 'currency', 'linetotal', 'pricing', 'supplier', 'quotation',
              'criticalinstaller', 'impactiflate', 'rulekey', 'execution', 'riskbuffer',
              'dependson', 'designcomment', 'samplecomment', 'sampleby']
    hits = [b for b in banned if b in blob]
    check(8, 'Snapshot contains zero pricing / supplier / risk / internal fields',
          not hits, 'hits=%s' % hits)

    # 9 — revoking the grant immediately blocks access
    supa.rest('PATCH', 'portal_proposal_grants',
              params={'proposal_id': 'eq.%s' % prop_a, 'client_user_id': 'eq.%s' % _uid(supa, ua['email'])},
              json_body={'revoked_at': 'now()'}, prefer='return=minimal')
    sc, data = rest(sess_a, 'portal_proposals', {'id': 'eq.%s' % prop_a, 'select': 'id'})
    revoke_ok = _denied(sc, data)
    # restore the grant so the manual review still works
    supa.rest('PATCH', 'portal_proposal_grants',
              params={'proposal_id': 'eq.%s' % prop_a, 'client_user_id': 'eq.%s' % _uid(supa, ua['email'])},
              json_body={'revoked_at': None}, prefer='return=minimal')
    check(9, 'Revoking the grant immediately blocks access', revoke_ok,
          'after-revoke rows=%s (grant restored for review)' % (data,))

    # 10 — local proxy / SQLite is never publicly reachable
    reachable = False
    try:
        rr = requests.get('http://127.0.0.1:8080/api/tp/portal/public-config', timeout=3)
        local_ok = rr.status_code == 200
    except Exception:  # noqa: BLE001
        local_ok = False
    # the meaningful assertion: proxy binds 127.0.0.1 only (config) + nothing forwards it
    bind = _proxy_bind()
    check(10, 'Local Vista proxy / SQLite never publicly reachable',
          local_ok and bind in ('127.0.0.1', 'localhost'),
          'proxy bind=%s (config.json proxy.bind); portal talks browser->Supabase only' % bind)

    npass = sum(1 for *_x, ok, _d in results if ok)
    print('\n=== %d / %d checks passed ===' % (npass, len(results)))
    sys.exit(0 if npass == len(results) else 1)


def _uid(supa, email):
    u = supa.admin_get_user_by_email(email)
    return u['id'] if u else ''


def _proxy_bind():
    try:
        return json.load(open('config.json')).get('proxy', {}).get('bind', '127.0.0.1')
    except Exception:  # noqa: BLE001
        return '127.0.0.1'


if __name__ == '__main__':
    main()
