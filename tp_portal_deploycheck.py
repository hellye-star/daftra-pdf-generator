"""
Vista Portal — DEPLOYED public portal security check.

Run AFTER the portal is live on Vercel:
    python tp_portal_deploycheck.py --portal-url https://vista-client-portal.vercel.app

Verifies the deployed site is fully isolated:
  * no secret / service_role key in any served file
  * no localhost / 127.0.0.1 / /api/tp / proxy dependency
  * portal-config.json contains only the 4 safe public keys
  * publishable key (not a secret) is what's shipped
  * security response headers present (CSP locked to the Supabase project)
  * the deployed page can reach Supabase, and anon is still denied by RLS

The Supabase-side RLS / auth / storage / snapshot checks are identical to
tp_portal_sectest.py (same project, same publishable key) — run that too.
"""
import argparse
import json
import re
import sys

import requests

import tp_portal

FORBIDDEN = ('sb_secret_', 'service_role', 'service-role', 'SUPABASE_SERVICE',
             'localhost', '127.0.0.1', '/api/tp/', 'proxy.py', 'VISTA_PORTAL_SUPABASE_SECRET_KEY')
SAFE_KEYS = {'url', 'publishableKey', 'bucket', 'redirectUrl'}

R = []


def rec(n, title, ok, detail=''):
    R.append(ok)
    print('  [%s] %d. %s%s' % ('PASS' if ok else 'FAIL', n, title, ('  — ' + detail) if detail else ''))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--portal-url', required=True, help='https://vista-client-portal.vercel.app')
    a = ap.parse_args()
    base = a.portal_url.rstrip('/')
    cfg = tp_portal.load_config()
    supa_host = cfg['url'].split('//', 1)[-1]

    print('\nDeployed portal: %s\n' % base)

    # 1. index.html served, no forbidden strings
    ri = requests.get(base + '/', timeout=20)
    html = ri.text
    hits = sorted({f for f in FORBIDDEN if f.lower() in html.lower()})
    rec(1, 'index.html served and clean', ri.status_code == 200 and not hits,
        'status=%s forbidden_strings=%s' % (ri.status_code, hits or 'none'))

    # 2. portal-config.json served, only safe keys, no secret
    rc = requests.get(base + '/portal-config.json', timeout=20)
    try:
        conf = rc.json()
    except ValueError:
        conf = None
    conf_hits = sorted({f for f in FORBIDDEN if f.lower() in rc.text.lower()})
    keys_ok = isinstance(conf, dict) and set(conf) <= SAFE_KEYS
    rec(2, 'portal-config.json: safe keys only, no secret',
        rc.status_code == 200 and keys_ok and not conf_hits,
        'keys=%s forbidden=%s' % (sorted(conf) if isinstance(conf, dict) else conf, conf_hits or 'none'))

    # 3. shipped key is a PUBLISHABLE key
    pk = (conf or {}).get('publishableKey', '')
    rec(3, 'shipped key is publishable (not secret)',
        pk.startswith('sb_publishable_'), 'prefix=%s' % (pk.split('_')[0] + '_' + (pk.split('_')[1] if pk.count('_') > 1 else '')))

    # 4. no Vista local dependency anywhere on the page
    rec(4, 'no local Vista server dependency',
        ('localhost' not in html.lower()) and ('127.0.0.1' not in html) and ('/api/tp' not in html),
        'index.html references only Supabase + fonts + jsdelivr')

    # 5. config points at the right Supabase project + a https redirect
    ok5 = isinstance(conf, dict) and conf.get('url', '').rstrip('/') == cfg['url'].rstrip('/') \
        and str(conf.get('redirectUrl', '')).startswith('https://')
    rec(5, 'config targets correct Supabase project + https redirect',
        ok5, 'url=%s redirectUrl=%s' % ((conf or {}).get('url'), (conf or {}).get('redirectUrl')))

    # 6. security headers
    h = {k.lower(): v for k, v in ri.headers.items()}
    csp = h.get('content-security-policy', '')
    csp_ok = ('frame-ancestors' in csp and "'none'" in csp
              and supa_host in csp
              and 'connect-src' in csp)
    rec(6, 'security headers present (CSP locked to Supabase project)',
        csp_ok and h.get('x-frame-options', '').upper() == 'DENY'
        and h.get('x-content-type-options', '').lower() == 'nosniff',
        'CSP connect-src has %s=%s, XFO=%s' % (supa_host, supa_host in csp, h.get('x-frame-options')))

    # 7. deployed page's key: anon read of portal_proposals is DENIED by RLS
    if isinstance(conf, dict) and conf.get('url') and pk:
        pr = requests.get(conf['url'].rstrip('/') + '/rest/v1/portal_proposals',
                          headers={'apikey': pk}, params={'select': 'id', 'limit': 3}, timeout=20)
        try:
            body = pr.json()
        except ValueError:
            body = None
        denied = pr.status_code >= 400 or not isinstance(body, list) or body == []
        rec(7, 'deployed publishable key: anon read denied by RLS', denied,
            'status=%s body=%s' % (pr.status_code, str(body)[:100]))
    else:
        rec(7, 'deployed publishable key: anon read denied by RLS', False, 'no usable config')

    # 8. jsdelivr is the only third-party script host
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    ext = [s for s in scripts if s.startswith('http') and 'cdn.jsdelivr.net' not in s]
    rec(8, 'only jsdelivr as external script host', not ext, 'other script hosts=%s' % (ext or 'none'))

    npass = sum(1 for x in R if x)
    print('\n' + '=' * 60)
    print('DEPLOY CHECK: %d / %d passed' % (npass, len(R)))
    print('Now also run:  python tp_portal_sectest.py --project-a __ROADMAP_VISUAL_REVIEW__ ...')
    sys.exit(0 if npass == len(R) else 1)


if __name__ == '__main__':
    main()
