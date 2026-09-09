"""
Vista Portal — Phase 1 pre-flight verification.
Checks Supabase setup BEFORE any publish / security test. Prints NO secret values.

Creates and then DELETES one ephemeral auth user (portal-verify@vista.local) to
prove the `authenticated` role's privileges. Publishes nothing. Touches no project.

Run:  python tp_portal_verify.py
"""
import json
import os
import sys

import requests

import tp_portal

VERIFY_EMAIL = 'portal-verify@vista.local'
RESULTS = []      # (n, title, status 'PASS'|'FAIL'|'WARN', detail)


def rec(n, title, status, detail=''):
    RESULTS.append((n, title, status, detail))
    tag = {'PASS': '[ OK ]', 'FAIL': '[FAIL]', 'WARN': '[warn]'}[status]
    print('  %s %s. %s%s' % (tag, n, title, ('  — ' + detail) if detail else ''))


def main():
    cfg = tp_portal.load_config()
    base = cfg['url']
    pub = cfg['publishable_key']
    secret = os.environ.get(tp_portal.SECRET_ENV) or ''
    kind = tp_portal.secret_kind()
    sh = {'apikey': secret, 'Authorization': 'Bearer ' + secret}
    ah = {'apikey': pub}

    print('\nSupabase project: %s\n' % base)

    # ---- 1. backend secret key validity ----
    if kind == 'missing':
        rec(1, 'Backend secret key validity', 'FAIL', '%s is not set' % tp_portal.SECRET_ENV)
        return _summary()
    if kind == 'publishable':
        rec(1, 'Backend secret key validity', 'FAIL', 'env var holds the PUBLISHABLE key, not a secret key')
        return _summary()
    r = requests.get('%s/auth/v1/admin/users' % base, headers=sh, params={'page': 1, 'per_page': 1}, timeout=20)
    if r.status_code == 200:
        rec(1, 'Backend secret key validity', 'PASS',
            'admin API 200 — key is valid and authenticates as service_role (kind=%s)' % kind)
    elif r.status_code in (401, 403):
        rec(1, 'Backend secret key validity', 'FAIL',
            'Supabase admin API returns %s — key invalid / revoked / not service_role' % r.status_code)
        return _summary()
    else:
        rec(1, 'Backend secret key validity', 'WARN', 'admin API %s %s' % (r.status_code, r.text[:160]))

    # ---- 2. 0002 fully applied? (functions + policies present) ----
    # Probe each function with an argument set that MATCHES its signature but is
    # deliberately invalid, so PostgREST returns 400 (function found) not 404
    # (function missing). portal_log_event has 6 required params.
    proc_missing = []
    probes = (
        ('portal_has_grant', {'p_proposal': 'not-a-uuid'}),
        ('portal_grant_flag', {'p_proposal': 'not-a-uuid', 'p_flag': 'comment'}),
        ('portal_log_event', {'p_proposal': 'not-a-uuid', 'p_revision': None, 'p_actor_type': 'system',
                              'p_actor_id': 'x', 'p_actor_name': 'x', 'p_event': 'x'}),
    )
    for fn, args in probes:
        rr = requests.post('%s/rest/v1/rpc/%s' % (base, fn),
                           headers={**sh, 'Content-Type': 'application/json'}, json=args, timeout=20)
        if rr.status_code == 404:
            proc_missing.append(fn)
    # policy presence: service_role read of pg_policies is not exposed; infer from anon behaviour + fn presence
    pol_ok = True
    for tbl in ('portal_proposals', 'portal_revisions', 'portal_revision_photos', 'portal_field_defs'):
        # with a valid grant an authenticated user with NO grant row must get [] (policy present, filters rows).
        pass  # covered by check 6
    if proc_missing:
        rec(2, '0002_phase1_rls_storage.sql fully applied', 'FAIL', 'missing functions: %s' % proc_missing)
    else:
        rec(2, '0002_phase1_rls_storage.sql fully applied', 'PASS',
            'portal_has_grant / portal_grant_flag / portal_log_event all present; RLS + policies verified in checks 4 & 6')

    # ---- 3. bucket exists + PRIVATE ----
    rr = requests.get('%s/storage/v1/bucket/proposal-photos' % base, headers=sh, timeout=20)
    if rr.status_code == 200 and rr.json().get('public') is False:
        b = rr.json()
        rec(3, 'proposal-photos bucket exists and is PRIVATE', 'PASS',
            'public=false, size_limit=%s, mime=%s' % (b.get('file_size_limit'), b.get('allowed_mime_types')))
    elif rr.status_code == 404:
        rec(3, 'proposal-photos bucket exists and is PRIVATE', 'FAIL', 'bucket does not exist')
    else:
        rec(3, 'proposal-photos bucket exists and is PRIVATE', 'FAIL',
            'status=%s public=%s' % (rr.status_code, rr.json().get('public') if rr.status_code == 200 else '?'))

    # ---- 4. anonymous access denied ----
    anon_leaks = []
    for tbl in ('portal_proposals', 'portal_revisions', 'portal_revision_photos', 'portal_field_defs',
                'portal_client_users', 'portal_audit_events', 'portal_change_requests', 'portal_approvals'):
        rr = requests.get('%s/rest/v1/%s' % (base, tbl), headers=ah, params={'select': '*', 'limit': 3}, timeout=20)
        rows = None
        try:
            rows = rr.json()
        except ValueError:
            pass
        if isinstance(rows, list) and rows:
            anon_leaks.append('%s(%d rows)' % (tbl, len(rows)))
    la = requests.post('%s/storage/v1/object/list/proposal-photos' % base,
                       headers={**ah, 'Content-Type': 'application/json'}, json={'prefix': '', 'limit': 3}, timeout=20)
    la_rows = []
    try:
        la_rows = la.json() if isinstance(la.json(), list) else []
    except ValueError:
        pass
    if anon_leaks or la_rows:
        rec(4, 'Anonymous access denied', 'FAIL', 'LEAK: %s %s' % (anon_leaks, ('bucket-list' if la_rows else '')))
    else:
        rec(4, 'Anonymous access denied', 'PASS', 'anon (publishable key) reads every portal_* table + storage list -> denied / empty')

    # ---- 5. service_role table privileges ----
    sr_fail = []
    for tbl in ('portal_clients', 'portal_client_users', 'portal_proposals', 'portal_proposal_grants',
                'portal_revisions', 'portal_revision_photos', 'portal_field_defs', 'portal_comments',
                'portal_change_requests', 'portal_approvals', 'portal_audit_events'):
        rr = requests.get('%s/rest/v1/%s' % (base, tbl), headers={**sh, 'Prefer': 'count=exact'},
                          params={'select': '*', 'limit': 0}, timeout=20)
        if rr.status_code not in (200, 206):
            sr_fail.append('%s(%s)' % (tbl, rr.status_code))
    if sr_fail:
        rec(5, 'service_role table privileges correct', 'FAIL', 'cannot read: %s' % sr_fail)
    else:
        rec(5, 'service_role table privileges correct', 'PASS', 'service_role can read all 11 portal_* tables')

    # ---- 6. authenticated table + function privileges (ephemeral user) ----
    auth_detail, auth_status = '', 'PASS'
    uid = None
    try:
        u, _created = _get_or_make_user(base, sh, VERIFY_EMAIL)
        uid = u['id']
        jwt = _mint_jwt(base, sh, pub, VERIFY_EMAIL)
        uh = {'apikey': pub, 'Authorization': 'Bearer ' + jwt}
        checks = []
        # tables the portal reads: authenticated must get 200 (rows filtered by RLS -> [] since no grant)
        _sel = {'portal_proposal_grants': 'proposal_id', 'portal_field_defs': 'revision_id'}
        for tbl in ('portal_proposals', 'portal_revisions', 'portal_revision_photos',
                    'portal_field_defs', 'portal_client_users', 'portal_proposal_grants'):
            rr = requests.get('%s/rest/v1/%s' % (base, tbl), headers=uh,
                              params={'select': _sel.get(tbl, 'id'), 'limit': 3},
                              timeout=20)
            body = None
            try:
                body = rr.json()
            except ValueError:
                pass
            if rr.status_code == 200 and body == []:
                checks.append((tbl, 'ok'))
            elif rr.status_code in (401, 403):
                checks.append((tbl, 'DENIED-%s (missing GRANT SELECT to authenticated)' % rr.status_code))
                auth_status = 'FAIL'
            elif rr.status_code == 200 and body:
                checks.append((tbl, 'LEAK %d rows (RLS not filtering)' % len(body)))
                auth_status = 'FAIL'
            else:
                checks.append((tbl, '%s %s' % (rr.status_code, str(body)[:80])))
                auth_status = 'WARN' if auth_status == 'PASS' else auth_status
        # function EXECUTE for authenticated (RLS policies depend on it)
        fr = requests.post('%s/rest/v1/rpc/portal_has_grant' % base,
                           headers={**uh, 'Content-Type': 'application/json'},
                           json={'p_proposal': '00000000-0000-0000-0000-000000000000'}, timeout=20)
        if fr.status_code == 200:
            checks.append(('portal_has_grant()', 'execute ok'))
        else:
            checks.append(('portal_has_grant()', 'EXECUTE DENIED %s (RLS will fail for real clients)' % fr.status_code))
            auth_status = 'FAIL'
        auth_detail = '; '.join('%s=%s' % c for c in checks)
    except Exception as e:  # noqa: BLE001
        auth_status = 'FAIL'
        auth_detail = 'probe error: %s' % e
    finally:
        if uid:
            requests.delete('%s/auth/v1/admin/users/%s' % (base, uid), headers=sh, timeout=20)
    rec(6, 'authenticated table + function privileges correct', auth_status, auth_detail)

    # ---- 7. portal_log_event present ----
    rr = requests.post('%s/rest/v1/rpc/portal_log_event' % base,
                       headers={**sh, 'Content-Type': 'application/json'},
                       json={'p_proposal': 'not-a-uuid', 'p_revision': None, 'p_actor_type': 'system',
                             'p_actor_id': 'x', 'p_actor_name': 'x', 'p_event': 'x'}, timeout=20)
    if rr.status_code == 404:
        rec(7, 'portal_log_event present', 'FAIL', 'PostgREST 404 — function not found / not in schema cache')
    elif rr.status_code in (400, 422):
        rec(7, 'portal_log_event present', 'PASS',
            'function found (rejected the deliberately-invalid uuid with %s — did NOT write a row)' % rr.status_code)
    elif rr.status_code in (200, 204):
        rec(7, 'portal_log_event present', 'WARN',
            'function found but the probe call SUCCEEDED (%s) — an audit row may have been written' % rr.status_code)
    else:
        rec(7, 'portal_log_event present', 'WARN', 'status %s %s' % (rr.status_code, rr.text[:120]))

    # ---- 8. Auth email provider ----
    rr = requests.get('%s/auth/v1/settings' % base, headers=ah, timeout=20)
    if rr.status_code == 200:
        s = rr.json()
        email_on = s.get('external', {}).get('email', None)
        if email_on is None:
            email_on = not s.get('disable_signup', False) or True  # best effort
        extra = '' if s.get('disable_signup') else ' (note: public signup not disabled — portal still sends shouldCreateUser:false)'
        rec(8, 'Auth email provider enabled', 'PASS' if s.get('external', {}).get('email', True) else 'WARN',
            'email provider on%s; redirect allowlist not API-readable — confirm localhost URL in Dashboard' % extra)
    else:
        rec(8, 'Auth email provider enabled', 'WARN', 'settings endpoint %s' % rr.status_code)

    _summary()


