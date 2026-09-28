"""
PO Generator — Phase 3 tests: totals with discounts / charges, quotation
reconciliation, PO settings, PDF layout, issuance (atomic numbering, duplicate
protection, immutable snapshots and stored PDFs) and revisions. Synthetic data
and FICTIONAL settings only.

Run from the repo root:
    python -m unittest tests.test_po_phase3 -v

Storage: a temporary directory (VISTA_PO_DATA_DIR) — never the production
store. PDF rendering is replaced by a deterministic fake except in
PdfRealRender, which uses the local Edge/Chrome if present (skipped otherwise).
"""
import hashlib
import http.client
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import po_db      # noqa: E402
import po_model   # noqa: E402
import po_pdf     # noqa: E402
from tests import po_test_support   # noqa: E402

_TMP = _PREV = None
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40
FICTIONAL = {'buyer': {'name': 'Example Trading Test Co.', 'nameAr': '', 'vat': '399999999900003', 'cr': '0000000000',
                       'address': '1 Test Street, Nowhere', 'phone': '', 'email': ''},
             'buyerConfirmed': True, 'numbering': {'pattern': 'TEST-PO-{YYYY}-{SEQ}', 'seqWidth': 4, 'start': 1},
             'numberingConfirmed': True, 'approval': {'required': True, 'approverName': 'Test Approver'},
             'approvalConfirmed': True, 'testMode': True}
RENDERED = []


def fake_pdf(doc_html):
    RENDERED.append(doc_html)
    return b'%PDF-1.4\n% fake ' + hashlib.sha256(doc_html.encode('utf-8')).hexdigest().encode() + b'\n%%EOF\n'


def render(doc, computed, settings, issue, photo_bytes):
    return fake_pdf(po_pdf.build_html(doc, computed, settings, issue, photo_bytes))


def setUpModule():
    global _TMP, _PREV
    _PREV = os.environ.get('VISTA_PO_DATA_DIR')
    _TMP = tempfile.mkdtemp(prefix='vista-po3-test-')
    os.environ['VISTA_PO_DATA_DIR'] = _TMP
    po_db.put_settings(po_model.validate_settings(FICTIONAL), 0)


def tearDownModule():
    if _PREV is None:
        os.environ.pop('VISTA_PO_DATA_DIR', None)
    else:
        os.environ['VISTA_PO_DATA_DIR'] = _PREV
    shutil.rmtree(_TMP, ignore_errors=True)


def item(iid, qty='2', price='100', **over):
    it = {'id': iid, 'name': 'Acrylic sign ' + iid[-3:], 'description': 'Acrylic sign\nLONG INTERNAL SPECIFICATION TEXT',
          'unit': 'pcs', 'qty': qty, 'unitPrice': price, 'included': True,
          'tax': {'treatment': 'taxable', 'rate': '15', 'origin': 'user'}, 'source': 'manual', 'createdAt': '', 'excludedAt': ''}
    it.update(over)
    return it


def draft(did='po_P3TESTdraft01', **over):
    d = {'id': did, 'schema': 1, 'status': 'draft', 'title': 'Test PO', 'currency': 'SAR', 'priceTaxBasis': 'exclusive',
         'supplier': {'daftraId': '7', 'name': 'Fictional Supplier Est.', 'snapshot': {'vatNumber': '300000000000003'}},
         'items': [item('it_P3item001')],
         'paymentTerms': {'text': '50% advance; 50% one calendar month after delivery.', 'isDraftDefault': True, 'balanceTrigger': 'delivery',
                          'milestones': [{'id': 'ms_advance', 'pct': '50', 'label': 'Advance'}, {'id': 'ms_balance', 'pct': '50', 'label': 'Balance'}]},
         'projectRef': '', 'deliveryLocation': 'Test site', 'notes': 'INTERNAL NOTE NEVER PRINTED', 'poNotes': 'Deliver in working hours.', 'createdAt': '', 'updatedAt': '', 'quotation': None}
    d.update(over)
    return po_model.validate_draft(d)


def ok(rev, daftra_id='7'):
    """The server-side supplier verification result for this draft revision (see po_storage_api.verify_supplier)."""
    return {'status': 'verified', 'at': '2026-09-28T10:00:00Z', 'daftraId': daftra_id, 'detail': '', 'draftRev': rev}


def adj(aid, kind, amount, treat='taxable', rate='15', src=''):
    return {'id': aid, 'kind': kind, 'label': kind.title(), 'amount': amount, 'tax': {'treatment': treat, 'rate': rate}, 'sourceKey': src}


def quotation(**tot):
    return {'sourceId': 'src_' + 'a' * 24, 'extractionId': 'ex_' + 'b' * 24, 'ref': 'Q-1', 'totals': tot}


