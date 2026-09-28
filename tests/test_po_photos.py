"""
PO Generator — item photo tests (photo storage, photo API, draft photo
associations). Synthetic data only.

Run from the repo root:
    python -m unittest tests.test_po_photos -v

Storage: a temporary directory is set in setUpModule (and the previous value
restored in tearDownModule) — the production PO store is never used.
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
from tests import po_test_support   # noqa: E402

_TMP = None
_PREV = None
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40
JPG = b'\xff\xd8\xff\xe0' + b'\x00' * 40
REG = {'x': 134.2, 'y': 201.37, 'w': 38.0, 'h': 25.04}


def setUpModule():
    global _TMP, _PREV
    _PREV = os.environ.get('VISTA_PO_DATA_DIR')
    _TMP = tempfile.mkdtemp(prefix='vista-po-photo-test-')
    os.environ['VISTA_PO_DATA_DIR'] = _TMP


def tearDownModule():
    if _PREV is None:
        os.environ.pop('VISTA_PO_DATA_DIR', None)
    else:
        os.environ['VISTA_PO_DATA_DIR'] = _PREV
    shutil.rmtree(_TMP, ignore_errors=True)


def draft(**over):
    d = {'id': 'po_PHTESTdraft01', 'schema': 1, 'status': 'draft', 'title': 'T', 'currency': 'SAR',
         'priceTaxBasis': 'exclusive', 'supplier': None, 'items': [],
         'paymentTerms': {'text': 't', 'isDraftDefault': True, 'balanceTrigger': 'undecided',
                          'milestones': [{'id': 'ms_advance', 'pct': '50', 'label': 'a'},
                                         {'id': 'ms_balance', 'pct': '50', 'label': 'b'}]},
         'projectRef': '', 'deliveryLocation': '', 'notes': '', 'createdAt': '', 'updatedAt': '', 'quotation': None}
    d.update(over)
    return d


SRC = 'src_' + 'a' * 24


def item(iid='it_photo00001'):
    return {'id': iid, 'description': 'Sign', 'unit': 'pcs', 'qty': '1', 'unitPrice': '10', 'included': True,
            'tax': {'treatment': 'taxable', 'rate': '15', 'origin': 'user'}, 'source': 'manual', 'createdAt': '', 'excludedAt': ''}


def assoc(**over):
    a = {'id': 'pa_ABCDEFGH', 'photoId': 'ph_' + '1' * 24, 'sourceId': SRC, 'page': 1, 'region': dict(REG), 'kind': 'embedded',
         'target': {'kind': 'item', 'itemId': 'it_photo00001'}, 'status': 'suggested', 'origin': 'auto', 'includeInPdf': False,
         'reasons': ['inside_item_block'], 'suggestion': {'extractionId': 'ex_' + 'b' * 24, 'rowIds': ['r1'], 'confidence': 'high'},
         'updatedAt': ''}
    a.update(over)
    return a


class PhotoStorage(unittest.TestCase):
    def setUp(self):
        self.src, _ = po_db.save_source('po_PHSTORE0001', 'q.pdf', 'application/pdf', PDF + self.id().encode())

    def test_stable_id_first_image_kept_original_untouched(self):
        meta, existing = po_db.save_photo(self.src['id'], 1, REG, 'embedded', 'image/png', PNG)
        self.assertFalse(existing)
        self.assertEqual(meta['id'], po_db.photo_id(self.src['id'], 1, po_db.check_region(1, REG), 'embedded'))
        self.assertEqual(meta['region'], {'x': 134.2, 'y': 201.4, 'w': 38.0, 'h': 25.0})
        # the same region with the SAME bytes (a re-run) → the stored record
        again, existing2 = po_db.save_photo(self.src['id'], 1, dict(REG), 'embedded', 'image/png', PNG)
        self.assertTrue(existing2)
        self.assertEqual(again, meta)
        self.assertEqual(po_db.read_photo_bytes(meta['id'])[1], PNG)
        # a user crop of the same region is a separate photo
        crop, _ = po_db.save_photo(self.src['id'], 1, REG, 'source_crop', 'image/jpeg', JPG)
        self.assertNotEqual(crop['id'], meta['id'])
        self.assertEqual({p['id'] for p in po_db.list_photos(self.src['id'])}, {meta['id'], crop['id']})
        # the original document is unchanged
        self.assertEqual(po_db.read_source_bytes(self.src['id'])[1], PDF + self.id().encode())

    def test_different_bytes_under_an_existing_photo_id_are_rejected(self):
        meta, _ = po_db.save_photo(self.src['id'], 3, REG, 'embedded', 'image/png', PNG + b'original')
        path = os.path.join(po_db.photos_dir(), meta['sha256'] + '.png')
        files_before = sorted(os.listdir(po_db.photos_dir()))
        for other in ((3, dict(REG), 'embedded', 'image/png', PNG + b'higher-resolution render'),
                      (3, dict(REG), 'embedded', 'image/jpeg', JPG + b'same region as jpeg')):
            with self.assertRaises(po_db.PhotoConflict) as cm:
                po_db.save_photo(self.src['id'], *other)
            self.assertEqual(cm.exception.existing, meta)               # the refusal names the kept photo
        # the original record, bytes, hash and file are unchanged; no new file was written
        self.assertEqual(po_db.get_photo(meta['id']), meta)
        got_meta, data = po_db.read_photo_bytes(meta['id'])
        self.assertEqual((got_meta, data), (meta, PNG + b'original'))
        self.assertEqual(hashlib.sha256(data).hexdigest(), meta['sha256'])
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), PNG + b'original')
        self.assertEqual(sorted(os.listdir(po_db.photos_dir())), files_before)
        self.assertEqual([p['id'] for p in po_db.list_photos(self.src['id'])].count(meta['id']), 1)
        # a better rendering is a NEW photo: another region/kind gets its own id and record
        new, existing = po_db.save_photo(self.src['id'], 3, dict(REG), 'source_crop', 'image/png', PNG + b'higher-resolution render')
        self.assertFalse(existing)
        self.assertNotEqual(new['id'], meta['id'])
        self.assertEqual(po_db.read_photo_bytes(meta['id'])[1], PNG + b'original')

    def test_rejections(self):
        for args in ((1, REG, 'logo', 'image/png', PNG), (1, REG, 'embedded', 'image/gif', PNG), (1, REG, 'embedded', 'image/png', JPG),
                     (1, REG, 'embedded', 'image/png', b''), (0, REG, 'embedded', 'image/png', PNG),
                     (1, {'x': 1, 'y': 1, 'w': 1, 'h': 1}, 'embedded', 'image/png', PNG), (1, {'x': 1}, 'embedded', 'image/png', PNG),
                     (1, dict(REG, w=float('nan')), 'embedded', 'image/png', PNG),
                     (1, REG, 'embedded', 'image/png', PNG + b'0' * po_db.PHOTO_MAX_BYTES)):
            with self.assertRaises(po_db.SourceInvalid, msg=repr(args[:3])):
                po_db.save_photo(self.src['id'], *args)
        with self.assertRaises(po_db.SourceInvalid):
            po_db.save_photo('src_' + 'f' * 24, 1, REG, 'embedded', 'image/png', PNG)     # unknown source
        self.assertEqual(po_db.list_photos(self.src['id']), [])

    def test_immutable_and_tamper_detected(self):
        meta, _ = po_db.save_photo(self.src['id'], 2, REG, 'embedded', 'image/png', PNG + b'tamper-me')
        c = sqlite3.connect(po_db.db_path())
        try:
            for sql in ("UPDATE po_photos SET page=9 WHERE id=?", "DELETE FROM po_photos WHERE id=?"):
                with self.assertRaises(sqlite3.DatabaseError) as cm:
                    c.execute(sql, (meta['id'],))
                self.assertIn('immutable', str(cm.exception))
        finally:
            c.close()
        path = os.path.join(po_db.photos_dir(), meta['sha256'] + '.png')
        os.chmod(path, 0o666)
        with open(path, 'ab') as f:
            f.write(b'!')
        with self.assertRaises(po_db.SourceIntegrityError):
            po_db.read_photo_bytes(meta['id'])
        self.assertIsNone(po_db.read_photo_bytes('ph_../../x'))


class PhotoModel(unittest.TestCase):
    def ok(self, photos, items=None):
        return po_model.validate_draft(draft(items=items if items is not None else [item()], photos=photos))

    def errs(self, photos, items=None):
        with self.assertRaises(po_model.DraftInvalid) as cm:
            self.ok(photos, items)
        return {e['path'] for e in cm.exception.errors}

    def test_valid_associations_round_trip(self):
        group = assoc(id='pa_GROUP0001', photoId='ph_' + '2' * 24, target={'kind': 'group', 'sourceId': SRC, 'parent': '11 WALL GRAPHICS'})
        crop = assoc(id='pa_CROP00001', photoId='ph_' + '3' * 24, kind='source_crop', origin='user', status='confirmed',
                     includeInPdf=True, reasons=[], suggestion=None)
        removed = assoc(id='pa_REMOVED01', photoId='ph_' + '4' * 24, status='removed', target=None)
        loose = assoc(id='pa_LOOSE0001', photoId='ph_' + '5' * 24, status='uncertain', target=None, reasons=['continues_from_previous_page'])
        d = self.ok([assoc(), group, crop, removed, loose])
        self.assertEqual(len(d['photos']), 5)
        self.assertEqual(d['photos'][1]['target'], {'kind': 'group', 'sourceId': SRC, 'parent': '11 WALL GRAPHICS'})
        self.assertEqual(d['photos'][0]['region'], {'x': 134.2, 'y': 201.4, 'w': 38.0, 'h': 25.0})
        self.assertEqual(po_model.validate_draft(d), d)                   # stable across saves
        self.assertEqual(po_model.validate_draft(draft())['photos'], [])  # older drafts: no photos

    def test_rejections(self):
        self.assertIn('photos[0].target.itemId', self.errs([assoc(target={'kind': 'item', 'itemId': 'it_notindraft'})]))
        self.assertIn('photos[1].photoId', self.errs([assoc(), assoc(id='pa_OTHER0001')]))            # one photo, one owner
        self.assertIn('photos[1].id', self.errs([assoc(), assoc(photoId='ph_' + '9' * 24)]))           # duplicate association id
        self.assertIn('photos[0].target', self.errs([assoc(target=None)]))                             # suggested needs an item
        self.assertIn('photos[0].target', self.errs([assoc(target={'kind': 'group', 'sourceId': 'src_' + 'c' * 24, 'parent': 'x'})]))
        self.assertIn('photos[0].status', self.errs([assoc(status='maybe')]))
        self.assertIn('photos[0].kind', self.errs([assoc(kind='logo')]))
        self.assertIn('photos[0].region', self.errs([assoc(region={'x': 1, 'y': 2})]))
        self.assertIn('photos[0].reasons', self.errs([assoc(reasons=['<script>'])]))
        self.assertIn('photos[0].path', self.errs([assoc(path='/etc/passwd')]))
        self.assertIn('photos[0].includeInPdf', self.errs([assoc(includeInPdf='yes')]))

    def test_uncertain_or_unassigned_pdf_photos_block_issue(self):
        codes = lambda d: {b['code'] for b in po_model.readiness(d, po_model.compute(d))}
        self.assertIn('photos_unconfirmed', codes(self.ok([assoc(status='uncertain')])))
        self.assertNotIn('photos_unconfirmed', codes(self.ok([assoc(status='confirmed', includeInPdf=True)])))
        self.assertNotIn('photos_unconfirmed', codes(self.ok([assoc(status='removed')])))


class PhotoHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # PO API over HTTP without importing proxy.py (no config.json / tokens needed)
        cls.srv, cls.port = po_test_support.start_server()
        s, j, _ = cls.req(cls, 'POST', '/api/po/drafts', json.dumps(draft(id='po_PHHTTPdraft1')).encode(), 'application/json')
        assert s == 201, j
        s, j, _ = cls.req(cls, 'POST', '/api/po/sources?draftId=po_PHHTTPdraft1&name=q.pdf', PDF + b'% http', 'application/pdf')
        assert s == 201, j
        cls.sid = j['data']['id']

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

    def post(self, data, ctype='image/png', **q):
        params = dict({'sourceId': self.sid, 'page': '1', 'x': '10', 'y': '20', 'w': '30', 'h': '40', 'kind': 'embedded'}, **q)
        return self.req('POST', '/api/po/photos?' + '&'.join(f'{k}={v}' for k, v in params.items()), data, ctype)

    def test_upload_list_serve(self):
        s, j, _ = self.post(PNG + b'http-1')
        self.assertEqual(s, 201, j)
        pid = j['data']['id']
        s2, j2, _ = self.post(PNG + b'http-1')                          # same bytes → the stored record
        self.assertEqual((s2, j2['data']['id'], j2['existing']), (200, pid, True))
        s, lst, _ = self.req('GET', '/api/po/photos?sourceId=' + self.sid)
        self.assertIn(pid, [p['id'] for p in lst['data']])
        s, data, h = self.req('GET', f'/api/po/photos/{pid}/file', raw=True)
        self.assertEqual((s, data), (200, PNG + b'http-1'))
        self.assertEqual(h['content-type'], 'image/png')
        self.assertEqual(h['x-content-type-options'], 'nosniff')
        self.assertIn('sandbox', h['content-security-policy'])
        self.assertNotIn('content-disposition', h)
        self.assertNotIn(_TMP, json.dumps(lst))                         # no storage paths leak

    def test_conflicting_bytes_return_409_and_keep_the_original(self):
        s, j, _ = self.post(PNG + b'http-original', y='300')
        self.assertEqual(s, 201, j)
        meta = j['data']
        s, j, _ = self.post(PNG + b'http-different', y='300')
        self.assertEqual(s, 409, j)
        self.assertFalse(j['ok'])
        self.assertEqual(j['code'], 'photo_conflict')
        self.assertEqual(j['existing'], meta)                            # the kept photo, unchanged
        self.assertNotIn('data', j)                                      # not presented as an upload result
        self.assertIn('kept unchanged', j['error'])
        s, data, h = self.req('GET', f"/api/po/photos/{meta['id']}/file", raw=True)
        self.assertEqual((s, data), (200, PNG + b'http-original'))
        self.assertEqual(hashlib.sha256(data).hexdigest(), meta['sha256'])
        s, lst, _ = self.req('GET', '/api/po/photos?sourceId=' + self.sid)
        self.assertEqual([p for p in lst['data'] if p['id'] == meta['id']], [meta])
        # associations that point at the photo are untouched by the refused upload
        d = draft(id='po_PHHTTPdraft3', items=[item()], photos=[assoc(photoId=meta['id'], sourceId=self.sid, region=meta['region'], suggestion=None)])
        s, j, _ = self.req('POST', '/api/po/drafts', json.dumps(d).encode(), 'application/json')
        self.assertEqual(s, 201, j)
        self.assertEqual(self.post(PNG + b'yet another', y='300')[0], 409)
        s, j, _ = self.req('GET', '/api/po/drafts/po_PHHTTPdraft3')
        self.assertEqual(j['data']['data']['photos'][0]['photoId'], meta['id'])
        self.assertEqual(j['data']['rev'], 1)

    def test_rejections(self):
        self.assertEqual(self.post(PNG, ctype='application/pdf')[0], 415)
        self.assertEqual(self.post(PNG, sourceId='src_' + 'e' * 24)[0], 404)
        self.assertEqual(self.post(PNG, page='x')[0], 400)
        self.assertEqual(self.post(PNG, w='abc')[0], 400)
        self.assertEqual(self.post(JPG, ctype='image/png', y='77')[0], 400)
        self.assertEqual(self.post(PNG, kind='logo', y='78')[0], 400)
        self.assertEqual(self.req('GET', '/api/po/photos')[0], 400)
        self.assertEqual(self.req('GET', '/api/po/photos/ph_' + '0' * 24 + '/file')[0], 404)
        s, _, _ = self.req('POST', '/api/po/photos?sourceId=' + self.sid + '&page=1&x=1&y=1&w=9&h=9&kind=embedded', PNG, 'image/png',
                           headers={'Origin': 'http://evil.example'})
        self.assertEqual(s, 403)
        s, _, _ = self.req('GET', '/api/po/photos?sourceId=' + self.sid, headers={'X-Forwarded-For': '1.2.3.4'})
        self.assertEqual(s, 403)

    def test_draft_with_photo_associations_saves_with_rev_check(self):
        s, j, _ = self.post(PNG + b'assoc', y='90')
        pid = j['data']['id']
        d = draft(id='po_PHHTTPdraft2', items=[item()],
                  photos=[assoc(photoId=pid, sourceId=self.sid, region=j['data']['region'], suggestion=None)])
        s, j, _ = self.req('POST', '/api/po/drafts', json.dumps(d).encode(), 'application/json')
        self.assertEqual(s, 201, j)
        rev = j['data']['rev']
        d['photos'][0]['status'] = 'confirmed'
        s, j, _ = self.req('PUT', '/api/po/drafts/po_PHHTTPdraft2', json.dumps(d).encode(), 'application/json', {'If-Match': str(rev)})
        self.assertEqual(s, 200, j)
        s, j, _ = self.req('PUT', '/api/po/drafts/po_PHHTTPdraft2', json.dumps(d).encode(), 'application/json', {'If-Match': str(rev)})
        self.assertEqual(s, 409)                                         # stale tab cannot overwrite photo decisions
        s, j, _ = self.req('GET', '/api/po/drafts/po_PHHTTPdraft2')
        self.assertEqual(j['data']['data']['photos'][0]['status'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