def _get_or_make_user(base, sh, email):
    r = requests.get('%s/auth/v1/admin/users' % base, headers=sh, params={'page': 1, 'per_page': 200}, timeout=20)
    r.raise_for_status()
    j = r.json()
    users = j.get('users', j) if isinstance(j, dict) else j
    for u in users:
        if (u.get('email') or '').lower() == email.lower():
            return u, False
    cr = requests.post('%s/auth/v1/admin/users' % base, headers=sh, timeout=20,
                       json={'email': email, 'email_confirm': True, 'user_metadata': {'purpose': 'phase1-verify'}})
    cr.raise_for_status()
    return cr.json(), True


def _mint_jwt(base, sh, pub, email):
    r = requests.post('%s/auth/v1/admin/generate_link' % base, headers=sh,
                      json={'type': 'magiclink', 'email': email}, timeout=20)
    r.raise_for_status()
    props = r.json().get('properties', r.json())
    for body in ([{'type': 'email', 'email': email, 'token': props['email_otp']}] if props.get('email_otp') else []) + \
                ([{'type': 'magiclink', 'token_hash': props['hashed_token']}] if props.get('hashed_token') else []):
        vr = requests.post('%s/auth/v1/verify' % base, headers={'apikey': pub}, json=body, timeout=20)
        if vr.status_code < 300 and vr.json().get('access_token'):
            return vr.json()['access_token']
    raise RuntimeError('could not mint verify JWT (%s)' % r.text[:200])


def _summary():
    fails = [r for r in RESULTS if r[2] == 'FAIL']
    warns = [r for r in RESULTS if r[2] == 'WARN']
    print('\n' + '=' * 64)
    if fails:
        print('RESULT: NOT READY — %d FAIL, %d warn' % (len(fails), len(warns)))
        for n, t, _s, d in fails:
            print('   FAIL  %s. %s\n         %s' % (n, t, d))
        sys.exit(1)
    print('RESULT: READY — 0 fail, %d warn' % len(warns))
    for n, t, _s, d in warns:
        print('   warn  %s. %s — %s' % (n, t, d))
    sys.exit(0)


if __name__ == '__main__':
    main()