class Totals(unittest.TestCase):
    def test_line_discount_and_document_adjustments(self):
        d = draft(items=[item('it_P3item001', '2', '100', discount='20'), item('it_P3item002', '1', '50')],
                  adjustments=[adj('adj_DISC0001', 'discount', '30'), adj('adj_CHRG0001', 'charge', '10', 'zero_rated', '')])
        c = po_model.compute(d)
        t = c['totals']
        self.assertEqual(c['lines'][0]['lineAmount'], '180.00')       # 200 - 20
        self.assertEqual(t['itemsNet'], '230.00')
        self.assertEqual(c['adjustments'][0]['net'], '-30.00')
        groups = {g['treatment']: g for g in t['taxGroups']}
        self.assertEqual((groups['taxable']['net'], groups['taxable']['tax']), ('200.00', '30.00'))   # 230 − 30 discount, VAT on the base
        self.assertEqual((groups['zero_rated']['net'], groups['zero_rated']['tax']), ('10.00', '0.00'))
        self.assertEqual((t['net'], t['tax'], t['gross']), ('210.00', '30.00', '240.00'))   # 230-30+10 ; 34.50-4.50 ; zero-rated charge
        self.assertEqual({g['treatment'] for g in t['taxGroups']}, {'taxable', 'zero_rated'})
        self.assertEqual([m['amount'] for m in c['milestones']], ['120.00', '120.00'])

    def test_jazaaco_source_totals_follow_zatca_category_vat(self):
        # the unchanged Jazaaco quotation net 398,667.91 at 15 %: per-category VAT 59,800.19 (per-line VAT summed gave 59,800.18)
        d = draft(items=[item('it_P3item001', '1', '398667.91')], quotation=quotation(subtotal='398667.91', vat='59800.19', grandTotal='458468.10'))
        c = po_model.compute(d)
        self.assertEqual((c['totals']['net'], c['totals']['tax'], c['totals']['gross']), ('398667.91', '59800.19', '458468.10'))
        self.assertEqual(c['reconciliation']['status'], 'matched')
        # several lines of one category: VAT is rounded once on the category base
        many = draft(items=[item('it_P3item%03d' % k, '1', '0.10') for k in range(1, 8)])
        self.assertEqual(po_model.compute(many)['totals']['tax'], '0.11')          # 0.70 × 15 % = 0.105 → 0.11 (per line: 7 × 0.02 = 0.14)
        mixed = draft(items=[item('it_P3item001', '1', '100'), item('it_P3item002', '1', '50', tax={'treatment': 'exempt', 'rate': '', 'origin': 'user'}),
                             item('it_P3item003', '1', '20', tax={'treatment': 'zero_rated', 'rate': '', 'origin': 'user'})])
        t = po_model.compute(mixed)['totals']
        self.assertEqual((t['net'], t['tax'], t['gross']), ('170.00', '15.00', '185.00'))
        self.assertEqual({g['treatment']: g['tax'] for g in t['taxGroups']}, {'taxable': '15.00', 'exempt': '0.00', 'zero_rated': '0.00'})

    def test_inclusive_prices_and_invalid_cases(self):
        d = draft(priceTaxBasis='inclusive', items=[item('it_P3item001', '1', '115')], adjustments=[adj('adj_DISC0001', 'discount', '11.50')])
        t = po_model.compute(d)['totals']
        self.assertEqual((t['net'], t['tax'], t['gross']), ('90.00', '13.50', '103.50'))
        big = po_model.compute(draft(items=[item('it_P3item001', '1', '10', discount='11')]))
        self.assertIn('discount larger than the line amount', big['lines'][0]['issues'])
        neg = po_model.compute(draft(items=[item('it_P3item001', '1', '10')], adjustments=[adj('adj_DISC0001', 'discount', '50')]))
        self.assertEqual(neg['totals']['status'], 'negative_total')
        with self.assertRaises(po_model.DraftInvalid):
            draft(adjustments=[adj('adj_DISC0001', 'discount', '1', src='quotation:discount'), adj('adj_DISC0002', 'discount', '2', src='quotation:discount')])

    def test_quotation_adjustments_must_be_applied_or_dismissed(self):
        q = quotation(subtotal='200', discount='20', vat='27', grandTotal='207')
        d = draft(quotation=q)
        c = po_model.compute(d)
        self.assertEqual([a['key'] for a in c['totals']['unappliedAdjustments']], ['quotation:discount'])
        codes = {b['code'] for b in po_model.readiness(d, c, FICTIONAL)}
        self.assertIn('adjustments_unapplied', codes)
        applied = draft(quotation=q, adjustments=[adj('adj_DISC0001', 'discount', '20', src='quotation:discount')])
        c2 = po_model.compute(applied)
        self.assertEqual(c2['totals']['unappliedAdjustments'], [])
        self.assertEqual(c2['reconciliation']['status'], 'matched')      # 200-20=180 net, 27 VAT, 207
        self.assertEqual(po_model.readiness(applied, c2, FICTIONAL), [])
        dismissed = draft(quotation=q, reconciliation={'note': '', 'acceptedTotals': None, 'acceptedAt': '',
                                                       'dismissed': [{'key': 'quotation:discount', 'reason': 'Discount withdrawn by supplier'}]})
        self.assertEqual(po_model.compute(dismissed)['totals']['unappliedAdjustments'], [])
        with self.assertRaises(po_model.DraftInvalid):
            draft(reconciliation={'dismissed': [{'key': 'quotation:discount', 'reason': ' '}]})

    def test_reconciliation_differences_need_a_note_for_the_current_totals(self):
        same = po_model.compute(draft(quotation=quotation(subtotal='200', vat='30', grandTotal='230')))
        self.assertEqual(same['reconciliation']['status'], 'matched')   # 2 × 100 + 15 % = 230.00
        d = draft(quotation=quotation(subtotal='200', vat='29.99', grandTotal='229.99'))
        c = po_model.compute(d)
        self.assertEqual(c['reconciliation']['status'], 'differs')
        rows = {r['key']: r for r in c['reconciliation']['checks']}
        self.assertEqual(rows['subtotal']['status'], 'match')
        self.assertEqual((rows['vat']['status'], rows['vat']['difference']), ('differs', '0.01'))
        self.assertEqual(rows['grandTotal']['difference'], '0.01')
        self.assertIn('totals_unreconciled', {b['code'] for b in po_model.readiness(d, c, FICTIONAL)})
        acc = {'note': 'Supplier rounding', 'acceptedTotals': c['reconciliation']['currentTotals'], 'acceptedAt': '', 'dismissed': []}
        ok = draft(quotation=d['quotation'], reconciliation=acc)
        self.assertTrue(po_model.compute(ok)['reconciliation']['accepted'])
        changed = draft(quotation=d['quotation'], reconciliation=acc, items=[item('it_P3item001', '3', '100')])
        self.assertFalse(po_model.compute(changed)['reconciliation']['accepted'], 'a changed total re-opens the difference')
        nonote = draft(quotation=d['quotation'], reconciliation=dict(acc, note=''))
        self.assertFalse(po_model.compute(nonote)['reconciliation']['accepted'])

    def test_legacy_dimensions_block_issue(self):
        legacy = item('it_P3legacy1', source='extracted', name='', dimensions=None,
                      sourceRef={'sourceId': 'src_' + 'a' * 24, 'extractionId': 'ex_' + 'b' * 24, 'rowId': 'r1'},
                      orig={'ref': '1', 'description': 'Sign', 'dimensions': 'W 1.30 × H 0.85'})
        d = draft(items=[legacy])
        self.assertIn('legacy_dimensions_review', {b['code'] for b in po_model.readiness(d, po_model.compute(d), FICTIONAL)})


