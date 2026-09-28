"""
PO Generator — Phase 2 tests (original documents, immutable extractions,
imported items). Synthetic data only.

Run from the repo root:
    python -m unittest tests.test_po_phase2 -v

Storage: a temporary directory is set in setUpModule (and the previous
value restored in tearDownModule) — the production PO store is never used.
Importing the PO modules creates nothing (see test_po_phase1.T6).
"""
import hashlib
import http.client
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import po_db      # noqa: E402
import po_model   # noqa: E402

_TMP = None
_PREV = None
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
JPG = b'\xff\xd8\xff\xe0' + b'\x00' * 32


def setUpModule():
    global _TMP, _PREV
    _PREV = os.environ.get('VISTA_PO_DATA_DIR')
    _TMP = tempfile.mkdtemp(prefix='vista-po2-test-')
    os.environ['VISTA_PO_DATA_DIR'] = _TMP


def tearDownModule():
    if _PREV is None:
        os.environ.pop('VISTA_PO_DATA_DIR', None)
    else:
        os.environ['VISTA_PO_DATA_DIR'] = _PREV
    shutil.rmtree(_TMP, ignore_errors=True)


def draft(**over):
    d = {'id': 'po_P2TESTdraft01', 'schema': 1, 'status': 'draft', 'title': 'T', 'currency': 'SAR',
         'priceTaxBasis': 'exclusive', 'supplier': None, 'items': [],
         'paymentTerms': {'text': 't', 'isDraftDefault': True, 'balanceTrigger': 'undecided',
                          'milestones': [{'id': 'ms_advance', 'pct': '50', 'label': 'a'},
                                         {'id': 'ms_balance', 'pct': '50', 'label': 'b'}]},
         'projectRef': '', 'deliveryLocation': '', 'notes': '', 'createdAt': '', 'updatedAt': '', 'quotation': None}
    d.update(over)
    return d


SRC = 'src_' + 'a' * 24
EXID = 'ex_' + 'b' * 24


def ext_item(iid='it_ext000001', **over):
    it = {'id': iid, 'description': 'Acrylic sign', 'unit': 'pcs', 'qty': '10', 'unitPrice': '120.00', 'included': True,
          'tax': {'treatment': 'taxable', 'rate': '15', 'origin': 'default'}, 'source': 'extracted', 'createdAt': '', 'excludedAt': '',
          'sourceRef': {'sourceId': SRC, 'extractionId': EXID, 'rowId': 'r1'},
          'orig': {'ref': '1', 'description': 'Acrylic sign', 'unit': 'pcs', 'qty': '10', 'unitPrice': '120.00',
                   'discount': '', 'lineTotal': '1,200.00', 'tax': ''},
          'reviewFlags': ['default_tax_requires_review']}
    it.update(over)
    return it


def extraction_result(**over):
    r = {'version': 'po-extract-1', 'pages': [{'page': 1, 'method': 'text'}], 'header': {}, 'rows': [{'id': 'r1'}],
         'otherRows': [], 'totals': {}, 'warnings': [], 'pageText': []}
    r.update(over)
    return r


