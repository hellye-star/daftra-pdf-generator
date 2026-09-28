"""
PO Generator — Phase 1 tests (drafts, validation, calculations, API guard).

Run from the repo root:
    python -m unittest tests.test_po_phase1 -v

Every test uses a TEMPORARY data directory (VISTA_PO_DATA_DIR is set before
any PO module is imported) — the production PO store under
~/.vista-platform/purchase-orders is never opened. No Daftra, Central DB or
Technical Proposal data is touched: the HTTP tests only call /api/po/*.
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

_TMP = tempfile.mkdtemp(prefix='vista-po-test-')
os.environ['VISTA_PO_DATA_DIR'] = _TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import po_db      # noqa: E402
import po_model   # noqa: E402
from tests import po_test_support   # noqa: E402

PROD_DIR = os.path.join(os.path.expanduser('~'), '.vista-platform', 'purchase-orders')


def draft(**over):
    d = {
        'id': 'po_TESTdraft0001', 'schema': 1, 'status': 'draft', 'title': 'Test', 'currency': 'SAR',
        'priceTaxBasis': 'exclusive', 'supplier': None, 'items': [],
        'paymentTerms': {'text': '50% advance payment; remaining 50% one calendar month after delivery.',
                         'isDraftDefault': True, 'balanceTrigger': 'undecided',
                         'milestones': [{'id': 'ms_advance', 'pct': '50', 'label': 'Advance'},
                                        {'id': 'ms_balance', 'pct': '50', 'label': 'Balance'}]},
        'projectRef': '', 'deliveryLocation': '', 'notes': '', 'createdAt': '', 'updatedAt': '',
    }
    d.update(over)
    return d


def item(iid, qty='1', price='10', treat='taxable', rate='15', included=True, desc='Item', unit='pcs'):
    return {'id': iid, 'description': desc, 'unit': unit, 'qty': qty, 'unitPrice': price,
            'included': included, 'tax': {'treatment': treat, 'rate': rate}, 'source': 'manual',
            'createdAt': '', 'excludedAt': ''}


def calc(doc):
    return po_model.compute(po_model.validate_draft(doc))


class T0Isolation(unittest.TestCase):
    def test_uses_temporary_directory_only(self):
        self.assertTrue(po_db.db_path().startswith(_TMP))
        self.assertFalse(po_db.db_path().startswith(PROD_DIR))


class T1Validation(unittest.TestCase):
    def errs(self, doc):
        with self.assertRaises(po_model.DraftInvalid) as cm:
            po_model.validate_draft(doc)
        return {e['path'] for e in cm.exception.errors}

    def test_valid_empty_draft(self):
        po_model.validate_draft(draft())

    def test_bad_numbers_rejected(self):
        for qty in ('-1', '1,000', '1e3', 'abc', '1.2345', '0', ' 1 2'):
            self.assertIn('items[0].qty', self.errs(draft(items=[item('it_aaaaaa1', qty=qty)])), qty)
        for price in ('-5', '10.12345', '1,5', 'NaN'):
            self.assertIn('items[0].unitPrice', self.errs(draft(items=[item('it_aaaaaa1', price=price)])), price)

    def test_arabic_indic_digits_normalized(self):
        d = po_model.validate_draft(draft(items=[item('it_aaaaaa1', qty='١٢٫٥', price='٣')]))
        self.assertEqual((d['items'][0]['qty'], d['items'][0]['unitPrice']), ('12.5', '3'))

    def test_zero_price_allowed_zero_qty_not(self):
        po_model.validate_draft(draft(items=[item('it_aaaaaa1', price='0')]))
        self.assertIn('items[0].qty', self.errs(draft(items=[item('it_aaaaaa1', qty='0.000')])))

    def test_blank_values_stay_blank(self):
        d = po_model.validate_draft(draft(items=[item('it_aaaaaa1', qty='', price='', treat='unresolved', rate='')]))
        self.assertEqual(d['items'][0]['qty'], '')
        self.assertEqual(d['items'][0]['tax'], {'treatment': 'unresolved', 'rate': '', 'origin': ''})

    def test_structural_rejections(self):
        self.assertIn('items[1].id', self.errs(draft(items=[item('it_aaaaaa1'), item('it_aaaaaa1')])))
        self.assertIn('status', self.errs(draft(status='issued')))
        self.assertIn('currency', self.errs(draft(currency='XXX')))
        self.assertIn('id', self.errs(draft(id='../../etc')))
        self.assertIn('poNumber', self.errs(draft(poNumber='PO-1')))
        self.assertIn('items[0].tax.rate', self.errs(draft(items=[item('it_aaaaaa1', rate='150')])))
        self.assertIn('supplier.daftraId', self.errs(draft(supplier={'daftraId': 'x1', 'name': 'A', 'snapshot': {}})))
        self.assertIn('supplier.snapshot.password',
                      self.errs(draft(supplier={'daftraId': '12', 'name': 'A', 'snapshot': {'password': 'x'}})))
        self.assertIn('title', self.errs(draft(title='a\x00b')))
        self.assertIn('title', self.errs(draft(title='x' * 201)))

    def test_script_text_stored_as_data(self):
        payload = '<img src=x onerror=alert(1)> وصف عربي\nسطر ثاني'
        d = po_model.validate_draft(draft(items=[item('it_aaaaaa1', desc=payload)]))
        self.assertEqual(d['items'][0]['description'], payload)


class T2Calculations(unittest.TestCase):
    def test_line_amount_rounding_and_category_vat(self):
        c = calc(draft(items=[item('it_aaaaaa1', qty='3', price='0.335'),      # 1.005 → 1.01
                              item('it_aaaaaa2', qty='1', price='0.10')]))
        self.assertEqual(c['lines'][0]['lineAmount'], '1.01')
        self.assertIsNone(c['lines'][1]['tax'], 'VAT is calculated per category, not per line (ZATCA BR-CO-17)')
        t = c['totals']
        self.assertEqual(t['status'], 'complete')
        self.assertEqual((t['net'], t['tax'], t['gross']), ('1.11', '0.17', '1.28'))   # 1.11 × 15 % = 0.1665 → 0.17
        # summing rounded line VAT would give 0.02 + 0.02 = 0.04; the category rule gives round(0.20 × 15 %) = 0.03
        c = calc(draft(items=[item('it_aaaaaa1', qty='1', price='0.10'), item('it_aaaaaa2', qty='1', price='0.10')]))
        self.assertEqual((c['totals']['net'], c['totals']['tax'], c['totals']['gross']), ('0.20', '0.03', '0.23'))

    def test_inclusive_prices(self):
        c = calc(draft(priceTaxBasis='inclusive', items=[item('it_aaaaaa1', qty='1', price='115')]))
        self.assertEqual((c['totals']['net'], c['totals']['tax'], c['totals']['gross']), ('100.00', '15.00', '115.00'))
        c = calc(draft(priceTaxBasis='inclusive', items=[item('it_aaaaaa1', qty='1', price='100')]))
        self.assertEqual((c['totals']['net'], c['totals']['tax'], c['totals']['gross']), ('86.96', '13.04', '100.00'))

    def test_mixed_zero_exempt_and_rates(self):
        c = calc(draft(items=[item('it_aaaaaa1', price='100'), item('it_aaaaaa2', price='100', treat='zero_rated', rate=''),
                              item('it_aaaaaa3', price='100', treat='exempt', rate=''), item('it_aaaaaa4', price='100', rate='5')]))
        t = c['totals']
        self.assertEqual((t['net'], t['tax'], t['gross']), ('400.00', '20.00', '420.00'))
        self.assertEqual(len(t['taxGroups']), 4)

    def test_excluded_items_not_counted(self):
        c = calc(draft(items=[item('it_aaaaaa1', price='100'), item('it_aaaaaa2', price='999', included=False)]))
        self.assertEqual(c['totals']['gross'], '115.00')
        self.assertEqual(c['totals']['excludedCount'], 1)

    def test_unresolved_states_never_guess(self):
        self.assertEqual(calc(draft(currency='', items=[item('it_aaaaaa1')]))['totals']['status'], 'currency_unresolved')
        self.assertEqual(calc(draft(items=[item('it_aaaaaa1', treat='unresolved', rate='')]))['totals']['status'], 'tax_unresolved')
        self.assertEqual(calc(draft(priceTaxBasis='unresolved', items=[item('it_aaaaaa1')]))['totals']['status'], 'tax_unresolved')
        # zero-rated lines do not depend on the inclusive/exclusive basis
        self.assertEqual(calc(draft(priceTaxBasis='unresolved', items=[item('it_aaaaaa1', treat='zero_rated', rate='')]))['totals']['status'], 'complete')
        self.assertEqual(calc(draft(items=[item('it_aaaaaa1', qty='')]))['totals']['status'], 'incomplete')
        self.assertIsNone(calc(draft(items=[item('it_aaaaaa1', treat='unresolved', rate='')]))['totals']['gross'])

    def test_currency_minor_units(self):
        self.assertEqual(calc(draft(currency='KWD', items=[item('it_aaaaaa1', qty='1', price='1.0005', treat='exempt', rate='')]))['totals']['gross'], '1.001')
        self.assertEqual(calc(draft(currency='JPY', items=[item('it_aaaaaa1', qty='1', price='10.5', treat='exempt', rate='')]))['totals']['gross'], '11')

    def test_milestones_sum_exactly(self):
        c = calc(draft(items=[item('it_aaaaaa1', qty='1', price='0.03', treat='exempt', rate='')],
                       paymentTerms={'text': 't', 'isDraftDefault': False, 'balanceTrigger': 'delivery',
                                     'milestones': [{'id': 'ms_aa', 'pct': '33.33', 'label': 'a'}, {'id': 'ms_bb', 'pct': '66.67', 'label': 'b'}]}))
        amounts = [m['amount'] for m in c['milestones']]
        self.assertEqual(amounts, ['0.01', '0.02'])
        bad = calc(draft(items=[item('it_aaaaaa1')], paymentTerms={'text': '', 'isDraftDefault': False, 'balanceTrigger': 'delivery',
                   'milestones': [{'id': 'ms_aa', 'pct': '50', 'label': ''}, {'id': 'ms_bb', 'pct': '40', 'label': ''}]}))
        self.assertFalse(bad['milestonePctValid'])
        self.assertIsNone(bad['milestones'][0]['amount'])

    def test_readiness_blocks_until_settings_are_confirmed(self):
        d = po_model.validate_draft(draft(supplier={'daftraId': '7', 'name': 'S', 'snapshot': {}},
                                          items=[item('it_aaaaaa1')],
                                          paymentTerms=dict(draft()['paymentTerms'], balanceTrigger='delivery_written_acceptance')))
        codes = {b['code'] for b in po_model.readiness(d, po_model.compute(d))}
        self.assertEqual(codes, {'buyer_unconfirmed', 'po_number_format_unconfirmed', 'approval_unconfirmed'})
        confirmed = dict(po_model.default_settings(), buyerConfirmed=True, numberingConfirmed=True, approvalConfirmed=True)
        self.assertEqual(po_model.readiness(d, po_model.compute(d), confirmed), [])
        codes = {b['code'] for b in po_model.readiness(po_model.validate_draft(draft()), calc(draft()))}
        self.assertTrue({'supplier_missing', 'no_included_items', 'payment_trigger_undecided'} <= codes)


class T3Storage(unittest.TestCase):
    def test_create_update_conflict(self):
        d = po_model.validate_draft(draft(id='po_STORAGEtest01'))
        doc, rev = po_db.create_draft(d)
        self.assertEqual(rev, 1)
        with self.assertRaises(po_db.DraftExists):
            po_db.create_draft(d)
        doc2, rev2 = po_db.update_draft(dict(doc, title='A'), 1)
        self.assertEqual(rev2, 2)
        with self.assertRaises(po_db.RevConflict) as cm:
            po_db.update_draft(dict(doc, title='STALE'), 1)
        self.assertEqual(cm.exception.current, 2)
        self.assertEqual(po_db.get_draft('po_STORAGEtest01')[0]['title'], 'A')   # not overwritten
        with self.assertRaises(po_db.DraftGone):
            po_db.update_draft(dict(doc, id='po_DOESNOTEXIST1'), 1)

    def test_concurrent_same_rev_only_one_wins(self):
        d = po_model.validate_draft(draft(id='po_RACEtest00001'))
        po_db.create_draft(d)
        results = []

        def attempt(n):
            try:
                po_db.update_draft(dict(d, title=f'writer {n}'), 1)
                results.append('ok')
            except po_db.RevConflict:
                results.append('conflict')
        ts = [threading.Thread(target=attempt, args=(n,)) for n in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results.count('ok'), 1)
        self.assertEqual(po_db.get_draft('po_RACEtest00001')[1], 2)


class T4Http(unittest.TestCase):
    """PO API handler on an ephemeral port → /api/po/* only (proxy.py's own routing
       is checked from its source in test_proxy_routes_po_api)."""

    def test_proxy_routes_po_api(self):
        self.assertEqual(po_test_support.proxy_po_routes(),
                         {'GET': True, 'POST': True, 'PUT': True, 'DELETE_blocked': True, 'PATCH_blocked': True})

    @classmethod
    def setUpClass(cls):
        # PO API over HTTP without importing proxy.py (no config.json / tokens needed)
        cls.srv, cls.port = po_test_support.start_server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        h = {'Host': f'127.0.0.1:{self.port}'}
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        hdrs = dict(r.getheaders())
        c.close()
        try:
            return r.status, json.loads(raw), hdrs
        except ValueError:
            return r.status, raw, hdrs

    def test_full_draft_lifecycle(self):
        d = draft(id='po_HTTPlifecycle1')
        s, j, h = self.req('POST', '/api/po/drafts', d)
        self.assertEqual(s, 201, j)
        self.assertEqual(j['data']['rev'], 1)
        self.assertNotIn('Access-Control-Allow-Origin', h)
        self.assertEqual(self.req('POST', '/api/po/drafts', d)[0], 409)
        d2 = dict(j['data']['data'], items=[item('it_httpitem1', qty='2', price='50')])
        s, j, _ = self.req('PUT', '/api/po/drafts/po_HTTPlifecycle1', d2, {'If-Match': '1'})
        self.assertEqual(s, 200, j)
        self.assertEqual(j['data']['rev'], 2)
        self.assertEqual(j['data']['computed']['totals']['gross'], '115.00')
        s, j, _ = self.req('PUT', '/api/po/drafts/po_HTTPlifecycle1', dict(d2, title='stale'), {'If-Match': '1'})
        self.assertEqual((s, j['code'], j['currentRev']), (409, 'rev_conflict', 2))
        self.assertEqual(self.req('PUT', '/api/po/drafts/po_HTTPlifecycle1', d2)[0], 428)
        s, j, _ = self.req('GET', '/api/po/drafts/po_HTTPlifecycle1')
        self.assertEqual((s, j['data']['rev'], j['data']['data']['title']), (200, 2, 'Test'))
        s, j, _ = self.req('GET', '/api/po/drafts')
        self.assertIn('po_HTTPlifecycle1', [x['id'] for x in j['data']])

    def test_server_side_validation(self):
        self.req('POST', '/api/po/drafts', draft(id='po_HTTPvalidate1'))
        bad = draft(id='po_HTTPvalidate1', items=[item('it_httpitem2', qty='-3', price='1.23456')])
        s, j, _ = self.req('PUT', '/api/po/drafts/po_HTTPvalidate1', bad, {'If-Match': '1'})
        self.assertEqual((s, j['code']), (400, 'invalid'))
        self.assertEqual({e['path'] for e in j['errors']}, {'items[0].qty', 'items[0].unitPrice'})
        self.assertEqual(self.req('GET', '/api/po/drafts/po_HTTPvalidate1')[1]['data']['rev'], 1)   # nothing saved
        s, j, _ = self.req('PUT', '/api/po/drafts/po_HTTPvalidate1', draft(id='po_HTTPother0001'), {'If-Match': '1'})
        self.assertEqual(s, 400)
        self.assertEqual(self.req('POST', '/api/po/drafts', b'{not json')[0], 400)

    def test_access_guard(self):
        self.assertEqual(self.req('GET', '/api/po/status')[0], 200)
        self.assertEqual(self.req('GET', '/api/po/status', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.req('GET', '/api/po/status', headers={'X-Forwarded-For': '1.2.3.4'})[0], 403)
        self.assertEqual(self.req('GET', '/api/po/status', headers={'CF-Connecting-IP': '1.2.3.4'})[0], 403)
        self.assertEqual(self.req('POST', '/api/po/drafts', draft(id='po_HTTPguard0001'),
                                  {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.req('POST', '/api/po/drafts', draft(id='po_HTTPguard0002'),
                                  {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.req('POST', '/api/po/drafts', json.dumps(draft(id='po_HTTPguard0003')).encode(),
                                  {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.req('POST', '/api/po/drafts', draft(id='po_HTTPguard0004'),
                                  {'Origin': f'http://localhost:{self.port}'})[0], 201)

    def test_no_path_or_method_exposure(self):
        self.assertEqual(self.req('GET', '/api/po/drafts/..%2f..%2fconfig.json')[0], 404)
        self.assertEqual(self.req('GET', '/api/po/files/anything')[0], 404)
        self.assertEqual(self.req('DELETE', '/api/po/drafts/po_HTTPlifecycle1')[0], 405)
        s, j, _ = self.req('GET', '/api/po/status')
        self.assertNotIn(_TMP.replace('\\', '/'), json.dumps(j).replace('\\\\', '/'))   # no filesystem paths


class T5DefaultVat(unittest.TestCase):
    """Phase 1 adjustment: new items default to standard VAT 15% (editable)."""

    def test_default_constant(self):
        self.assertEqual(po_model.DEFAULT_ITEM_TAX, {'treatment': 'taxable', 'rate': '15', 'origin': 'default'})

    def test_default_item_computes_exclusive_and_inclusive_without_double_vat(self):
        it = dict(item('it_dflt00001', qty='2', price='100'), tax=dict(po_model.DEFAULT_ITEM_TAX))
        ex = calc(draft(priceTaxBasis='exclusive', items=[it]))['totals']
        self.assertEqual((ex['net'], ex['tax'], ex['gross']), ('200.00', '30.00', '230.00'))
        inc = calc(draft(priceTaxBasis='inclusive', items=[it]))['totals']
        self.assertEqual((inc['net'], inc['tax'], inc['gross']), ('173.91', '26.09', '200.00'))
        # inclusive/exclusive stays a separate, explicit decision
        self.assertEqual(calc(draft(priceTaxBasis='unresolved', items=[it]))['totals']['status'], 'tax_unresolved')

    def test_origins_validated_and_preserved(self):
        for origin in ('default', 'user', '', 'extracted'):   # 'extracted' is valid since Phase 2 (stated by the quotation)
            d = po_model.validate_draft(draft(items=[dict(item('it_orig00001'), tax={'treatment': 'exempt', 'rate': '', 'origin': origin})]))
            self.assertEqual(d['items'][0]['tax']['origin'], origin)
        for bad in ('guess', 'auto'):
            with self.assertRaises(po_model.DraftInvalid):
                po_model.validate_draft(draft(items=[dict(item('it_orig00001'), tax={'treatment': 'taxable', 'rate': '15', 'origin': bad})]))
        with self.assertRaises(po_model.DraftInvalid):
            po_model.validate_draft(draft(items=[dict(item('it_orig00001'), tax={'treatment': 'taxable', 'rate': '15', 'extra': 1})]))

    def test_legacy_unresolved_item_is_not_overwritten(self):
        legacy = item('it_legacy0001', treat='unresolved', rate='')
        legacy['tax'] = {'treatment': 'unresolved', 'rate': ''}      # saved before origins existed
        d = po_model.validate_draft(draft(items=[legacy]))
        self.assertEqual(d['items'][0]['tax'], {'treatment': 'unresolved', 'rate': '', 'origin': ''})

    def test_http_status_exposes_default_and_round_trip_preserves_tax(self):
        T4Http.setUpClass()
        try:
            h = T4Http('test_access_guard')
            s, j, _ = h.req('GET', '/api/po/status')
            self.assertEqual(j['data']['defaults']['newItemTax'], po_model.DEFAULT_ITEM_TAX)
            items = [dict(item('it_keep00001', included=False), tax={'treatment': 'taxable', 'rate': '5', 'origin': 'user'}),
                     dict(item('it_keep00002'), tax={'treatment': 'zero_rated', 'rate': '', 'origin': 'user'}),
                     dict(item('it_keep00003'), tax={'treatment': 'unresolved', 'rate': '', 'origin': ''}),
                     dict(item('it_keep00004'), tax=dict(po_model.DEFAULT_ITEM_TAX))]
            s, j, _ = h.req('POST', '/api/po/drafts', draft(id='po_HTTPtaxkeep01', items=items))
            self.assertEqual(s, 201, j)
            # restore the excluded item and save again: tax settings must survive both steps
            doc = j['data']['data']
            doc['items'][0]['included'] = True
            s, j, _ = h.req('PUT', '/api/po/drafts/po_HTTPtaxkeep01', doc, {'If-Match': '1'})
            self.assertEqual(s, 200, j)
            got = h.req('GET', '/api/po/drafts/po_HTTPtaxkeep01')[1]['data']['data']['items']
            self.assertEqual([i['tax'] for i in got], [
                {'treatment': 'taxable', 'rate': '5', 'origin': 'user'},
                {'treatment': 'zero_rated', 'rate': '', 'origin': 'user'},
                {'treatment': 'unresolved', 'rate': '', 'origin': ''},
                {'treatment': 'taxable', 'rate': '15', 'origin': 'default'}])
        finally:
            T4Http.tearDownClass()


_CHILD = r'''
import json, os, sys
tmp_home = os.environ['PO_TEST_HOME']
home = os.path.abspath(os.path.expanduser('~'))
# Hard guard: never run against the real profile, even if the env override failed.
if not os.path.normcase(home).startswith(os.path.normcase(os.path.abspath(tmp_home))):
    print(json.dumps({'refused': 'home resolves outside the temporary profile'})); sys.exit(3)
if 'VISTA_PO_DATA_DIR' in os.environ:
    print(json.dumps({'refused': 'override still set'})); sys.exit(3)
sys.path.insert(0, os.environ['PO_REPO'])
expected = os.path.join(tmp_home, '.vista-platform')
import po_db, po_storage_api
out = {'modules': [po_db.__file__, po_storage_api.__file__], 'dbPath': po_db.db_path(),
       'afterImport': os.path.exists(expected)}
if sys.argv[1] == 'first-write':
    import po_model
    out['reads'] = [po_db.list_drafts(), po_db.counts(), po_db.get_draft('po_CHILDmissing01')]
    try:
        po_db.update_draft({'id': 'po_CHILDmissing01'}, 1)
        out['updateMissing'] = 'no error'
    except po_db.DraftGone:
        out['updateMissing'] = 'DraftGone'
    out['afterReads'] = os.path.exists(expected)
    doc, rev = po_db.create_draft(po_model.validate_draft({'id': 'po_CHILDfirst0001'}))
    out['afterWrite'] = os.path.isfile(po_db.db_path())
    out['created'] = rev
    out['readBack'] = po_db.get_draft('po_CHILDfirst0001')[1]
    out['count'] = po_db.counts()['drafts']
    doc2, rev2 = po_db.update_draft(dict(doc, title='second'), 1)
    out['updated'] = rev2
    try:
        po_db.update_draft(dict(doc, title='stale'), 1)
        out['stale'] = 'no error'
    except po_db.RevConflict:
        out['stale'] = 'RevConflict'
print(json.dumps(out))
'''


class T6NoImportSideEffects(unittest.TestCase):
    """Importing po_db / po_storage_api must not create or open storage."""

    def run_child(self, mode):
        import subprocess
        home = tempfile.mkdtemp(prefix='vista-po-home-')
        try:
            env = dict(os.environ)
            env.pop('VISTA_PO_DATA_DIR', None)
            for k in ('HOMEDRIVE', 'HOMEPATH'):
                env.pop(k, None)
            env.update({'USERPROFILE': home, 'HOME': home, 'PO_TEST_HOME': home,
                        'PO_REPO': os.path.dirname(os.path.dirname(os.path.abspath(__file__)))})
            r = subprocess.run([sys.executable, '-B', '-c', _CHILD, mode], env=env, cwd=home,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = json.loads(r.stdout.strip().splitlines()[-1])
            repo = os.path.normcase(env['PO_REPO'])
            for m in out['modules']:
                self.assertTrue(os.path.normcase(m).startswith(repo), m)
            self.assertTrue(os.path.normcase(out['dbPath']).startswith(os.path.normcase(home)))
            return out
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_import_creates_no_storage(self):
        out = self.run_child('import-only')
        self.assertFalse(out['afterImport'])

    def test_reads_create_nothing_and_first_write_initialises_schema(self):
        out = self.run_child('first-write')
        self.assertFalse(out['afterImport'])
        self.assertEqual(out['reads'], [[], {'drafts': 0}, None])
        self.assertEqual(out['updateMissing'], 'DraftGone')
        self.assertFalse(out['afterReads'])
        self.assertTrue(out['afterWrite'])
        self.assertEqual((out['created'], out['readBack'], out['count']), (1, 1, 1))
        self.assertEqual((out['updated'], out['stale']), (2, 'RevConflict'))


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