class Settings(unittest.TestCase):
    def test_validation_and_confirmation_rules(self):
        s = po_model.validate_settings(FICTIONAL)
        self.assertEqual(po_model.format_po_number('TEST-PO-{YYYY}-{SEQ}', 4, 2026, 7), 'TEST-PO-2026-0007')
        self.assertEqual(po_model.revision_label('TEST-PO-2026-0007', 0), 'TEST-PO-2026-0007')
        self.assertEqual(po_model.revision_label('TEST-PO-2026-0007', 2), 'TEST-PO-2026-0007 Rev 2')
        for bad in (dict(s, numbering=dict(s['numbering'], pattern='PO-{YYYY}')), dict(s, numbering=dict(s['numbering'], pattern='<b>{SEQ}')),
                    dict(s, buyer=dict(s['buyer'], vat=''), buyerConfirmed=True),
                    dict(s, approval={'required': True, 'approverName': ''}, approvalConfirmed=True), dict(s, extra=1)):
            with self.assertRaises(po_model.DraftInvalid):
                po_model.validate_settings(bad)
        self.assertFalse(po_model.default_settings()['buyerConfirmed'])


class PdfLayout(unittest.TestCase):
    def test_item_order_spec_excluded_photos_and_marks(self):
        it = item('it_P3item001', name='Lobby sign', dimensions={'values': {'W': '1.20', 'D': '', 'H': '0.90'}, 'unit': 'M', 'status': 'confirmed',
                                                                   'origin': 'user', 'conflicts': [], 'described': {'main': '', 'additional': ['BASE W 1.2 M X D 0.20 M']}})
        ph = {'id': 'pa_PHOTO0001', 'photoId': 'ph_' + '1' * 24, 'sourceId': 'src_' + 'a' * 24, 'page': 1, 'region': {'x': 1, 'y': 2, 'w': 30, 'h': 20},
              'kind': 'embedded', 'target': {'kind': 'item', 'itemId': 'it_P3item001'}, 'status': 'confirmed', 'origin': 'user',
              'includeInPdf': True, 'reasons': [], 'suggestion': None, 'updatedAt': ''}
        off = dict(ph, id='pa_PHOTO0002', photoId='ph_' + '2' * 24, includeInPdf=False)
        d = draft(items=[it], photos=[ph, off])
        h = po_pdf.build_html(d, po_model.compute(d), FICTIONAL, None, {ph['photoId']: ('image/png', PNG), off['photoId']: ('image/png', PNG + b'x')})
        body = h.split('<tbody class="blk">')[1]
        self.assertIn('data:image/png', body.split('</tbody>')[0], 'the item and its photos form one unbreakable block')
        pos = [body.index(x) for x in ('Lobby sign', 'W 1.20 × D — × H 0.90 M', 'BASE W 1.2 M X D 0.20 M', '>2<', '>230.00<' if False else '>200.00<', 'data:image/png')]
        self.assertEqual(pos, sorted(pos), 'name → dimensions → measurements → qty/pricing → photos')
        self.assertEqual(h.count(po_pdf.logo_data_uri()), 1, 'the Vista logo is embedded (no external loading)')
        self.assertEqual(h.replace(po_pdf.logo_data_uri(), '').count('data:image/png'), 1, 'only photos ticked "Include in PO PDF"')
        self.assertNotIn('src="http', h)
        self.assertIn('@top-center', h, 'every draft page carries a DRAFT mark in its margin')
        self.assertNotIn('LONG INTERNAL SPECIFICATION TEXT', h)
        self.assertNotIn('INTERNAL NOTE NEVER PRINTED', h, 'internal notes stay internal')
        self.assertIn('Deliver in working hours.', h, 'PO notes are printed')
        self.assertIn('>DRAFT<', h)
        self.assertIn('TEST DOCUMENT', h)
        self.assertIn('Not a tax invoice', h)
        pos = [h.index(x) for x in ('Total excluding VAT', 'VAT 15% on 200.00', 'VAT total', 'Total including VAT (SAR)')]
        self.assertEqual(pos, sorted(pos), 'excluding VAT → VAT by rate → VAT total → including VAT')
        issued = po_pdf.build_html(d, po_model.compute(d), dict(FICTIONAL, testMode=False),
                                   {'displayNo': 'PO-2026-0001 Rev 1', 'revisionNo': 1, 'issuedAt': '2026-09-28T10:00:00Z', 'approvedBy': 'A',
                                    'reason': 'Qty <changed>', 'previous': {'displayNo': 'PO-2026-0001', 'issuedAt': '2026-09-20T10:00:00Z'}}, {})
        self.assertNotIn('>DRAFT<', issued)
        self.assertNotIn('@top-center', issued)
        self.assertIn('Vista United', issued)
        # totals that cannot be calculated are explained, never printed blank or as zero
        bad = draft(items=[dict(it, unitPrice='')], photos=[])
        hb = po_pdf.build_html(bad, po_model.compute(bad), FICTIONAL, None, {})
        self.assertIn('Totals not calculated', hb)
        self.assertIn('Item 1 (Lobby sign): unit price missing', hb)
        self.assertNotIn('Total including VAT', hb)
        # buyer details: a line and its label only when the value is set
        self.assertIn('VAT No.: 399999999900003', h)
        self.assertIn('CR No.: 0000000000', h)
        self.assertIn('Authorised signature — Example Trading Test Co.', h)
        blank = dict(FICTIONAL, buyer={'name': '', 'nameAr': '', 'vat': ' ', 'cr': '', 'address': '', 'phone': '', 'email': ''})
        hbl = po_pdf.build_html(d, po_model.compute(d), blank, None, {})
        for gone in ('VAT No.', 'CR No.', 'Authorised signature —'):
            self.assertNotIn(gone, hbl)
        head = hbl[hbl.index('<div class="top">'):hbl.index('<h1>')]
        self.assertIn('class="logo"', head)
        self.assertNotIn('class="muted"', head, 'no empty buyer lines under the logo')
        self.assertIn('Authorised signature</div>', hbl)
        self.assertIn('VAT 300000000000003', hbl, 'supplier VAT stays')
        self.assertIn('Total including VAT (SAR)', hbl, 'VAT calculations stay')
        # a blank unit label: totals are printed, with the gap as a separate review warning
        nu = draft(items=[dict(it, unit='')], photos=[])
        hn = po_pdf.build_html(nu, po_model.compute(nu), FICTIONAL, None, {})
        self.assertNotIn('Totals not calculated', hn)
        self.assertIn('Total including VAT (SAR)', hn)
        self.assertIn('Review before issuing', hn)
        self.assertIn('Unit missing — item(s) 1', hn)
        self.assertIn('supersedes PO-2026-0001', issued)
        self.assertIn('Qty &lt;changed&gt;', issued, 'escaped')

    def test_parent_photo_printed_once_under_first_included_component(self):
        src = 'src_' + 'a' * 24
        comp = lambda iid, inc: item(iid, included=inc, source='extracted', sourceRef={'sourceId': src, 'extractionId': 'ex_' + 'b' * 24, 'rowId': 'r1'},
                                     orig={'parent': '11 WALL GRAPHICS'})
        ph = {'id': 'pa_PHOTO0001', 'photoId': 'ph_' + '3' * 24, 'sourceId': src, 'page': 1, 'region': {'x': 1, 'y': 2, 'w': 30, 'h': 20},
              'kind': 'embedded', 'target': {'kind': 'group', 'sourceId': src, 'parent': '11 WALL GRAPHICS'}, 'status': 'suggested',
              'origin': 'auto', 'includeInPdf': True, 'reasons': [], 'suggestion': None, 'updatedAt': ''}
        d = draft(items=[comp('it_P3comp001', False), comp('it_P3comp002', True), comp('it_P3comp003', True)], photos=[ph])
        self.assertEqual(list(po_pdf.item_photos(d)), ['it_P3comp002'])