class P2Model(unittest.TestCase):
    def test_extracted_item_and_quotation_validate(self):
        q = {'sourceId': SRC, 'extractionId': EXID, 'ref': 'Q-1', 'dateRaw': '03/04/2026', 'dateAmbiguous': True,
             'currency': 'SAR', 'totals': {'grandTotal': '2248.25'}, 'flags': {'quotationDate': ['ambiguous_date']}}
        d = po_model.validate_draft(draft(items=[ext_item()], quotation=q))
        self.assertEqual(d['items'][0]['sourceRef']['rowId'], 'r1')
        self.assertEqual(d['items'][0]['orig']['lineTotal'], '1,200.00')
        self.assertTrue(d['quotation']['dateAmbiguous'])
        self.assertEqual(d['quotation']['totals']['grandTotal'], '2248.25')

    def errs(self, doc):
        with self.assertRaises(po_model.DraftInvalid) as cm:
            po_model.validate_draft(doc)
        return {e['path'] for e in cm.exception.errors}

    def test_rejections(self):
        self.assertIn('items[0].sourceRef', self.errs(draft(items=[ext_item(sourceRef=None)])))
        self.assertIn('items[0].sourceRef.rowId', self.errs(draft(items=[ext_item(sourceRef={'sourceId': SRC, 'extractionId': EXID, 'rowId': '../x'})])))
        manual = ext_item(source='manual')
        self.assertIn('items[0].sourceRef', self.errs(draft(items=[manual])))
        self.assertIn('items[0].reviewFlags', self.errs(draft(items=[ext_item(reviewFlags=['made_up'])])))
        self.assertIn('items[0].orig.secret', self.errs(draft(items=[ext_item(orig={'secret': 'x'})])))
        self.assertIn('quotation.sourceId', self.errs(draft(quotation={'sourceId': 'nope', 'extractionId': EXID})))
        self.assertIn('quotation.bogus', self.errs(draft(quotation={'sourceId': SRC, 'extractionId': EXID, 'bogus': 1})))

    def test_default_tax_review_blocks_issue_only_when_included(self):
        d = po_model.validate_draft(draft(items=[ext_item()]))
        codes = {b['code'] for b in po_model.readiness(d, po_model.compute(d))}
        self.assertIn('default_tax_unreviewed', codes)
        d2 = po_model.validate_draft(draft(items=[ext_item(included=False)]))
        self.assertNotIn('default_tax_unreviewed', {b['code'] for b in po_model.readiness(d2, po_model.compute(d2))})
        d3 = po_model.validate_draft(draft(items=[ext_item(reviewFlags=[], tax={'treatment': 'taxable', 'rate': '15', 'origin': 'user'})]))
        self.assertNotIn('default_tax_unreviewed', {b['code'] for b in po_model.readiness(d3, po_model.compute(d3))})

    def test_occurrence_and_gap_flags_validate(self):
        it = ext_item(orig=dict(ext_item()['orig'], occurrence='2'),
                      reviewFlags=['same_as_existing_item', 'document_adjustments_unapplied', 'from_incomplete_extraction'])
        d = po_model.validate_draft(draft(items=[it]))
        self.assertEqual(d['items'][0]['orig']['occurrence'], '2')

    def test_item_name_and_printed_dimensions(self):
        dims = {'values': {'W': '1.30', 'D': '', 'H': '0.85'}, 'unit': '', 'status': 'needs_confirmation', 'origin': 'extracted',
                'conflicts': [{'dim': 'H', 'column': '0.85', 'description': '1.11'}]}
        d = po_model.validate_draft(draft(items=[ext_item(name='Company fence', dimensions=dims)]))
        it = d['items'][0]
        self.assertEqual(it['name'], 'Company fence')
        self.assertEqual(it['dimensions'], dims)                         # blanks and labels kept as printed
        self.assertEqual(list(it['dimensions']['values']), ['W', 'D', 'H'])
        codes = lambda doc: {b['code'] for b in po_model.readiness(doc, po_model.compute(doc))}
        self.assertIn('dimensions_unconfirmed', codes(d))
        ok = po_model.validate_draft(draft(items=[ext_item(dimensions=dict(dims, status='confirmed', origin='user'))]))
        self.assertNotIn('dimensions_unconfirmed', codes(ok))
        excluded = po_model.validate_draft(draft(items=[ext_item(included=False, dimensions=dims)]))
        self.assertNotIn('dimensions_unconfirmed', codes(excluded))
        self.assertIsNone(po_model.validate_draft(draft(items=[ext_item()]))['items'][0]['dimensions'])
        for bad in ({'values': {'X': '1'}, 'unit': '', 'status': 'as_printed', 'origin': 'extracted'},
                    dict(dims, status='guessed'), dict(dims, origin='ocr'), dict(dims, extra=1),
                    dict(dims, conflicts=[{'dim': 'H', 'column': '1'}])):
            with self.assertRaises(po_model.DraftInvalid):
                po_model.validate_draft(draft(items=[ext_item(dimensions=bad)]))
        # description-only measurements are kept word for word (with or without dimension columns)
        des = dict(dims, described={'main': '2+2+2 X H 1.2 M', 'additional': ['POLE OF 1.5 M', 'BASE W 1.2 M X D 0.20 M']})
        got = po_model.validate_draft(draft(items=[ext_item(dimensions=des)]))['items'][0]['dimensions']
        self.assertEqual(got['described'], des['described'])
        self.assertEqual(got['values'], dims['values'])                  # blank D stays blank
        only_des = {'values': {}, 'unit': '', 'status': 'as_printed', 'origin': 'extracted', 'conflicts': [],
                    'described': {'main': '', 'additional': ['1m concrete base height with 1.5m underground']}}
        self.assertEqual(po_model.validate_draft(draft(items=[ext_item(dimensions=only_des)]))['items'][0]['dimensions']['described'],
                         only_des['described'])
        for bad in (dict(only_des, described={'main': '', 'additional': []}),                  # nothing at all
                    dict(des, described={'main': 'x', 'additional': ['a'] * 9}),              # too many parts
                    dict(des, described={'main': 'x', 'extra': 1})):
            with self.assertRaises(po_model.DraftInvalid):
                po_model.validate_draft(draft(items=[ext_item(dimensions=bad)]))
        # the name alone is enough for a complete line (the long description is internal)
        named = po_model.validate_draft(draft(items=[ext_item(name='Sign', description='', reviewFlags=[],
                                                              tax={'treatment': 'taxable', 'rate': '15', 'origin': 'user'})]))
        self.assertEqual(po_model.compute(named)['totals']['invalidItems'], [])

    def test_component_dimension_and_customer_evidence_validate(self):
        it = ext_item(orig=dict(ext_item()['orig'], parent='3 Wall graphics', dimensions='W 2.00 × H 2.00'),
                      reviewFlags=['component_of_item', 'spec_dimension_conflict', 'same_values_as_other_row'])
        q = {'sourceId': SRC, 'extractionId': EXID, 'customerName': 'Northwind', 'projectName': 'Harbor',
             'flags': {'quotationDate': ['filename_date_differs']}}
        d = po_model.validate_draft(draft(items=[it], quotation=q))
        self.assertEqual(d['items'][0]['orig']['parent'], '3 Wall graphics')
        self.assertEqual(d['items'][0]['orig']['dimensions'], 'W 2.00 × H 2.00')
        self.assertEqual((d['quotation']['customerName'], d['quotation']['projectName']), ('Northwind', 'Harbor'))
        # a row without a printed amount is never imported with one: 'no_amount' is only a flag
        d2 = po_model.validate_draft(draft(items=[ext_item(qty='', reviewFlags=['no_amount', 'dash_amount'])]))
        self.assertEqual(d2['items'][0]['qty'], '')

    def test_provisional_totals_and_blockers(self):
        base = ext_item(reviewFlags=[], tax={'treatment': 'taxable', 'rate': '15', 'origin': 'extracted'})
        clean = po_model.validate_draft(draft(items=[base]))
        c = po_model.compute(clean)
        self.assertFalse(c['totals']['provisional'])
        self.assertNotIn('adjustments_unapplied', {b['code'] for b in po_model.readiness(clean, c)})
        for flag in ('line_discount_present', 'document_adjustments_unapplied'):
            d = po_model.validate_draft(draft(items=[dict(base, reviewFlags=[flag])]))
            c = po_model.compute(d)
            self.assertTrue(c['totals']['provisional'], flag)
            self.assertEqual(c['totals']['gross'], '1380.00')          # computed, but marked provisional
            self.assertIn('adjustments_unapplied', {b['code'] for b in po_model.readiness(d, c)})
        # excluded items do not make the PO provisional
        d = po_model.validate_draft(draft(items=[dict(base, reviewFlags=['line_discount_present'], included=False)]))
        self.assertFalse(po_model.compute(d)['totals']['provisional'])
        d = po_model.validate_draft(draft(items=[dict(base, reviewFlags=['from_incomplete_extraction'])]))
        self.assertIn('incomplete_source_document', {b['code'] for b in po_model.readiness(d, po_model.compute(d))})

    def test_phase1_drafts_still_valid(self):
        legacy = draft()
        legacy.pop('quotation')
        legacy['items'] = [{'id': 'it_legacy01', 'description': 'x', 'unit': 'u', 'qty': '1', 'unitPrice': '1', 'included': True,
                            'tax': {'treatment': 'unresolved', 'rate': ''}, 'source': 'manual', 'createdAt': '', 'excludedAt': ''}]
        d = po_model.validate_draft(legacy)
        self.assertIsNone(d['quotation'])
        self.assertNotIn('sourceRef', d['items'][0])