class Issuance(unittest.TestCase):
    def new_draft(self, did, **over):
        doc, rev = po_db.create_draft(draft(did, **over))
        return doc, rev

    def test_atomic_numbering_duplicates_and_locking(self):
        d1, r1 = self.new_draft('po_P3ISSUEdraft1')
        d2, r2 = self.new_draft('po_P3ISSUEdraft2')
        m1, dup = po_db.issue_draft(d1['id'], r1, 'key-issue-000000001', '', 'Test Approver', 'tester', render, ok(r1))
        self.assertFalse(dup)
        self.assertRegex(m1['displayNo'], r'^TEST-PO-\d{4}-\d{4}$')
        again, dup2 = po_db.issue_draft(d1['id'], r1, 'key-issue-000000001', '', 'Test Approver', 'tester', render, ok(r1))
        self.assertEqual((again['id'], dup2), (m1['id'], True), 'the same request twice → the same issue')
        with self.assertRaises(po_db.DraftIssued):
            po_db.issue_draft(d1['id'], r1, 'key-issue-000000002', '', 'Test Approver', 'tester', render, ok(r1))
        m2, _ = po_db.issue_draft(d2['id'], r2, 'key-issue-000000003', '', 'Test Approver', 'tester', render, ok(r2))
        n1, n2 = int(m1['displayNo'][-4:]), int(m2['displayNo'][-4:])
        self.assertEqual(n2, n1 + 1)
        with self.assertRaises(po_db.DraftIssued):
            po_db.update_draft(dict(d1, title='changed'), r1)

    def test_blocked_or_failed_issue_consumes_nothing(self):
        d, r = self.new_draft('po_P3ISSUEdraft3')
        counters = lambda: sqlite3.connect(po_db.db_path()).execute('SELECT COUNT(*), COALESCE(SUM(next),0) FROM po_counters').fetchone()
        before = counters()
        with self.assertRaises(po_db.NotReady) as cm:
            po_db.issue_draft(d['id'], r, 'key-issue-000000010', '', 'Someone Else', 'tester', render, ok(r))
        self.assertIn('approval_missing', {b['code'] for b in cm.exception.blockers})
        with self.assertRaises(po_db.RevConflict):
            po_db.issue_draft(d['id'], r + 5, 'key-issue-000000011', '', 'Test Approver', 'tester', render, ok(r + 5))

        def broken(*a):
            raise po_pdf.PdfRenderError('no browser')
        with self.assertRaises(po_pdf.PdfRenderError):
            po_db.issue_draft(d['id'], r, 'key-issue-000000012', '', 'Test Approver', 'tester', broken, ok(r))
        self.assertEqual(counters(), before, 'no number consumed')
        self.assertIsNone(po_db.issue_for_draft(d['id']))
        m, _ = po_db.issue_draft(d['id'], r, 'key-issue-000000013', '', 'test approver', 'tester', render, ok(r))   # approver match ignores case
        self.assertEqual(m['approvedBy'], 'test approver')

    def test_snapshot_pdf_and_photo_hashes_are_immutable(self):
        src, _ = po_db.save_source('po_P3ISSUEdraft4', 'q.pdf', 'application/pdf', PDF + b'%issue4')
        pm, _ = po_db.save_photo(src['id'], 1, {'x': 1, 'y': 2, 'w': 30, 'h': 20}, 'embedded', 'image/png', PNG + b'issue4')
        ph = {'id': 'pa_PHOTO0004', 'photoId': pm['id'], 'sourceId': src['id'], 'page': 1, 'region': pm['region'], 'kind': 'embedded',
              'target': {'kind': 'item', 'itemId': 'it_P3item001'}, 'status': 'confirmed', 'origin': 'user', 'includeInPdf': True,
              'reasons': [], 'suggestion': None, 'updatedAt': ''}
        d, r = self.new_draft('po_P3ISSUEdraft4', photos=[ph])
        m, _ = po_db.issue_draft(d['id'], r, 'key-issue-000000020', '', 'Test Approver', 'tester', render, ok(r))
        got = po_db.get_issue(m['id'])
        self.assertEqual(got['snapshot']['photos'][0]['sha256'], pm['sha256'])
        self.assertTrue(got['current'])
        self.assertEqual([e['event'] for e in got['events']], ['issued'])
        meta, pdf = po_db.read_issue_pdf(m['id'])
        self.assertEqual(hashlib.sha256(pdf).hexdigest(), m['pdfSha256'])
        c = sqlite3.connect(po_db.db_path())
        try:
            for sql, arg in (("UPDATE po_issues SET display_no='X' WHERE id=?", m['id']), ("DELETE FROM po_issues WHERE id=?", m['id']),
                             ("UPDATE po_issue_events SET detail='x' WHERE issue_id=?", m['id']), ("DELETE FROM po_bases WHERE id=?", m['baseId']),
                             ("UPDATE po_bases SET base_no='X' WHERE id=?", m['baseId'])):
                with self.assertRaises(sqlite3.DatabaseError):
                    c.execute(sql, (arg,))
        finally:
            c.close()
        path = os.path.join(po_db.issued_dir(), m['pdfSha256'] + '.pdf')
        os.chmod(path, 0o666)
        with open(path, 'ab') as f:
            f.write(b'tamper')
        with self.assertRaises(po_db.SourceIntegrityError):
            po_db.read_issue_pdf(m['id'])

    def test_revision_workflow(self):
        dims = {'values': {'W': '1.30', 'D': '', 'H': '0.85'}, 'unit': '', 'status': 'as_printed', 'origin': 'extracted', 'conflicts': []}
        d, r = self.new_draft('po_P3REVdraft001', items=[item('it_P3item001', dimensions=dims)])
        orig, _ = po_db.issue_draft(d['id'], r, 'key-issue-000000030', '', 'Test Approver', 'tester', render, ok(r))
        orig_pdf = po_db.read_issue_pdf(orig['id'])[1]
        rd, rrev = po_db.revise_issue(orig['id'], 'po_P3REVdraft002')
        self.assertEqual(rd['revision'], {'baseId': orig['baseId'], 'baseNo': orig['displayNo'], 'basedOnIssueId': orig['id'], 'basedOnRevision': 0})
        self.assertTrue(po_db.get_issue(orig['id'])['current'], 'the issued PO stays current while its revision is a draft')
        with self.assertRaises(po_db.OpenRevisionExists):
            po_db.revise_issue(orig['id'], 'po_P3REVdraft003')
        with self.assertRaises(ValueError):                           # the revision reference cannot be forged
            po_db.update_draft(dict(rd, revision=dict(rd['revision'], basedOnRevision=5)), rrev)
        rd2, rrev2 = po_db.update_draft(dict(rd, items=[item('it_P3item001', '3', '100', dimensions=dims)]), rrev)
        chg = po_model.changes(po_db.get_issue(orig['id'])['snapshot']['draft'], rd2)
        self.assertIn(('Item', '2', '3'), [(c['area'], c['before'], c['after']) for c in chg])
        self.assertEqual([c for c in chg if c['label'].endswith('Dimensions')], [], 'unchanged dimensions are not reported')
        with self.assertRaises(po_db.NotReady) as cm:
            po_db.issue_draft(rd2['id'], rrev2, 'key-issue-000000031', '', 'Test Approver', 'tester', render, ok(rrev2))
        self.assertIn('revision_reason_missing', {b['code'] for b in cm.exception.blockers})
        rev1, _ = po_db.issue_draft(rd2['id'], rrev2, 'key-issue-000000032', 'Quantity increased to 3', 'Test Approver', 'tester', render, ok(rrev2))
        self.assertEqual(rev1['displayNo'], orig['displayNo'] + ' Rev 1')
        self.assertEqual((rev1['revisionNo'], rev1['previousIssueId'], rev1['baseId']), (1, orig['id'], orig['baseId']))
        old = po_db.get_issue(orig['id'])
        self.assertFalse(old['current'])
        self.assertEqual([e['event'] for e in old['events']], ['issued', 'superseded'])
        self.assertEqual(po_db.read_issue_pdf(orig['id'])[1], orig_pdf, 'the original PDF is unchanged')
        snap = po_db.get_issue(rev1['id'])['snapshot']
        self.assertTrue(any(c['after'] == '3' for c in snap['changes']))
        self.assertIn('supersedes ' + orig['displayNo'], RENDERED[-1])
        with self.assertRaises(po_db.StaleRevision):                  # only the CURRENT issue can be revised
            po_db.revise_issue(orig['id'], 'po_P3REVdraft004')
        listed = [b for b in po_db.list_issues() if b['baseId'] == orig['baseId']][0]
        self.assertEqual([i['revisionNo'] for i in listed['issues']], [1, 0])
        self.assertEqual([i['current'] for i in listed['issues']], [True, False])


import po_storage_api   # noqa: E402

DAFTRA = {'mode': 'ok', 'calls': 0}


def fake_daftra(handler, daftra_id):
    """Stand-in for this server's /daftra/ route (no network, no credentials)."""
    DAFTRA['calls'] += 1
    mode = DAFTRA['mode']
    if mode == 'down':
        return 0, None
    if mode == 'missing':
        return 404, None
    return 200, {'id': daftra_id, 'business_name': 'Renamed Supplier' if mode == 'changed' else 'Fictional Supplier Est.', 'bn1': '300000000000003'}


class HttpBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import po_storage_api
        cls.srv, cls.port = po_test_support.start_server()
        cls._real = po_pdf.html_to_pdf
        po_pdf.html_to_pdf = fake_pdf
        cls._fetch = po_storage_api.fetch_daftra_supplier
        po_storage_api.fetch_daftra_supplier = fake_daftra          # no network: a stand-in Daftra reply

    @classmethod
    def tearDownClass(cls):
        import po_storage_api
        po_pdf.html_to_pdf = cls._real
        po_storage_api.fetch_daftra_supplier = cls._fetch
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None, raw=False):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        h = {'Host': f'127.0.0.1:{self.port}'}
        if body is not None:
            h['Content-Type'] = 'application/json'
            body = json.dumps(body).encode()
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        hdr = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return (r.status, data, hdr) if raw else (r.status, json.loads(data), hdr)