class P2Storage(unittest.TestCase):
    def test_save_is_content_addressed_and_unchanged(self):
        meta, existing = po_db.save_source('po_STORE000001', r'..\..\evil\quote.pdf', 'application/pdf', PDF)
        self.assertFalse(existing)
        self.assertEqual(meta['name'], 'quote.pdf')
        self.assertEqual(meta['sha256'], hashlib.sha256(PDF).hexdigest())
        path = os.path.join(po_db.sources_dir(), meta['sha256'] + '.pdf')
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), PDF)
        again, existing2 = po_db.save_source('po_STORE000001', 'other-name.pdf', 'application/pdf', PDF)
        self.assertTrue(existing2)
        self.assertEqual(again['id'], meta['id'])
        got_meta, data = po_db.read_source_bytes(meta['id'])
        self.assertEqual(data, PDF)

    def test_invalid_files_rejected(self):
        with self.assertRaises(po_db.SourceInvalid):
            po_db.save_source('po_STORE000002', 'a.pdf', 'application/pdf', b'not a pdf')
        with self.assertRaises(po_db.SourceInvalid):
            po_db.save_source('po_STORE000002', 'a.png', 'image/png', JPG)
        with self.assertRaises(po_db.SourceInvalid):
            po_db.save_source('po_STORE000002', 'a.exe', 'application/octet-stream', PDF)
        with self.assertRaises(po_db.SourceInvalid):
            po_db.save_source('po_STORE000002', 'a.pdf', 'application/pdf', b'')
        big = b'%PDF-' + b'0' * (po_db.SOURCE_MAX_BYTES)
        with self.assertRaises(po_db.SourceInvalid):
            po_db.save_source('po_STORE000002', 'big.pdf', 'application/pdf', big)
        self.assertEqual(po_db.list_sources('po_STORE000002'), [])

    def test_rows_are_immutable(self):
        meta, _ = po_db.save_source('po_STORE000003', 'x.png', 'image/png', PNG)
        ex = po_db.save_extraction(meta['id'], 'test', extraction_result(), {'rows': 1})
        c = sqlite3.connect(po_db.db_path())
        try:
            for sql in ("UPDATE po_sources SET original_name='x' WHERE id=?", "DELETE FROM po_sources WHERE id=?"):
                with self.assertRaises(sqlite3.DatabaseError) as cm:
                    c.execute(sql, (meta['id'],))
                self.assertIn('immutable', str(cm.exception))
            for sql in ("UPDATE po_extractions SET data_json='{}' WHERE id=?", "DELETE FROM po_extractions WHERE id=?"):
                with self.assertRaises(sqlite3.DatabaseError):
                    c.execute(sql, (ex['id'],))
        finally:
            c.close()
        self.assertEqual(po_db.get_extraction(ex['id'])['data'], extraction_result())

    def test_tampered_file_is_detected_not_served(self):
        data = JPG + b'unique-tamper'
        meta, _ = po_db.save_source('po_STORE000004', 'x.jpg', 'image/jpeg', data)
        path = os.path.join(po_db.sources_dir(), meta['sha256'] + '.jpg')
        os.chmod(path, 0o666)
        with open(path, 'ab') as f:
            f.write(b'!')
        with self.assertRaises(po_db.SourceIntegrityError):
            po_db.read_source_bytes(meta['id'])

    def test_lookup_ids_are_validated(self):
        self.assertIsNone(po_db.get_source('../../etc/passwd'))
        self.assertIsNone(po_db.read_source_bytes('src_zz'))
        self.assertIsNone(po_db.get_extraction('ex_../x'))