class Http(HttpBase):
    def test_settings_preview_issue_reprint_revise(self):
        s, j, _ = self.req('GET', '/api/po/settings')
        sdoc, srev = j['data']['data'], j['data']['rev']
        self.assertTrue(sdoc['testMode'])
        self.assertEqual(self.req('PUT', '/api/po/settings', sdoc)[0], 428)
        self.assertEqual(self.req('PUT', '/api/po/settings', sdoc, {'If-Match': str(srev + 3)})[0], 409)
        bad = dict(sdoc, buyer=dict(sdoc['buyer'], vat=''))
        self.assertEqual(self.req('PUT', '/api/po/settings', bad, {'If-Match': str(srev)})[0], 400, 'confirmed buyer needs a VAT number')
        s, j, _ = self.req('POST', '/api/po/drafts', draft('po_P3HTTPdraft01'))
        self.assertEqual(s, 201, j)
        rev = j['data']['rev']
        self.assertEqual(j['data']['readiness'], [])
        s, pdf, h = self.req('GET', '/api/po/drafts/po_P3HTTPdraft01/preview.pdf', raw=True)
        self.assertEqual((s, h['content-type']), (200, 'application/pdf'))
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertIn('>DRAFT<', RENDERED[-1])
        body = {'idempotencyKey': 'http-issue-key-00001', 'approvedBy': 'Test Approver', 'reason': '',
                'supplierCheck': {'status': 'verified', 'at': '2026-09-28T10:00:00Z', 'daftraId': '7'}}
        self.assertEqual(self.req('POST', '/api/po/drafts/po_P3HTTPdraft01/issue', body)[0], 428)
        s, j, _ = self.req('POST', '/api/po/drafts/po_P3HTTPdraft01/issue', dict(body, approvedBy='Nobody'), {'If-Match': str(rev)})
        self.assertEqual(s, 422)
        self.assertIn('approval_missing', [b['code'] for b in j['blockers']])
        s, j, _ = self.req('POST', '/api/po/drafts/po_P3HTTPdraft01/issue', body, {'If-Match': str(rev)})
        self.assertEqual(s, 201, j)
        iid = j['data']['id']
        s, j2, _ = self.req('POST', '/api/po/drafts/po_P3HTTPdraft01/issue', body, {'If-Match': str(rev)})
        self.assertEqual((s, j2['data']['id'], j2['duplicate']), (200, iid, True))
        snap = self.req('GET', f'/api/po/issues/{iid}')[1]['data']['snapshot']
        # the server's OWN verification is recorded; the page's claim in the request body is ignored
        self.assertEqual((snap['supplierCheck']['status'], snap['supplierCheck']['daftraId'], snap['supplierCheck']['draftRev']), ('verified', '7', rev))
        self.assertNotEqual(snap['supplierCheck']['at'], '2026-09-28T10:00:00Z')
        self.assertEqual(snap['computed']['totals']['vatMethod'], 'category')
        self.assertEqual(snap['templateVersion'], po_pdf.TEMPLATE_VERSION)
        s, j3, _ = self.req('PUT', '/api/po/drafts/po_P3HTTPdraft01', draft('po_P3HTTPdraft01', title='x'), {'If-Match': str(rev)})
        self.assertEqual((s, j3['code']), (409, 'issued'))
        s, stored, h = self.req('GET', f'/api/po/issues/{iid}/pdf', raw=True)
        self.assertEqual(hashlib.sha256(stored).hexdigest(), j['data']['pdfSha256'])
        n_before = len(RENDERED)
        self.assertEqual(self.req('GET', f'/api/po/issues/{iid}/pdf', raw=True)[1], stored, 'reprints are the stored bytes')
        self.assertEqual(len(RENDERED), n_before, 'a reprint never re-renders')
        s, j, _ = self.req('POST', f'/api/po/issues/{iid}/revise', {'draftId': 'po_P3HTTPrevise1'})
        self.assertEqual(s, 201, j)
        self.assertEqual(j['data']['data']['revision']['basedOnIssueId'], iid)
        self.assertEqual(self.req('POST', f'/api/po/issues/{iid}/revise', {'draftId': 'po_P3HTTPrevise2'})[0], 409)
        s, j, _ = self.req('GET', '/api/po/drafts/po_P3HTTPrevise1/changes')
        self.assertEqual((j['data']['changes'], j['data']['basedOnCurrent']), ([], True))
        forged = draft('po_P3HTTPforged1')
        forged['revision'] = j['data']['revision']
        self.assertEqual(self.req('POST', '/api/po/drafts', forged)[0], 400)
        s, j, _ = self.req('GET', '/api/po/issues')
        self.assertTrue(any(b['openRevisionDraftId'] == 'po_P3HTTPrevise1' for b in j['data']))