class P2Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import proxy
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), proxy.VistaProxyHandler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        s, j, _ = cls.req(cls, 'POST', '/api/po/drafts', json.dumps(draft(id='po_P2HTTPdraft1')).encode(), 'application/json')
        assert s == 201, j

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, ctype=None, headers=None, raw=False):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        h = {'Host': f'127.0.0.1:{self.port}'}
        if ctype:
            h['Content-Type'] = ctype
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        if raw:
            return r.status, data, hdrs
        try:
            return r.status, json.loads(data), hdrs
        except ValueError:
            return r.status, data, hdrs

    def upload(self, data, ctype='application/pdf', name='quote.pdf', draft_id='po_P2HTTPdraft1', headers=None):
        from urllib.parse import quote
        return self.req('POST', f'/api/po/sources?draftId={draft_id}&name={quote(name)}', data, ctype, headers)

    def test_upload_list_download(self):
        s, j, _ = self.upload(PDF + b'% one', name='عرض سعر ٧.pdf')
        self.assertEqual(s, 201, j)
        sid = j['data']['id']
        self.assertEqual(j['data']['name'], 'عرض سعر ٧.pdf')
        self.assertEqual(self.upload(PDF + b'% one')[0], 200)             # same bytes → existing record
        s, lst, _ = self.req('GET', '/api/po/sources?draftId=po_P2HTTPdraft1')
        self.assertIn(sid, [x['id'] for x in lst['data']])
        s, data, h = self.req('GET', f'/api/po/sources/{sid}/file', raw=True)
        self.assertEqual((s, data), (200, PDF + b'% one'))
        self.assertTrue(h['content-disposition'].startswith('attachment;'))
        self.assertEqual(h['x-content-type-options'], 'nosniff')
        self.assertIn('sandbox', h['content-security-policy'])
        self.assertEqual(h['content-type'], 'application/pdf')
        self.assertEqual(h['x-source-sha256'], hashlib.sha256(PDF + b'% one').hexdigest())
        self.assertNotIn('access-control-allow-origin', h)

    def test_upload_rejections(self):
        self.assertEqual(self.upload(PDF, draft_id='po_NOSUCHdraft1')[0], 404)
        self.assertEqual(self.upload(b'<html>', ctype='text/html')[0], 415)
        self.assertEqual(self.upload(b'not a pdf at all')[0], 400)
        self.assertEqual(self.upload(PNG, ctype='application/pdf')[0], 400)
        s, j, _ = self.upload(b'%PDF-x', headers={'Content-Length': str(po_db.SOURCE_MAX_BYTES + 1)})
        self.assertEqual(s, 413)
        self.assertEqual(self.upload(PDF, headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.upload(PDF, headers={'X-Forwarded-For': '1.2.3.4'})[0], 403)

    def test_file_routes_expose_no_paths(self):
        for p in ('/api/po/sources/..%2f..%2fconfig.json/file', '/api/po/sources/src_x/file',
                  '/api/po/sources/' + 'src_' + '0' * 24 + '/file', '/api/po/sources/%2e%2e/file'):
            self.assertEqual(self.req('GET', p)[0], 404, p)
        for m in ('PUT', 'DELETE'):
            self.assertEqual(self.req(m, '/api/po/sources/' + 'src_' + '0' * 24)[0], 405 if m == 'DELETE' else 404)

    def test_extraction_store_is_insert_only_and_exact(self):
        s, j, _ = self.upload(PDF + b'% two')
        sid = j['data']['id']
        result = extraction_result(rows=[{'id': 'r1', 'fields': {'description': {'value': 'وصف <b>x</b>', 'raw': 'وصف <b>x</b>'}}}])
        s, j, _ = self.req('POST', '/api/po/extractions', json.dumps({'sourceId': sid, 'engine': 'po-extract-1', 'result': result}).encode(), 'application/json')
        self.assertEqual(s, 201, j)
        exid = j['data']['id']
        self.assertEqual(j['data']['summary']['rows'], 1)
        s, got, _ = self.req('GET', f'/api/po/extractions/{exid}')
        self.assertEqual(got['data']['data'], result)                     # stored exactly as sent
        s, lst, _ = self.req('GET', f'/api/po/extractions?sourceId={sid}')
        self.assertEqual([x['id'] for x in lst['data']], [exid])
        # a rerun creates a NEW record; the first is unchanged
        s, j2, _ = self.req('POST', '/api/po/extractions', json.dumps({'sourceId': sid, 'engine': 'po-extract-1', 'result': extraction_result()}).encode(), 'application/json')
        self.assertNotEqual(j2['data']['id'], exid)
        self.assertEqual(self.req('GET', f'/api/po/extractions/{exid}')[1]['data']['data'], result)
        self.assertEqual(self.req('PUT', f'/api/po/extractions/{exid}', b'{}', 'application/json')[0], 404)
        self.assertEqual(self.req('DELETE', f'/api/po/extractions/{exid}')[0], 405)

    def test_extraction_rejections(self):
        s, j, _ = self.upload(PDF + b'% three')
        sid = j['data']['id']
        post = lambda body: self.req('POST', '/api/po/extractions', json.dumps(body).encode(), 'application/json')[0]
        self.assertEqual(post({'sourceId': 'src_' + 'f' * 24, 'engine': 'e', 'result': extraction_result()}), 404)
        self.assertEqual(post({'sourceId': sid, 'engine': 'e', 'result': {'rows': []}}), 400)
        self.assertEqual(post({'sourceId': sid, 'engine': 'e', 'result': extraction_result(rows=[{'t': 'a\x00b'}])}), 400)
        self.assertEqual(post({'sourceId': sid, 'engine': '', 'result': extraction_result()}), 400)
        self.assertEqual(self.req('POST', '/api/po/extractions', b'x' * 10, 'text/plain')[0], 415)

    def test_draft_with_imported_items_round_trips(self):
        s, j, _ = self.req('GET', '/api/po/drafts/po_P2HTTPdraft1')
        doc, rev = j['data']['data'], j['data']['rev']
        doc['items'] = [ext_item()]
        doc['quotation'] = {'sourceId': SRC, 'extractionId': EXID, 'ref': 'Q-9', 'dateRaw': '20/09/2026', 'dateIso': '2026-09-20'}
        s, j, _ = self.req('PUT', '/api/po/drafts/po_P2HTTPdraft1', json.dumps(doc).encode(), 'application/json', {'If-Match': str(rev)})
        self.assertEqual(s, 200, j)
        codes = {b['code'] for b in j['data']['readiness']}
        self.assertIn('default_tax_unreviewed', codes)
        s, j, _ = self.req('GET', '/api/po/drafts/po_P2HTTPdraft1')
        it = j['data']['data']['items'][0]
        self.assertEqual((it['source'], it['orig']['lineTotal'], it['reviewFlags']), ('extracted', '1,200.00', ['default_tax_requires_review']))
        self.assertEqual(j['data']['data']['quotation']['dateIso'], '2026-09-20')

    def test_status_reports_upload_limits(self):
        s, j, _ = self.req('GET', '/api/po/status')
        self.assertEqual(j['data']['uploads']['maxBytes'], po_db.SOURCE_MAX_BYTES)
        self.assertEqual(sorted(j['data']['uploads']['types']), ['application/pdf', 'image/jpeg', 'image/png'])


if __name__ == '__main__':
    unittest.main()