class SupplierVerifiedServerSide(HttpBase):
    """Issuing re-reads the supplier from Daftra ON THE SERVER; nothing the page sends can replace it."""

    def set_test_mode(self, on):
        doc, rev = po_db.get_settings()
        po_db.put_settings(po_model.validate_settings(dict(doc, testMode=on)), rev)

    def issue(self, did, key, rev, **extra):
        body = dict({'idempotencyKey': key, 'approvedBy': 'Test Approver', 'reason': ''}, **extra)
        return self.req('POST', f'/api/po/drafts/{did}/issue', body, {'If-Match': str(rev)})

    def test_real_issuance_requires_a_verified_supplier(self):
        self.set_test_mode(False)
        try:
            s, j, _ = self.req('POST', '/api/po/drafts', draft('po_P3SUPdraft001'))
            rev = j['data']['rev']
            counters = sqlite3.connect(po_db.db_path()).execute('SELECT COALESCE(SUM(next),0) FROM po_counters').fetchone()
            for mode in ('down', 'missing', 'changed'):
                DAFTRA['mode'] = mode
                forged = {'supplierCheck': {'status': 'verified', 'at': 'x', 'daftraId': '7'}}     # a page's claim is ignored
                s, j, _ = self.issue('po_P3SUPdraft001', f'sup-key-{mode}-0000001', rev, **forged)
                self.assertEqual(s, 422, mode)
                self.assertIn('supplier_unverified', [b['code'] for b in j['blockers']], mode)
            self.assertIsNone(po_db.issue_for_draft('po_P3SUPdraft001'), 'nothing issued')
            self.assertEqual(po_db.get_draft('po_P3SUPdraft001')[1], rev, 'the draft is unchanged')
            self.assertEqual(sqlite3.connect(po_db.db_path()).execute('SELECT COALESCE(SUM(next),0) FROM po_counters').fetchone(), counters,
                             'no number used')
            DAFTRA['mode'] = 'ok'
            s, j, _ = self.issue('po_P3SUPdraft001', 'sup-key-ok-000000001', rev)
            self.assertEqual(s, 201, j)
            snap = self.req('GET', f'/api/po/issues/{j["data"]["id"]}')[1]['data']['snapshot']
            self.assertEqual((snap['supplierCheck']['status'], snap['supplierCheck']['draftRev']), ('verified', rev))
            calls = DAFTRA['calls']
            DAFTRA['mode'] = 'down'                        # a retry of the SAME request returns the issue, no second check
            s, j2, _ = self.issue('po_P3SUPdraft001', 'sup-key-ok-000000001', rev)
            self.assertEqual((s, j2['duplicate'], DAFTRA['calls']), (200, True, calls))
        finally:
            DAFTRA['mode'] = 'ok'
            self.set_test_mode(True)

    def test_storage_layer_refuses_without_a_matching_verification(self):
        doc, rev = po_db.create_draft(draft('po_P3SUPdraft002'))
        for bad in (None, dict(ok(rev), status='unavailable'), dict(ok(rev), draftRev=rev + 1), ok(rev, daftra_id='8')):
            self.set_test_mode(False)
            try:
                with self.assertRaises(po_db.NotReady):
                    po_db.issue_draft(doc['id'], rev, 'sup-key-store-00000' + str(id(bad))[-3:], '', 'Test Approver', 'tester', render, bad)
            finally:
                self.set_test_mode(True)

    def test_test_mode_records_a_missing_supplier_instead_of_blocking(self):
        DAFTRA['mode'] = 'missing'
        try:
            s, j, _ = self.req('POST', '/api/po/drafts', draft('po_P3SUPdraft003'))
            s, j, _ = self.issue('po_P3SUPdraft003', 'sup-key-test-0000001', j['data']['rev'])
            self.assertEqual(s, 201, j)
            snap = self.req('GET', f'/api/po/issues/{j["data"]["id"]}')[1]['data']['snapshot']
            self.assertEqual(snap['supplierCheck']['status'], 'not_found')
        finally:
            DAFTRA['mode'] = 'ok'


class DefaultPoNotes(unittest.TestCase):
    def test_standard_notes_cover_the_required_clauses(self):
        n = po_model.DEFAULT_PO_NOTES.lower()
        for words in (('conform',), ('inspection', 'written acceptance'), ('defective', 'corrected or replaced', 'no additional cost'),
                      ('scope', 'price', "prior written approval")):
            for w in words:
                self.assertIn(w, n)
        self.assertLessEqual(len(po_model.DEFAULT_PO_NOTES), po_model.LIMITS['poNotes'])
        self.assertEqual(po_model.validate_draft(draft(poNotes=po_model.DEFAULT_PO_NOTES))['poNotes'], po_model.DEFAULT_PO_NOTES)


@unittest.skipUnless(po_pdf.find_browser(), 'no local Edge/Chrome to print PDFs')
class PdfRealRender(unittest.TestCase):
    def test_local_browser_prints_a_pdf(self):
        d = draft()
        data = po_pdf.render(d, po_model.compute(d), FICTIONAL, None, {})
        self.assertTrue(data.startswith(b'%PDF-'))
        self.assertGreater(len(data), 2000)


if __name__ == '__main__':
    unittest.main()
