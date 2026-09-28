/* PO Generator Phase 2 — extraction engine tests (synthetic fixtures only).
   Run:  node tests/test_po_extract.js
   Pure data in → data out: no network, no storage, no real supplier files. */
'use strict';
const assert = require('assert');
const path = require('path');
const X = require(path.join(__dirname, '..', 'po-extract.js'));

let passed = 0, failed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log('ok   ' + name); }
  catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + (e && e.message)); }
}
// rows: [y, [[x, text, conf?], ...], h?]
function page(no, rows, o) {
  o = o || {};
  const items = [];
  for (const [y, cells, h] of rows) for (const [x, str, conf] of cells) items.push({ str, x, y, w: str.length * 5, h: h || 10, conf });
  return { page: no, width: 595, height: 842, method: o.method || 'text', items };
}
const EN_HEADER = [[40, '#'], [70, 'Description'], [300, 'Unit'], [350, 'Qty'], [400, 'Unit Price'], [480, 'Total']];
const row = (ref, desc, unit, qty, price, total) => [[40, ref], [70, desc], [300, unit], [350, qty], [400, price], [480, total]].filter(c => c[1] !== '');
const val = f => f && f.value;
const kinds = res => res.otherRows.map(o => o.kind);

// ── 1. simple English quotation ────────────────────────────────────────────
const simple = X.extract({ pages: [page(1, [
  [40, [[40, 'ACME Signs Trading Co.']], 18],
  [62, [[40, 'VAT No: 300000000000003']]],
  [76, [[40, 'Tel: +966 12 345 6789']]],
  [100, [[40, 'QUOTATION']]],
  [115, [[40, 'Quotation No: Q-2026-118'], [330, 'Date: 20/09/2026']]],
  [130, [[40, 'To: Vista United Co.']]],
  [145, [[40, 'Jeddah, KSA']]],
  [190, EN_HEADER],
  [210, row('1', 'Acrylic sign 120x60 cm', 'pcs', '10', '120.00', '1,200.00')],
  [225, [[70, '10mm clear acrylic, UV print']]],
  [245, row('2', 'Aluminium frame', 'pcs', '10', '45.50', '455.00')],
  [265, row('3', 'Installation', 'LS', '1', '300.00', '300.00')],
  [300, [[330, 'Subtotal'], [480, '1,955.00']]],
  [315, [[330, 'VAT 15%'], [480, '293.25']]],
  [330, [[330, 'Grand Total'], [480, '2,248.25']]],
  [380, [[40, 'Payment Terms: 50% advance, 50% on delivery']]],
  [395, [[40, 'Delivery: 3 weeks from PO']]],
  [410, [[40, 'Validity: 30 days']]],
  [425, [[40, 'All prices are in SAR and exclusive of VAT.']]],
])] });

test('simple: header fields', () => {
  const h = simple.header;
  assert.strictEqual(val(h.quotationRef), 'Q-2026-118');
  assert.strictEqual(h.quotationDate.value.iso, '2026-09-20');
  assert.strictEqual(h.quotationDate.value.ambiguous, false);
  assert.strictEqual(h.quotationDate.value.raw, '20/09/2026');
  assert.strictEqual(val(h.currency), 'SAR');
  assert.strictEqual(val(h.supplierVat), '300000000000003');
  assert.strictEqual(val(h.supplierName), 'ACME Signs Trading Co.');
  assert.ok(h.supplierName.flags.includes('heuristic_supplier_name'), 'heuristic name is flagged');
  assert.strictEqual(val(h.paymentTerms), '50% advance, 50% on delivery');
  assert.strictEqual(val(h.delivery), '3 weeks from PO');
  assert.strictEqual(val(h.validity), '30 days');
  assert.strictEqual(val(h.taxBasis), 'exclusive');
  assert.strictEqual(val(h.taxRate), '15');
});
test('simple: items with multiline description and evidence', () => {
  assert.strictEqual(simple.rows.length, 3);
  const [a, b, c] = simple.rows;
  assert.strictEqual(a.fields.description.value, 'Acrylic sign 120x60 cm\n10mm clear acrylic, UV print');
  assert.ok(a.fields.description.flags.includes('multiline'));
  assert.deepStrictEqual([val(a.fields.qty), val(a.fields.unitPrice), val(a.fields.lineTotal), val(a.fields.unit)], ['10', '120.00', '1200.00', 'pcs']);
  assert.strictEqual(a.fields.lineTotal.raw, '1,200.00');
  assert.strictEqual(a.page, 1);
  assert.ok(a.fields.qty.region && a.fields.qty.region.x === 350, 'qty region kept');
  assert.deepStrictEqual([val(b.fields.unitPrice), val(b.fields.lineTotal)], ['45.50', '455.00']);
  assert.strictEqual(val(c.fields.unit), 'LS');
  assert.ok(simple.rows.every(r => r.kind === 'item' && r.selectedByDefault && !r.flags.includes('line_total_mismatch')));
});
test('simple: totals and cross-checks (reported, not forced)', () => {
  const t = simple.totals;
  assert.deepStrictEqual([val(t.subtotal), val(t.vat), val(t.grandTotal)], ['1955.00', '293.25', '2248.25']);
  assert.ok(t.checks.length === 2 && t.checks.every(c => c.ok), JSON.stringify(t.checks));
  assert.deepStrictEqual(kinds(simple), ['subtotal', 'vat', 'grand_total']);
});
test('simple: buyer block is not used as supplier identity', () => {
  assert.ok(!/vista/i.test(val(simple.header.supplierName)));
});

// ── 2. multipage: repeated header, carried forward, footer, title-first rows
const multi = X.extract({ pages: [
  page(1, [
    [40, [[40, 'ACME Signs Trading Co.']], 18],
    [100, EN_HEADER],
    [120, row('1', 'Wall graphic vinyl', 'm2', '25', '80.00', '2,000.00')],
    [140, row('2', 'Lightbox fabric print', 'pcs', '4', '350.00', '1,400.00')],
    [160, [[40, '3'], [70, 'Directional totem, double-sided']]],
    [175, [[70, 'powder-coated steel, 2.2 m high']]],
    [190, [[300, 'pcs'], [350, '2'], [400, '1,800.00'], [480, '3,600.00']]],
    [300, [[330, 'Carried forward'], [480, '7,000.00']]],
    [800, [[270, 'Page 1 of 2']]],
  ]),
  page(2, [
    [40, [[40, 'ACME Signs Trading Co.']], 18],
    [60, [[40, 'Tel: 012 345 6789']]],
    [100, EN_HEADER],
    [115, [[330, 'Brought forward'], [480, '7,000.00']]],
    [130, row('4', 'Door vinyl lettering', 'set', '1', '650.00', '650.00')],
    [160, [[330, 'Subtotal'], [480, '7,650.00']]],
    [800, [[270, 'Page 2 of 2']]],
  ]),
] });
test('multipage: rows across pages, repeated header recorded, footer/letterhead not attached', () => {
  assert.strictEqual(multi.rows.length, 4, JSON.stringify(multi.rows.map(r => r.fields.description.value)));
  const totem = multi.rows[2];
  assert.strictEqual(totem.fields.description.value, 'Directional totem, double-sided\npowder-coated steel, 2.2 m high');
  assert.deepStrictEqual([val(totem.fields.qty), val(totem.fields.unitPrice), val(totem.fields.lineTotal)], ['2', '1800.00', '3600.00']);
  assert.strictEqual(multi.rows[3].page, 2);
  const allDesc = multi.rows.map(r => r.fields.description.value).join(' ');
  assert.ok(!/ACME|Page \d/.test(allDesc), 'letterhead/footer leaked: ' + allDesc);
  const k = kinds(multi);
  assert.ok(k.includes('repeated_header') && k.filter(x => x === 'carried_forward').length === 2 && k.includes('subtotal'), k.join(','));
  assert.strictEqual(multi.totals.carriedForward.length, 2);
  assert.strictEqual(val(multi.totals.subtotal), '7650.00');
});

// ── 3. table continues on the next page without a repeated header ──────────
const noRepeat = X.extract({ pages: [
  page(1, [[100, EN_HEADER], [120, row('1', 'Banner', 'pcs', '2', '100.00', '200.00')]]),
  page(2, [
    [40, [[70, 'ACME Signs Trading Co. letterhead']], 18],
    [120, row('2', 'Flag', 'pcs', '3', '50.00', '150.00')],
  ]),
] });
test('no repeated header: letterhead not attached, warning raised', () => {
  assert.strictEqual(noRepeat.rows.length, 2);
  assert.ok(!/letterhead/.test(noRepeat.rows.map(r => r.fields.description.value).join(' ')));
  assert.ok(noRepeat.warnings.some(w => w.code === 'table_continues_without_header' && w.page === 2));
});

// ── 4. optional / alternative / duplicates / lump sum / mismatch / ambiguous
const tricky = X.extract({ pages: [page(1, [
  [100, EN_HEADER],
  [120, row('1', 'Acrylic letters 20 cm', 'pcs', '10', '40.00', '400.00')],
  [135, row('2', 'Acrylic letters 20 cm', 'pcs', '10', '40.00', '400.00')],
  [150, row('3', 'Site survey', '', '', '', '500.00')],
  [165, row('4', 'Foam board', 'pcs', '2', '100.00', '250.00')],
  [180, row('5', 'Vinyl roll', 'roll', '3', '1.500', '4,500.00')],
  [200, [[70, 'Optional items']]],
  [215, row('6', 'Night lighting kit', 'set', '1', '900.00', '900.00')],
  [230, row('7', 'Alternative: stainless steel frame', 'pcs', '1', '1,200.00', '1,200.00')],
])] });
test('tricky: identical values under a different printed number stay a separate selected row (flagged)', () => {
  const dup = tricky.rows.find(r => r.fields.ref.value === '2');
  assert.ok(dup && dup.flags.includes('same_values_as_other_row') && dup.sameValuesAs === tricky.rows[0].id);
  assert.ok(!dup.flags.includes('possible_duplicate'));
  assert.strictEqual(dup.selectedByDefault, true);
  assert.strictEqual(tricky.rows[0].selectedByDefault, true);
});
test('tricky: lump sum keeps qty/price empty (not inferred)', () => {
  const ls = tricky.rows.find(r => r.fields.ref.value === '3');
  assert.strictEqual(val(ls.fields.qty), null);
  assert.strictEqual(val(ls.fields.unitPrice), null);
  assert.strictEqual(val(ls.fields.lineTotal), '500.00');
  assert.ok(ls.flags.includes('lump_sum_or_missing_breakdown'));
});
test('tricky: line-total mismatch flagged, values unchanged', () => {
  const m = tricky.rows.find(r => r.fields.ref.value === '4');
  assert.ok(m.flags.includes('line_total_mismatch'));
  assert.strictEqual(val(m.fields.lineTotal), '250.00');
});
test('tricky: ambiguous number is not guessed', () => {
  const a = tricky.rows.find(r => r.fields.ref.value === '5');
  assert.strictEqual(val(a.fields.unitPrice), null);
  assert.strictEqual(a.fields.unitPrice.raw, '1.500');
  assert.ok(a.flags.includes('ambiguous_number'));
});
test('tricky: optional / alternative rows marked and not selected', () => {
  const o = tricky.rows.find(r => r.fields.ref.value === '6');
  const alt = tricky.rows.find(r => r.fields.ref.value === '7');
  assert.strictEqual(o.kind, 'optional'); assert.strictEqual(o.selectedByDefault, false);
  assert.strictEqual(alt.kind, 'alternative'); assert.strictEqual(alt.selectedByDefault, false);
  assert.ok(kinds(tricky).includes('section_heading'));
});

// ── 5. Arabic RTL quotation with Arabic-Indic digits ───────────────────────
const AR_HEADER = [[520, 'رقم'], [350, 'الوصف'], [250, 'الوحدة'], [190, 'الكمية'], [110, 'سعر الوحدة'], [30, 'الإجمالي']];
const arabic = X.extract({ pages: [page(1, [
  [40, [[300, 'المورد: مؤسسة الإعلان المتقدم']]],
  [60, [[300, 'الرقم الضريبي: ٣٠٠١٢٣٤٥٦٧٨٩٠٠٣']]],
  [80, [[300, 'رقم العرض: ق-٢٠٢٦-٠٧']]],
  [95, [[200, 'التاريخ: ٠٣/٠٤/٢٠٢٦م الموافق ١٥/٠٣/١٤٤٨هـ']]],
  [110, [[300, 'السادة: شركة فيستا المتحدة']]],
  [150, AR_HEADER],
  [170, [[520, '١'], [300, 'لوحة أكريليك مقاس ١٢٠×٦٠'], [250, 'حبة'], [195, '١٠'], [115, '١٬٢٠٠٫٥٠'], [30, '١٢٬٠٠٥٫٠٠']]],
  [230, [[250, 'الإجمالي شامل الضريبة'], [30, '١٢٬٠٠٥٫٠٠']]],
  [270, [[200, 'الأسعار شاملة ضريبة القيمة المضافة ١٥٪ والمبالغ بالريال السعودي']]],
  [285, [[250, 'شروط الدفع: ٥٠٪ مقدم و٥٠٪ عند التسليم']]],
  [300, [[250, 'مدة التوريد: ٢١ يوم عمل']]],
])] });
test('arabic: RTL columns, Arabic-Indic numbers', () => {
  assert.strictEqual(arabic.rows.length, 1, JSON.stringify(arabic.rows.map(r => r.rawText)));
  const r = arabic.rows[0];
  assert.strictEqual(val(r.fields.description), 'لوحة أكريليك مقاس ١٢٠×٦٠');
  assert.deepStrictEqual([val(r.fields.qty), val(r.fields.unitPrice), val(r.fields.lineTotal), val(r.fields.unit)], ['10', '1200.50', '12005.00', 'حبة']);
  assert.ok(!r.flags.includes('line_total_mismatch'));
});
test('arabic: header fields, ambiguous Gregorian date + Hijri kept unconverted', () => {
  const h = arabic.header;
  assert.strictEqual(val(h.supplierName), 'مؤسسة الإعلان المتقدم');
  assert.strictEqual(val(h.supplierVat), '300123456789003');
  assert.strictEqual(val(h.quotationRef), 'ق-2026-07');
  const d = h.quotationDate.value;
  assert.strictEqual(d.ambiguous, true);
  assert.strictEqual(d.iso, null);
  assert.strictEqual(d.calendar, 'gregorian');
  assert.ok(h.quotationDate.flags.includes('ambiguous_date'));
  assert.strictEqual(d.otherCalendars[0].calendar, 'hijri');
  assert.strictEqual(d.otherCalendars[0].hijri, '1448-03-15');
  assert.strictEqual(val(h.taxBasis), 'inclusive');
  assert.strictEqual(val(h.taxRate), '15');
  assert.strictEqual(val(h.currency), 'SAR');
  assert.ok(/50%/.test(val(h.paymentTerms)));
  assert.ok(/21/.test(val(h.delivery)));
  assert.strictEqual(val(arabic.totals.grandTotal), '12005.00');
});

// glyph-per-item Arabic (as produced by some PDF generators): each letter its own
// item, laid out right-to-left with no gap; words separated by a visible gap.
// Letters are laid out right-to-left; digit runs (with their separators) are
// laid out left-to-right as separate glyph items — the realistic worst case.
function glyphs(text, xRight, y, cw) {
  cw = cw || 6;
  const out = [];
  let x = xRight;
  for (const word of text.split(' ')) {
    for (const seg of word.match(/[\d٠-٩٫٬.,\/%٪\-]+|[^\d٠-٩٫٬.,\/%٪\-]+/g)) {
      const chars = [...seg];
      if (/^[\d٠-٩٫٬.,\/%٪\-]+$/.test(seg)) {
        x -= chars.length * cw;
        chars.forEach((ch, i) => out.push({ str: ch, x: x + i * cw, y, w: cw, h: 10 }));
      } else for (const ch of chars) { x -= cw; out.push({ str: ch, x, y, w: cw, h: 10 }); }
    }
    x -= 4;                                          // word gap
  }
  return out;
}
test('arabic glyph-per-item text is joined into words in reading order', () => {
  const items = [].concat(glyphs('رقم العرض: ق-٢٠٢٦-٠٧', 560, 40), glyphs('شروط الدفع: ٥٠٪ مقدم', 560, 60), glyphs('الأسعار شاملة الضريبة ١٥٪', 560, 80));
  const r = X.extract({ pages: [{ page: 1, width: 595, height: 842, method: 'text', items }] });
  assert.strictEqual(r.header.quotationRef.value, 'ق-2026-07');
  assert.strictEqual(r.header.paymentTerms.value, '50% مقدم');
  assert.strictEqual(r.header.taxBasis.value, 'inclusive');
  assert.strictEqual(r.pageText[0].lines[0].text, 'رقم العرض: ق-٢٠٢٦-٠٧');
});

test('RTL table: description at the column edge and a straddling unit word stay whole', () => {
  const items = [];
  for (const [x, s] of AR_HEADER) items.push({ str: s, x, y: 150, w: s.length * 5, h: 10 });
  items.push({ str: '١', x: 522, y: 170, w: 5, h: 10 });
  items.push(...glyphs('لوحة أكريليك', 515, 170));         // starts at the far right of the description column
  items.push(...glyphs('طباعة مباشرة', 515, 185));          // second line of the same cell
  items.push(...glyphs('حبة', 286, 170));                   // right-aligned unit word crossing the band boundary
  items.push({ str: '١٠', x: 195, y: 170, w: 10, h: 10 }, { str: '١٬٢٠٠٫٥٠', x: 110, y: 170, w: 40, h: 10 }, { str: '١٢٬٠٠٥٫٠٠', x: 25, y: 170, w: 45, h: 10 });
  const r = X.extract({ pages: [{ page: 1, width: 595, height: 842, method: 'text', items }] });
  assert.strictEqual(r.rows.length, 1, JSON.stringify(r.rows.map(x => x.rawText)));
  assert.strictEqual(r.rows[0].fields.description.value, 'لوحة أكريليك\nطباعة مباشرة');
  assert.strictEqual(r.rows[0].fields.unit.value, 'حبة');
  assert.strictEqual(r.rows[0].fields.ref.value, '١');
  assert.deepStrictEqual([r.rows[0].fields.qty.value, r.rows[0].fields.unitPrice.value, r.rows[0].fields.lineTotal.value], ['10', '1200.50', '12005.00']);
});

test('tightly spaced header labels (OCR) still give separate columns', () => {
  const w = (x, s) => ({ str: s, x, y: 100, w: s.length * 5, h: 10 });
  const d = (x, s) => ({ str: s, x, y: 120, w: s.length * 5, h: 10 });
  const items = [w(40, '#'), w(60, 'Description'), w(250, 'Unit'), w(300, 'Qty'), w(320, 'Unit'), w(343, 'Price'), w(375, 'Total'),
                 d(40, '1'), d(60, 'Brochure'), d(250, 'pcs'), d(300, '500'), d(345, '3.20'), d(380, '1,600.00')];
  const r = X.extract({ pages: [{ page: 1, width: 595, height: 842, method: 'ocr', items }] });
  assert.strictEqual(r.rows.length, 1);
  assert.deepStrictEqual([r.rows[0].fields.qty.value, r.rows[0].fields.unitPrice.value, r.rows[0].fields.lineTotal.value], ['500', '3.20', '1600.00']);
});

test('left-aligned header labels with right-aligned numbers (real OCR positions)', () => {
  const H = (x0, x1, s) => ({ str: s, x: x0, y: 146, w: x1 - x0, h: 13, conf: 0.95 });   // OCR words carry a confidence
  const R = (x0, x1, s) => ({ str: s, x: x0, y: 169, w: x1 - x0, h: 13, conf: 0.93 });
  const items = [H(45, 51, '#'), H(76, 134, 'Description'), H(266, 286, 'Unit'), H(319, 340, 'Qty'), H(371, 391, 'Unit'), H(395, 420, 'Price'), H(474, 498, 'Total'),
    R(46, 49, '1'), R(76, 119, 'Brochure'), R(121, 136, 'A4,'), R(140, 145, '4'), R(149, 177, 'pages'), R(266, 282, 'pcs'), R(341, 358, '500'), R(441, 461, '3.20'), R(510, 550, '1,600.00')];
  const r = X.extract({ pages: [{ page: 1, width: 595, height: 842, method: 'ocr', items }] });
  const f = r.rows[0].fields;
  assert.deepStrictEqual([f.description.value, f.unit.value, f.qty.value, f.unitPrice.value, f.lineTotal.value], ['Brochure A4, 4 pages', 'pcs', '500', '3.20', '1600.00']);
  assert.ok(!r.rows[0].flags.includes('line_total_mismatch'));
});

// ── 6. OCR page with low-confidence words and no date ──────────────────────
const ocr = X.extract({ pages: [page(1, [
  [100, EN_HEADER.map(c => [c[0], c[1], 0.9])],
  [120, [[40, '1', 0.9], [70, 'Printed sticker', 0.88], [300, 'pcs', 0.9], [350, '50', 0.31], [400, '2.00', 0.9], [480, '100.00', 0.9]]],
], { method: 'ocr' })] });
test('ocr: low-confidence values flagged, missing date reported as missing', () => {
  const r = ocr.rows[0];
  assert.ok(r.fields.qty.flags.includes('low_confidence') && r.flags.includes('low_confidence'));
  assert.strictEqual(val(r.fields.qty), '50', 'value is still shown — flagged, not hidden');
  assert.ok(ocr.header.quotationDate.flags.includes('missing'));
  assert.strictEqual(ocr.header.quotationDate.value, null);
  assert.strictEqual(ocr.pages[0].method, 'ocr');
  assert.ok(ocr.pages[0].lowConfidenceWords >= 1);
});

// ── 7. no table at all ─────────────────────────────────────────────────────
const noTable = X.extract({ pages: [page(1, [
  [100, [[40, 'Signage works for Jeddah branch 5,000.00']]],
  [120, [[40, 'Thank you for your business']]],
])] });
test('no table: numeric lines kept as unstructured rows, no items invented', () => {
  assert.strictEqual(noTable.rows.length, 0);
  assert.strictEqual(noTable.otherRows.length, 1);
  assert.strictEqual(noTable.otherRows[0].kind, 'unstructured');
  assert.ok(noTable.warnings.some(w => w.code === 'no_items_detected'));
});
test('empty/unreadable page is reported', () => {
  const r = X.extract({ pages: [{ page: 1, width: 595, height: 842, method: 'none', items: [] }] });
  assert.ok(r.warnings.some(w => w.code === 'page_unreadable'));
  assert.strictEqual(r.rows.length, 0);
});

// ── 8. parsers ─────────────────────────────────────────────────────────────
test('parseAmount', () => {
  const p = s => X.parseAmount(s);
  assert.strictEqual(p('1,234.50').value, '1234.50');
  assert.strictEqual(p('SAR 1,200').value, '1200');
  assert.strictEqual(p('١٬٢٣٤٫٥٠').value, '1234.50');
  assert.strictEqual(p('ر.س ٩٥٠').value, '950');
  assert.strictEqual(p('(100.00)').value, '-100.00');
  for (const amb of ['1.500', '12,50', '1.234,50']) { assert.strictEqual(p(amb).value, null, amb); assert.ok(p(amb).flags.includes('ambiguous_number')); }
  assert.ok(p('abc').flags.includes('unreadable_number'));
  assert.ok(p('').flags.includes('missing'));
});
test('parseQty', () => {
  assert.deepStrictEqual([X.parseQty('10 pcs').value, X.parseQty('10 pcs').unit], ['10', 'pcs']);
  assert.ok(X.parseQty('LS').flags.includes('lump_sum'));
  assert.strictEqual(X.parseQty('LS').value, null);
  assert.ok(X.parseQty('مقطوعية').flags.includes('lump_sum'));
});
test('parseDates', () => {
  const d = s => X.parseDates(s)[0];
  assert.strictEqual(d('2026-09-20').iso, '2026-09-20');
  assert.strictEqual(d('20/09/2026').iso, '2026-09-20');
  assert.ok(d('09/20/2026').flags.includes('month_first_format'));
  assert.strictEqual(d('03/04/2026').ambiguous, true);
  assert.strictEqual(d('03/04/2026').iso, null);
  assert.strictEqual(d('04/04/2026').iso, '2026-04-04');
  assert.strictEqual(d('1448/03/15').calendar, 'hijri');
  assert.strictEqual(d('1448/03/15').iso, null);
  assert.ok(d('1448/03/15').flags.includes('hijri_not_converted'));
  assert.strictEqual(d('20 Sep 2026').iso, '2026-09-20');
  assert.strictEqual(d('September 20, 2026').iso, '2026-09-20');
  assert.ok(d('20/09/26').flags.includes('two_digit_year'));
  assert.strictEqual(d('20/09/26').iso, null);
  assert.strictEqual(d('15 رمضان 1447').calendar, 'hijri');
  assert.strictEqual(X.parseDates('no date here').length, 0);
});
test('parseTaxCell', () => {
  assert.deepStrictEqual(X.parseTaxCell('15%'), { treatment: 'taxable', rate: '15', amount: null, text: '15%' });
  assert.strictEqual(X.parseTaxCell('0%').treatment, 'zero_rated');
  assert.strictEqual(X.parseTaxCell('Exempt').treatment, 'exempt');
  assert.strictEqual(X.parseTaxCell('معفى').treatment, 'exempt');
  const amt = X.parseTaxCell('45.00');
  assert.strictEqual(amt.treatment, null); assert.strictEqual(amt.amount, '45.00');
});
test('quotation date ignores delivery/validity dates on the same line', () => {
  const r = X.extract({ pages: [page(1, [
    [60, [[40, 'Delivery Date: 01/11/2026']]],
    [80, [[40, 'Date: 21/09/2026   Valid until: 21/10/2026']]],
  ])] });
  assert.strictEqual(r.header.quotationDate.value.iso, '2026-09-21');
});
test('a dated line ("Delivery Date: …") is never read as a charge', () => {
  const r = X.extract({ pages: [page(1, [
    [60, [[40, 'Delivery Date: 15/10/2026']]],
    [80, [[40, 'Delivery charges: 150.00']]],
  ])] });
  assert.strictEqual(r.totals.delivery.value, '150.00');
});
test('conflicting tax statements are not resolved automatically', () => {
  const r = X.extract({ pages: [page(1, [
    [60, [[40, 'Prices are inclusive of VAT']]],
    [80, [[40, 'All amounts exclude VAT']]],
  ])] });
  assert.strictEqual(r.header.taxBasis.value, null);
  assert.ok(r.header.taxBasis.flags.includes('conflicting_tax_statements'));
});
test('SR currency is inferred and flagged', () => {
  const r = X.extract({ pages: [page(1, [[60, [[40, 'Total amount 1,500 SR']]]])] });
  assert.strictEqual(r.header.currency.value, 'SAR');
  assert.ok(r.header.currency.flags.includes('currency_inferred_from_SR'));
});
test('extraction output is plain JSON (storable)', () => {
  const s = JSON.stringify(simple);
  assert.ok(s.length > 100 && JSON.parse(s).version === X.VERSION);
});

// ── 9. import planning (explicit selection, no overwrites) ─────────────────
let idn = 0;
const newId = () => 'it_test' + String(++idn).padStart(6, '0');
const baseOpts = { extractionId: 'ex_' + 'a'.repeat(24), sourceId: 'src_' + 'b'.repeat(24), now: '2026-09-27T12:00:00Z', newId };
test('planImport: only selected rows, tax stated by document kept as extracted', () => {
  const plan = X.planImport(simple, { items: [] }, Object.assign({ selectedRowIds: ['r1', 'r3'] }, baseOpts));
  assert.strictEqual(plan.items.length, 2);
  const a = plan.items[0];
  assert.deepStrictEqual([a.description, a.unit, a.qty, a.unitPrice], ['Acrylic sign 120x60 cm\n10mm clear acrylic, UV print', 'pcs', '10', '120.00']);
  assert.deepStrictEqual(a.tax, { treatment: 'taxable', rate: '15', origin: 'extracted' }, 'document states VAT 15%');
  assert.ok(!a.reviewFlags.includes('default_tax_requires_review'));
  assert.deepStrictEqual(a.sourceRef, { sourceId: baseOpts.sourceId, extractionId: baseOpts.extractionId, rowId: 'r1' });
  assert.strictEqual(a.orig.lineTotal, '1,200.00');
  assert.strictEqual(a.source, 'extracted');
});
test('planImport: no stated tax → default 15% flagged for review', () => {
  const plan = X.planImport(tricky, { items: [] }, Object.assign({ selectedRowIds: ['r1'] }, baseOpts));
  assert.deepStrictEqual(plan.items[0].tax, { treatment: 'taxable', rate: '15', origin: 'default' });
  assert.ok(plan.items[0].reviewFlags.includes('default_tax_requires_review'));
});
test('planImport: optional row only when explicitly selected, and flagged', () => {
  const opt = tricky.rows.find(r => r.kind === 'optional');
  assert.strictEqual(X.planImport(tricky, { items: [] }, Object.assign({ selectedRowIds: [] }, baseOpts)).items.length, 0);
  const plan = X.planImport(tricky, { items: [] }, Object.assign({ selectedRowIds: [opt.id] }, baseOpts));
  assert.ok(plan.items[0].reviewFlags.includes('optional_selected'));
});
test('planImport: ambiguous/lump values imported blank (never guessed)', () => {
  const amb = tricky.rows.find(r => r.fields.ref.value === '5');
  const ls = tricky.rows.find(r => r.fields.ref.value === '3');
  const plan = X.planImport(tricky, { items: [] }, Object.assign({ selectedRowIds: [amb.id, ls.id] }, baseOpts));
  const p1 = plan.items.find(i => i.sourceRef.rowId === amb.id), p2 = plan.items.find(i => i.sourceRef.rowId === ls.id);
  assert.strictEqual(p1.unitPrice, ''); assert.strictEqual(p1.orig.unitPrice, '1.500'); assert.ok(p1.reviewFlags.includes('ambiguous_number'));
  assert.strictEqual(p2.qty, ''); assert.strictEqual(p2.unitPrice, ''); assert.strictEqual(p2.orig.lineTotal, '500.00');
});
test('planImport: rerun never modifies existing (edited) items and skips already-imported rows', () => {
  const first = X.planImport(simple, { items: [] }, Object.assign({ selectedRowIds: ['r1', 'r2'] }, baseOpts));
  const draftItems = first.items.map(i => JSON.parse(JSON.stringify(i)));
  draftItems[0].unitPrice = '110.00';                   // user edit
  draftItems[0].description = 'Edited by user';
  const before = JSON.stringify(draftItems);
  // same extraction again
  const again = X.planImport(simple, { items: draftItems }, Object.assign({ selectedRowIds: ['r1', 'r2', 'r3'] }, baseOpts));
  assert.deepStrictEqual(again.items.map(i => i.sourceRef.rowId), ['r3']);
  assert.ok(again.skipped.some(s => s.rowId === 'r1') && again.skipped.some(s => s.rowId === 'r2'));
  // a NEW extraction of the same document (rerun): identical rows skipped, edited row not overwritten
  const rerun = X.planImport(simple, { items: draftItems }, Object.assign({ selectedRowIds: ['r2'] }, baseOpts, { extractionId: 'ex_' + 'c'.repeat(24) }));
  assert.strictEqual(rerun.items.length, 0);
  assert.strictEqual(JSON.stringify(draftItems), before, 'existing items untouched');
});
test('planImport: re-run on the same document never re-adds an imported row that was edited', () => {
  const first = X.planImport(simple, { items: [] }, Object.assign({ selectedRowIds: ['r1', 'r2', 'r3'] }, baseOpts));
  const draftItems = first.items.map(i => JSON.parse(JSON.stringify(i)));
  draftItems[1].unitPrice = '44.00'; draftItems[1].description = 'Aluminium frame (negotiated)';   // user edits
  const before = JSON.stringify(draftItems);
  const rerunId = 'ex_' + 'd'.repeat(24);
  const rerun = X.planImport(simple, { items: draftItems }, Object.assign({ selectedRowIds: ['r1', 'r2', 'r3'] }, baseOpts, { extractionId: rerunId }));
  assert.strictEqual(rerun.items.length, 0, 'nothing re-added: ' + rerun.items.map(i => i.sourceRef.rowId));
  assert.ok(rerun.skipped.find(s => s.rowId === 'r2').reason.includes('possibly edited'));
  assert.strictEqual(JSON.stringify(draftItems), before);
  // a different stored document with the same printed row is NOT treated as already imported
  const other = X.planImport(simple, { items: draftItems }, Object.assign({ selectedRowIds: ['r2'] }, baseOpts, { extractionId: rerunId, sourceId: 'src_' + 'e'.repeat(24) }));
  assert.strictEqual(other.items.length, 1);
});
test('planImport: header fields only when chosen; currency / basis only on explicit request', () => {
  const none = X.planImport(simple, { items: [] }, Object.assign({ selectedRowIds: [] }, baseOpts));
  assert.strictEqual(none.quotation, null);
  const p = X.planImport(simple, { items: [] }, Object.assign({ selectedRowIds: [], headerKeys: ['quotationRef', 'quotationDate', 'currency', 'taxBasis', 'totals'] }, baseOpts));
  assert.strictEqual(p.quotation.ref, 'Q-2026-118');
  assert.strictEqual(p.quotation.dateIso, '2026-09-20');
  assert.strictEqual(p.quotation.totals.grandTotal, '2248.25');
  assert.strictEqual(p.currency, null); assert.strictEqual(p.priceTaxBasis, null);
  const p2 = X.planImport(simple, { items: [] }, Object.assign({ selectedRowIds: [], headerKeys: ['currency', 'taxBasis'], applyCurrency: true, applyTaxBasis: true }, baseOpts));
  assert.strictEqual(p2.currency, 'SAR'); assert.strictEqual(p2.priceTaxBasis, 'exclusive');
  const amb = X.planImport(arabic, { items: [] }, Object.assign({ selectedRowIds: [], headerKeys: ['quotationDate'] }, baseOpts));
  assert.strictEqual(amb.quotation.dateAmbiguous, true); assert.strictEqual(amb.quotation.dateIso, '');
});

test('planImport: an "other row" imports only when chosen, description only', () => {
  const o = noTable.otherRows[0];
  const plan = X.planImport(noTable, { items: [] }, Object.assign({ selectedRowIds: [], selectedOtherIds: [o.id] }, baseOpts));
  assert.strictEqual(plan.items.length, 1);
  const it = plan.items[0];
  assert.deepStrictEqual([it.qty, it.unitPrice, it.sourceRef.rowId], ['', '', o.id]);
  assert.ok(it.reviewFlags.includes('no_numbers') && it.reviewFlags.includes('default_tax_requires_review'));
  assert.strictEqual(X.planImport(noTable, { items: [] }, Object.assign({ selectedRowIds: [] }, baseOpts)).items.length, 0);
});

// ── 9b. Phase 2 gap fixes ─────────────────────────────────────────────────────
// Two legitimate identical rows (no reference column), one on each page.
const NOREF_HEADER = [[70, 'Description'], [300, 'Unit'], [350, 'Qty'], [400, 'Unit Price'], [480, 'Total']];
const noref = (desc, unit, qty, price, total) => [[70, desc], [300, unit], [350, qty], [400, price], [480, total]];
test('unnumbered identical rows: flagged possible_duplicate and deselected, never removed', () => {
  const r = X.extract({ pages: [page(1, [[100, NOREF_HEADER], [120, noref('Roll-up banner 85x200', 'pcs', '2', '150.00', '300.00')],
                                         [140, noref('Roll-up banner 85x200', 'pcs', '2', '150.00', '300.00')]])] });
  assert.strictEqual(r.rows.length, 2);
  assert.ok(r.rows[1].flags.includes('possible_duplicate') && r.rows[1].duplicateOf === r.rows[0].id);
  assert.strictEqual(r.rows[1].selectedByDefault, false);
  assert.strictEqual(r.rows[0].selectedByDefault, true);
});
const twins = X.extract({ pages: [
  page(1, [[100, NOREF_HEADER], [120, noref('Printed flag 90x150 cm', 'pcs', '5', '60.00', '300.00')],
           [140, noref('Printed flag 90x150 cm', 'pcs', '5', '60.00', '300.00')]]),
  page(2, [[100, NOREF_HEADER], [120, noref('Printed flag 90x150 cm', 'pcs', '5', '60.00', '300.00')]]),
] });
const twinIds = twins.rows.map(r => r.id);
test('identical rows: each keeps its own occurrence, all selectable together', () => {
  assert.strictEqual(twins.rows.length, 3);
  assert.deepStrictEqual(twinIds.map(id => X.occurrences(twins).get(id)), ['1', '2', '3']);
  const plan = X.planImport(twins, { items: [] }, Object.assign({ selectedRowIds: twinIds }, baseOpts));
  assert.strictEqual(plan.items.length, 3, 'none collapsed');
  assert.deepStrictEqual(plan.items.map(i => i.orig.occurrence), ['1', '2', '3']);
  assert.ok(plan.items[1].reviewFlags.includes('same_as_existing_item'), 'identical PO values are flagged, not dropped');
});
test('identical rows: importing one does not mark the others imported (same run and re-run)', () => {
  const one = X.planImport(twins, { items: [] }, Object.assign({ selectedRowIds: [twinIds[0]] }, baseOpts));
  const draftItems = one.items;
  assert.strictEqual(X.alreadyImportedReason(twins, twins.rows[0], draftItems, baseOpts.extractionId, baseOpts.sourceId) !== '', true);
  assert.strictEqual(X.alreadyImportedReason(twins, twins.rows[1], draftItems, baseOpts.extractionId, baseOpts.sourceId), '');
  assert.strictEqual(X.alreadyImportedReason(twins, twins.rows[2], draftItems, baseOpts.extractionId, baseOpts.sourceId), '');
  // re-run of the same document (new extraction id, row ids re-numbered by the engine)
  const rerunId = 'ex_' + 'f'.repeat(24);
  const again = X.extract({ pages: [
    page(1, [[100, NOREF_HEADER], [120, noref('Printed flag 90x150 cm', 'pcs', '5', '60.00', '300.00')],
             [140, noref('Printed flag 90x150 cm', 'pcs', '5', '60.00', '300.00')]]),
    page(2, [[100, NOREF_HEADER], [120, noref('Printed flag 90x150 cm', 'pcs', '5', '60.00', '300.00')]]) ] });
  const marked = again.rows.map(r => !!X.alreadyImportedReason(again, r, draftItems, rerunId, baseOpts.sourceId));
  assert.deepStrictEqual(marked, [true, false, false], 'only the first occurrence counts as imported');
});
test('identical rows: after editing the imported PO item, a re-run still adds only the other occurrences', () => {
  const first = X.planImport(twins, { items: [] }, Object.assign({ selectedRowIds: [twinIds[0]] }, baseOpts));
  const draftItems = first.items.map(i => Object.assign({}, i, { unitPrice: '55.00', description: 'Printed flag (agreed price)' }));
  const before = JSON.stringify(draftItems);
  const rerun = X.planImport(twins, { items: draftItems }, Object.assign({ selectedRowIds: twinIds }, baseOpts, { extractionId: 'ex_' + '9'.repeat(24) }));
  assert.deepStrictEqual(rerun.items.map(i => i.orig.occurrence), ['2', '3']);
  assert.strictEqual(rerun.skipped.length, 1);
  assert.strictEqual(JSON.stringify(draftItems), before);
});
test('incomplete document: reported with page range; apply blocked until acknowledged', () => {
  const part = X.extract({ document: { totalPages: 62 }, pages: [
    page(1, [[100, EN_HEADER], [120, row('1', 'Banner', 'pcs', '2', '100.00', '200.00')]]),
    page(2, [[100, EN_HEADER], [120, row('2', 'Flag', 'pcs', '3', '50.00', '150.00')]]) ] });
  assert.deepStrictEqual(part.document, { totalPages: 62, processedPages: [1, 2], complete: false });
  const w = part.warnings.find(x => x.code === 'document_incomplete');
  assert.ok(w && /2 of 62 pages/.test(w.message) && /pages 1–2/.test(w.message), w && w.message);
  const blocked = X.planImport(part, { items: [] }, Object.assign({ selectedRowIds: ['r1'], headerKeys: ['quotationRef'] }, baseOpts));
  assert.strictEqual(blocked.blocked, 'incomplete_not_acknowledged'); assert.strictEqual(blocked.items.length, 0);
  const ok = X.planImport(part, { items: [] }, Object.assign({ selectedRowIds: ['r1'], headerKeys: ['quotationRef'], acknowledgeIncomplete: true }, baseOpts));
  assert.ok(ok.items[0].reviewFlags.includes('from_incomplete_extraction'));
  assert.ok(ok.quotation.flags.document[0] === 'incomplete_extraction_acknowledged');
  assert.strictEqual(simple.document.complete, true, 'a fully read document is complete');
});
test('document discounts/charges: listed and every imported item flagged as provisional', () => {
  const withAdj = X.extract({ pages: [page(1, [
    [100, EN_HEADER], [120, row('1', 'Banner', 'pcs', '2', '100.00', '200.00')],
    [160, [[330, 'Subtotal'], [480, '200.00']]], [175, [[330, 'Discount'], [480, '(20.00)']]],
    [190, [[330, 'Delivery charges'], [480, '50.00']]], [205, [[330, 'Grand Total'], [480, '230.00']]] ])] });
  const adj = X.unappliedAdjustments(withAdj).map(a => a.kind + ' ' + a.amount);
  assert.deepStrictEqual(adj, ['discount -20.00', 'delivery 50.00']);
  const plan = X.planImport(withAdj, { items: [] }, Object.assign({ selectedRowIds: ['r1'], headerKeys: ['totals'] }, baseOpts));
  assert.ok(plan.items[0].reviewFlags.includes('document_adjustments_unapplied'));
  assert.strictEqual(plan.adjustments.length, 2);
  assert.ok(plan.quotation.flags.adjustments.length === 2);
  assert.ok(!X.planImport(simple, { items: [] }, Object.assign({ selectedRowIds: ['r1'] }, baseOpts)).items[0].reviewFlags.includes('document_adjustments_unapplied'));
});
test('mixed page: text-layer header and OCR table keep distinguishable evidence', () => {
  const textItems = [{ str: 'Quotation No: MX-77', x: 40, y: 40, w: 100, h: 10 }, { str: 'Date: 21/09/2026', x: 300, y: 40, w: 80, h: 10 }];
  const ocrWords = [];
  for (const [x, s] of EN_HEADER) ocrWords.push({ str: s, x, y: 100, w: s.length * 5, h: 10, conf: 0.9, src: 'ocr' });
  for (const [x, s] of row('1', 'Vinyl banner', 'pcs', '4', '75.00', '300.00')) ocrWords.push({ str: s, x, y: 120, w: s.length * 5, h: 10, conf: 0.88, src: 'ocr' });
  const r = X.extract({ pages: [{ page: 1, width: 595, height: 842, method: 'mixed', items: textItems.concat(ocrWords) }] });
  assert.strictEqual(r.header.quotationRef.value, 'MX-77'); assert.strictEqual(r.header.quotationRef.method, 'text');
  assert.strictEqual(r.rows.length, 1);
  assert.strictEqual(r.rows[0].fields.unitPrice.method, 'ocr'); assert.strictEqual(r.rows[0].fields.unitPrice.value, '75.00');
  assert.deepStrictEqual([...new Set(r.pageText[0].lines.map(l => l.method))].sort(), ['ocr', 'text']);
  assert.strictEqual(r.pages[0].method, 'mixed');
});

// ── 10. supplier suggestions ───────────────────────────────────────────────
const suppliers = [
  { id: '5', name: 'GIFFIN Graphics Company', snapshot: { vatNumber: '311297440900003' } },
  { id: '9', name: 'ACME Signs Trading Est', snapshot: { vatNumber: '' } },
  { id: '12', name: 'Other Vendor', snapshot: { vatNumber: '300000000000003' } },
];
test('suggestSuppliers: VAT match ranks first, name similarity second, nothing auto-selected', () => {
  const s = X.suggestSuppliers(simple.header, suppliers);
  assert.strictEqual(s[0].id, '12'); assert.ok(s[0].reasons.includes('VAT number matches'));
  assert.ok(s.some(x => x.id === '9' && x.reasons.includes('name similar')));
  assert.ok(!s.some(x => x.id === '5'));
});
test('supplierMismatch flags VAT and name differences', () => {
  const m = X.supplierMismatch(simple.header, suppliers[0]);
  assert.ok(m.some(x => x.code === 'vat_mismatch') && m.some(x => x.code === 'name_differs'));
  assert.deepStrictEqual(X.supplierMismatch(simple.header, suppliers[2]).map(x => x.code), ['name_differs']);
});

// ── real-document layout rules (synthetic, fictional suppliers) ───────────
// Signage-style layout: W/D/H dimension columns, a merged header cell
// ("H QTY/SQM UNIT COST", one word per text item), bullets in the number column, titled items with
// sub-numbered or unnumbered priced components, a table that continues on
// page 2 without a header, letterhead/footer repeated on both pages, a price
// with no quantity and a "-" amount, an unlabelled Total, payment schedule lines.
const SIGN_HEAD = [[40, 'S.No'], [70, 'DESCRIPTION'], [290, 'W'], [318, 'D'], [345, 'H'], [355, 'QTY/SQM'], [395, 'UNIT'], [420, 'COST'], [480, 'TOTAL'], [508, 'SR']];
const sn = (ref, desc, w, d, h, q, up, tot) => [[40, ref], [70, desc], [290, w], [315, d], [340, h], [385 - q.length * 5, q], [440 - up.length * 5, up], [520 - tot.length * 5, tot]]
  .filter(c => c[1] !== '');
const FURN = [[20, [[200, 'GENERAL CONTRACTING lTRADING']]], [780, [[60, 'VAT: 30 12 34 56 78 90 00 3 - CR: 40 30 12 34 56']]],
              [792, [[60, 'WWW.NORTHBUILD.COM - EMAIL: SALES@NORTHBUILD.COM']]]];
const signDoc = {
  document: { totalPages: 2, fileName: 'Q-88 - HARBOR - 3-5-2026.pdf' },
  pages: [
    page(1, FURN.concat([
      [40, [[60, 'QUOTATION']]],
      [55, [[60, 'CLIENT: NORTHWIND'], [400, 'Quotation No.'], [470, 'Q-88']]],
      [67, [[60, 'ATTN:'], [400, 'DATE'], [470, 'Wed-Mar-04-2026']]],
      [79, [[60, 'PROJECT: HARBOR']]],
      [100, SIGN_HEAD],
      [115, sn('1', 'Lobby sign', '1.20', '0.40', '0.90', '3', '250.00', '750.00')],
      [127, [[40, '-'], [70, 'SIZE: W 1.2 X D 0.4 X H 0.5 M']]],
      [139, [[70, 'Made of acrylic']]],
      [155, [[40, '2'], [70, 'Reception desk works']]],
      [167, [[40, '-']].concat(sn('', 'Paint - SIZE: W 2 x H 1 M', '2.00', '', '1.00', '1', '400.00', '400.00'))],
      [179, sn('', 'Logo plate', '0.50', '', '0.30', '1', '150.00', '150.00')],
      [195, [[40, '3'], [70, 'WALL GRAPHICS']]],
      [207, sn('1', 'SIZE: W 3 x H 2 M', '3.00', '0.10', '2.00', '1', '600.00', '600.00')],
      [219, sn('2', 'SIZE: W 2 x H 2 M', '2.00', '0.10', '2.00', '1', '400.00', '400.00')],
      [231, sn('3', 'SIZE: W 2 x H 2 M', '2.00', '0.10', '2.00', '1', '400.00', '400.00')],
    ])),
    page(2, FURN.concat([
      [40, sn('4', 'Banner stand', '0.80', '0.30', '2.00', '2', '100.00', '200.00')],
      [52, sn('5', 'Facade letters', '2.00', '', '0.50', '', '900.00', '-')],
      [64, sn('#', 'Delivery & Installation', '', '', '', '1.00', '300.00', '300.00')],
      [76, [[420, 'Total'], [480, '3,200.00']]],
      [88, [[60, 'TERMS & CONDITIONS'], [420, 'VAT'], [450, '15.00%'], [490, '480.00']]],
      [100, [[60, 'QUOTATION VALIDITY: 10 DAYS'], [420, 'Grand Total'], [480, '3,680.00']]],
      [112, [[60, 'PAYMENT TERMS:']]],
      [124, [[62, '60% Advance Payment'], [480, '2,208.00']]],
      [136, [[62, '40% On completion'], [480, '1,472.00']]],
    ])),
  ] };
const sign = X.extract(signDoc);
const byDesc = (r, re) => r.rows.find(x => re.test(x.fields.description.value || ''));
test('dimension columns: W/D/H are dimensions, never quantities; merged header "H QTY/SQM UNIT COST" split', () => {
  const r = byDesc(sign, /^Lobby sign/);
  assert.strictEqual(val(r.fields.qty), '3'); assert.strictEqual(val(r.fields.unitPrice), '250.00'); assert.strictEqual(val(r.fields.lineTotal), '750.00');
  assert.deepStrictEqual(val(r.fields.dimensions), { W: '1.20', D: '0.40', H: '0.90' });
});
test('dimension columns: a contradicting size in the description is flagged, not corrected', () => {
  const r = byDesc(sign, /^Lobby sign/);
  assert.ok(r.flags.includes('spec_dimension_conflict'));
  assert.deepStrictEqual(r.fields.dimensions.conflicts, [{ dim: 'H', column: '0.90', description: 0.5 }]);
  assert.strictEqual(val(r.fields.dimensions).H, '0.90');
});
test('bullets in the number column continue the item (multi-line spec stays with its parent)', () => {
  const r = byDesc(sign, /^Lobby sign/);
  assert.strictEqual(val(r.fields.description), 'Lobby sign\nSIZE: W 1.2 X D 0.4 X H 0.5 M\nMade of acrylic');
});
test('titled item with an unnumbered second priced line: both lines are components of the item', () => {
  const a = byDesc(sign, /^Reception desk works/), b = byDesc(sign, /^Logo plate/);
  assert.ok(a && b);
  assert.deepStrictEqual(a.parent, { ref: '2', title: 'Reception desk works' });
  assert.deepStrictEqual(b.parent, { ref: '2', title: 'Reception desk works' });
  assert.strictEqual(val(b.fields.lineTotal), '150.00');
});
test('titled item with sub-numbered lines: heading row + components; identical components both kept and selected', () => {
  assert.ok(sign.otherRows.some(o => o.kind === 'item_heading' && /WALL GRAPHICS/.test(o.text)));
  const comps = sign.rows.filter(r => r.parent && r.parent.ref === '3');
  assert.deepStrictEqual(comps.map(r => val(r.fields.ref)), ['1', '2', '3']);
  assert.ok(comps.every(r => r.selectedByDefault));
  assert.ok(comps[2].flags.includes('same_values_as_other_row') && !comps[2].flags.includes('possible_duplicate'));
  assert.strictEqual(byDesc(sign, /^Banner stand/).parent, null, 'the successor of the parent number starts a new item');
});
test('repeated letterhead/footer is page furniture; the table resumes on page 2 without a header', () => {
  const r = byDesc(sign, /^Banner stand/);
  assert.ok(r && r.page === 2 && val(r.fields.qty) === '2');
  assert.ok(!sign.rows.some(x => /NORTHBUILD|CONTRACTING/.test(x.rawText)));
});
test('price with no quantity and a "-" amount: visible, not selected, nothing invented', () => {
  const r = byDesc(sign, /^Facade letters/);
  assert.strictEqual(val(r.fields.qty), null); assert.strictEqual(val(r.fields.lineTotal), null);
  assert.strictEqual(val(r.fields.unitPrice), '900.00');
  assert.ok(r.flags.includes('dash_amount') && r.flags.includes('no_amount'));
  assert.strictEqual(r.selectedByDefault, false);
});
test('a separately printed service line ("#") stays its own row', () => {
  const r = byDesc(sign, /^Delivery & Installation/);
  assert.ok(r && val(r.fields.lineTotal) === '300.00' && val(r.fields.qty) === '1.00' && r.kind === 'item');
});
test('unlabelled Total before VAT is the subtotal (flagged); rows without an amount are excluded from the sum', () => {
  assert.strictEqual(val(sign.totals.subtotal), '3200.00');
  assert.ok(sign.totals.subtotal.flags.includes('from_total_line'));
  assert.strictEqual(val(sign.totals.vat), '480.00'); assert.strictEqual(val(sign.totals.grandTotal), '3680.00');
  assert.ok(sign.totals.checks.length === 2 && sign.totals.checks.every(c => c.ok), JSON.stringify(sign.totals.checks));
  assert.strictEqual(sign.totals.checks[1].excludedRows.length, 1);
});
test('payment-schedule lines ("60% Advance … 2,208.00") are terms, not charges', () => {
  assert.strictEqual(val(sign.totals.delivery), null);
  assert.strictEqual(sign.totals.otherCharges.length, 0);
  assert.strictEqual(val(sign.header.paymentTerms), '60% Advance Payment\n40% On completion');
});
test('header cells: label cell → value cell; validity beside totals; client and project; hyphenated weekday date', () => {
  const h = sign.header;
  assert.strictEqual(val(h.quotationRef), 'Q-88');
  assert.strictEqual(val(h.quotationDate).iso, '2026-03-04');
  assert.strictEqual(val(h.validity), '10 DAYS');
  assert.strictEqual(val(h.customerName), 'NORTHWIND');
  assert.strictEqual(val(h.projectName), 'HARBOR');
});
test('file-name date is never used; a disagreement with the printed date is reported', () => {
  assert.ok(sign.header.quotationDate.flags.includes('filename_date_differs'));
  assert.ok(sign.warnings.some(w => w.code === 'filename_date_differs'));
  const same = X.extract(Object.assign({}, signDoc, { document: { totalPages: 2, fileName: 'Q-88 4-3-2026.pdf' } }));
  assert.ok(!same.header.quotationDate.flags.includes('filename_date_differs'));
});
test('supplier: spaced VAT/CR digits read from the footer; tagline is not a name; website domain as flagged fallback', () => {
  const h = sign.header;
  assert.strictEqual(val(h.supplierVat), '301234567890003'); assert.ok(h.supplierVat.flags.includes('digits_spaced_in_source'));
  assert.strictEqual(val(h.supplierCr), '4030123456');
  assert.strictEqual(val(h.supplierName), 'NORTHBUILD');
  assert.ok(h.supplierName.flags.includes('name_from_website'));
});
test('planImport: component keeps parent title in its name; dimensions structured as printed; evidence in orig', () => {
  const comp = byDesc(sign, /^Logo plate/);
  const plan = X.planImport(sign, { items: [] }, Object.assign({ selectedRowIds: [comp.id] }, baseOpts));
  const it = plan.items[0];
  assert.strictEqual(it.name, 'Reception desk works — Logo plate');
  assert.strictEqual(it.description, 'Reception desk works — Logo plate');
  assert.deepStrictEqual(it.dimensions, { values: { W: '0.50', D: '', H: '0.30' }, unit: '', origin: 'extracted', status: 'as_printed', conflicts: [],
    described: { main: '', additional: [] } });
  assert.strictEqual(it.orig.description, 'Logo plate');
  assert.strictEqual(it.orig.parent, '2 Reception desk works');
  assert.strictEqual(it.orig.dimensions, 'W 0.50 × D (blank) × H 0.30');
  assert.strictEqual(it.orig.proposedDescription, it.description, 'the proposed text is kept so later user edits stay detectable');
  assert.ok(it.reviewFlags.includes('component_of_item'));
  assert.strictEqual(it.qty, '1');
});
test('planImport: item name is the printed title; the long specification stays in the internal description', () => {
  const lobby = byDesc(sign, /^Lobby sign/);
  const it = X.planImport(sign, { items: [] }, Object.assign({ selectedRowIds: [lobby.id] }, baseOpts)).items[0];
  assert.strictEqual(it.name, 'Lobby sign');
  assert.strictEqual(it.description, 'Lobby sign\nSIZE: W 1.2 X D 0.4 X H 0.5 M\nMade of acrylic');
  assert.strictEqual(it.orig.description, it.description);
});
test('planImport: conflicting source dimensions are kept as printed and need confirmation (unit never inferred)', () => {
  const lobby = byDesc(sign, /^Lobby sign/);
  const d = X.planImport(sign, { items: [] }, Object.assign({ selectedRowIds: [lobby.id] }, baseOpts)).items[0].dimensions;
  assert.deepStrictEqual(d.values, { W: '1.20', D: '0.40', H: '0.90' }, 'column values, not the description\'s');
  assert.strictEqual(d.status, 'needs_confirmation');
  assert.deepStrictEqual(d.conflicts, [{ dim: 'H', column: '0.90', description: '0.5' }]);
  assert.strictEqual(d.unit, '', 'the description says "M" but the columns print no unit — nothing inferred');
  const plain = X.planImport(simple, { items: [] }, Object.assign({ selectedRowIds: ['r1'] }, baseOpts)).items[0];
  assert.strictEqual(plain.dimensions, null, 'no dimension columns → no dimensions invented');
});

// Invoice-style layout: label and value in separate cells, buyer block in a
// right-hand column, "--- End ---" filler row, currency code inside amount cells,
// payment terms on the same line as the subtotal.
const box = X.extract({ document: { totalPages: 1 }, pages: [page(1, [
  [60, [[330, 'Quote Ref. #:'], [450, 'AB-26-7']]],
  [72, [[360, 'Date:'], [450, '5 October 2026']]],
  [100, [[28, 'Falcon Print Company']]],
  [112, [[28, 'Industrial Area, Dammam'], [285, 'Vista United']]],
  [124, [[28, 'VAT 300111222300003'], [244, 'Attn:'], [285, 'Dammam, KSA']]],
  [136, [[28, 'CR No. 2050123456']]],
  [160, [[33, 'Project / Campaign Name:'], [192, 'Autumn launch']]],
  [190, [[35, 'Quantity'], [208, 'Description'], [410, 'Unit Price'], [513, 'Amount']]],
  [205, [[48, '12'], [208, 'Roll-up banners'], [410, 'SAR'], [430, '150.00'], [500, 'SAR'], [520, '1,800.00']]],
  [220, [[36, '--- End ---'], [208, '--- End ---'], [410, '--- End ---'], [513, '--- End ---']]],
  [235, [[28, 'Payment Terms: 30 days'], [410, 'Sub Total'], [500, 'SAR'], [520, '1,800.00']]],
  [250, [[28, 'Special Instructions:'], [410, 'VAT 15%'], [500, 'SAR'], [530, '270.00']]],
  [265, [[410, 'Grand Total'], [500, 'SAR'], [520, '2,070.00']]],
])] });
test('boxed layout: currency codes stay out of descriptions; "--- End ---" is not an item', () => {
  assert.strictEqual(box.rows.length, 1);
  const r = box.rows[0];
  assert.strictEqual(val(r.fields.description), 'Roll-up banners');
  assert.strictEqual(val(r.fields.qty), '12'); assert.strictEqual(val(r.fields.unitPrice), '150.00'); assert.strictEqual(val(r.fields.lineTotal), '1800.00');
  assert.ok(box.otherRows.some(o => o.kind === 'end_marker'));
  assert.ok(box.totals.checks.every(c => c.ok) && box.totals.checks.length === 2, JSON.stringify(box.totals.checks));
});
test('boxed layout: header values from neighbouring cells; buyer column never read as supplier', () => {
  const h = box.header;
  assert.strictEqual(val(h.quotationRef), 'AB-26-7');
  assert.strictEqual(val(h.quotationDate).iso, '2026-10-05');
  assert.strictEqual(val(h.supplierName), 'Falcon Print Company');
  assert.strictEqual(val(h.supplierVat), '300111222300003'); assert.strictEqual(val(h.supplierCr), '2050123456');
  assert.strictEqual(val(h.customerName), 'Vista United');
  assert.strictEqual(val(h.projectName), 'Autumn launch');
  assert.strictEqual(val(h.paymentTerms), '30 days');
});

// ── selection safety and printed dimension cells ───────────────────────────
test('dimension columns: a blank cell stays blank under its own letter (never relabelled)', () => {
  const r = byDesc(sign, /^Logo plate/);
  assert.deepStrictEqual(val(r.fields.dimensions), { W: '0.50', D: '', H: '0.30' });
  assert.strictEqual(r.fields.dimensions.raw, 'W 0.50 × D (blank) × H 0.30');
  // the description says "W 2 x H 1" for the paint line; its printed H column value stays H
  const paint = byDesc(sign, /^Reception desk works/);
  assert.deepStrictEqual(val(paint.fields.dimensions), { W: '2.00', D: '', H: '1.00' });
});
test('OCR rows start unselected whatever their confidence, numbering or matching values', () => {
  const words = [];
  for (const [x, s] of EN_HEADER) words.push({ str: s, x, y: 100, w: s.length * 5, h: 10, conf: 0.97 });
  for (const [y, n] of [[120, '1'], [135, '2']])
    for (const [x, s] of row(n, 'Acrylic letters 20 cm', 'pcs', '10', '40.00', '400.00')) words.push({ str: s, x, y, w: s.length * 5, h: 10, conf: 0.97 });
  const r = X.extract({ pages: [{ page: 1, width: 595, height: 842, method: 'ocr', items: words }] });
  assert.strictEqual(r.rows.length, 2);
  assert.ok(r.rows.every(x => x.fields.unitPrice.method === 'ocr' && x.selectedByDefault === false));
  assert.ok(r.rows[1].flags.includes('same_values_as_other_row'), 'numbering is reported, but does not select an OCR row');
});
test('mixed-source rows start unselected; text-layer header stays selectable, OCR header does not qualify', () => {
  const textItems = [{ str: 'Quotation No: MX-78', x: 40, y: 40, w: 100, h: 10 }];
  const words = [];
  for (const [x, s] of EN_HEADER) words.push({ str: s, x, y: 100, w: s.length * 5, h: 10, conf: 0.95, src: 'ocr' });
  // description from the text layer, numbers from OCR → a mixed row
  textItems.push({ str: 'Vinyl banner', x: 70, y: 120, w: 60, h: 10 });
  for (const [x, s] of [[40, '1'], [300, 'pcs'], [350, '4'], [400, '75.00'], [480, '300.00']]) words.push({ str: s, x, y: 120, w: s.length * 5, h: 10, conf: 0.95, src: 'ocr' });
  const r = X.extract({ pages: [{ page: 1, width: 595, height: 842, method: 'mixed', items: textItems.concat(words) }] });
  assert.strictEqual(r.rows.length, 1);
  assert.strictEqual(r.rows[0].selectedByDefault, false);
  assert.strictEqual(r.header.quotationRef.method, 'text');
});
test('text-layer rows keep default selection (numbered twins included)', () => {
  assert.ok(simple.rows.every(x => x.selectedByDefault));
  assert.ok(sign.rows.filter(x => x.parent && x.parent.ref === '3').every(x => x.selectedByDefault));
});

// ── item photos: placement → item block, exclusions, re-run safety ─────────
const LOGO = { x: 40, y: 5, w: 50, h: 30 };
const signImgDoc = JSON.parse(JSON.stringify(signDoc));
signImgDoc.pages[0].images = [
  LOGO,                                        // letterhead logo, repeated on page 2 → excluded
  { x: 300, y: 60, w: 40, h: 20 },             // above the item table header → excluded
  { x: 70, y: 141, w: 40, h: 13 },             // inside item 1's block
  { x: 70, y: 182, w: 36, h: 12 },             // inside item 2's block (a titled item with 2 priced components)
  { x: 70, y: 242, w: 40, h: 20 }, { x: 120, y: 242, w: 40, h: 20 },   // two pictures in item 3's block (3 components)
  { x: 200, y: 120, w: 8, h: 8 },              // icon-sized → excluded
  { x: 40, y: 250, w: 200, h: 12 },            // thin strip → excluded
];
signImgDoc.pages[1].images = [
  LOGO,
  { x: 300, y: 38, w: 40, h: 13 },             // near the top of a page without letterhead: still item 4's picture
  { x: 300, y: 45, w: 40, h: 14 },             // starts in item 4's block and runs into item 5 → uncertain
  { x: 300, y: 150, w: 60, h: 30 },            // after the totals (signature / stamp area) → excluded
];
const signImg = X.extract(signImgDoc);
const phAt = (page, y) => signImg.photos.find(p => p.page === page && p.region.y === y);
test('photos: logos, header, signature area, icons and strips are never suggested (listed with the reason)', () => {
  assert.deepStrictEqual(signImg.photos.filter(p => p.status === 'excluded').map(p => p.page + ':' + p.exclude).sort(),
    ['1:above_item_table', '1:decorative_strip', '1:repeated_on_pages', '1:too_small', '2:after_item_table', '2:repeated_on_pages']);
  assert.ok(signImg.photos.filter(p => p.status === 'excluded').every(p => p.target === null));
});
test('photos: a picture belongs to the item block it sits in (page position + item boundaries)', () => {
  const p = phAt(1, 141);
  assert.strictEqual(p.status, 'candidate'); assert.strictEqual(p.confidence, 'high');
  assert.strictEqual(p.target.kind, 'row'); assert.strictEqual(byDesc(signImg, /^Lobby sign/).id, p.target.rowId);
  const top = phAt(2, 38);
  assert.strictEqual(top.target.rowId, byDesc(signImg, /^Banner stand/).id, 'a picture near the top of a page is not treated as letterhead');
});
test('photos: pictures of a titled item with priced components stay with the PARENT, never one component', () => {
  const g2 = phAt(1, 182);
  assert.strictEqual(g2.target.kind, 'group'); assert.strictEqual(g2.target.parent, '2 Reception desk works');
  assert.strictEqual(g2.target.rowIds.length, 2);
  const g3 = signImg.photos.filter(p => p.page === 1 && p.region.y === 242);
  assert.strictEqual(g3.length, 2, 'several photos per item');
  assert.ok(g3.every(p => p.target.kind === 'group' && p.target.parent === '3 WALL GRAPHICS' && p.target.rowIds.length === 3));
});
test('photos: a picture crossing into the next item is flagged for confirmation', () => {
  const p = phAt(2, 45);
  assert.strictEqual(p.status, 'candidate'); assert.strictEqual(p.confidence, 'low');
  assert.ok(p.reasons.includes('starts_above_item') || p.reasons.includes('overlaps_next_item'), p.reasons.join(','));
});
test('photos: without image placements nothing is guessed', () => {
  assert.deepStrictEqual(sign.photos, []);
});
// draft side
const phStored = signImg.photos.filter(p => p.status === 'candidate').map((p, i) => ({ id: 'ph_' + String(i).padStart(24, '0'), page: p.page, region: p.region, kind: 'embedded' }));
let phN = 0;
const phOpts = extra => Object.assign({ extractionId: baseOpts.extractionId, sourceId: baseOpts.sourceId, stored: phStored, newId: () => 'pa_TEST' + String(++phN).padStart(4, '0'), now: '' }, extra || {});
test('planPhotos: only photos of items in the draft are attached; parent photos need a component in the draft', () => {
  const lobby = byDesc(signImg, /^Lobby sign/);
  const one = X.planImport(signImg, { items: [] }, Object.assign({ selectedRowIds: [lobby.id] }, baseOpts));
  const add = X.planPhotos(signImg, { items: one.items, photos: [] }, phOpts({ items: one.items }));
  assert.strictEqual(add.length, 1);
  assert.deepStrictEqual(add[0].target, { kind: 'item', itemId: one.items[0].id });
  assert.strictEqual(add[0].status, 'suggested'); assert.strictEqual(add[0].includeInPdf, false);
  const comp = byDesc(signImg, /^Logo plate/);
  const two = X.planImport(signImg, { items: [] }, Object.assign({ selectedRowIds: [comp.id] }, baseOpts));
  const g = X.planPhotos(signImg, { items: two.items, photos: [] }, phOpts({ items: two.items }));
  assert.deepStrictEqual(g.map(a => a.target), [{ kind: 'group', sourceId: baseOpts.sourceId, parent: '2 Reception desk works' }]);
  assert.deepStrictEqual(X.photosForItem({ photos: g }, two.items[0]).length, 1);
});
test('planPhotos: uncertain positions are attached as "uncertain" for confirmation', () => {
  const rows = signImg.rows.filter(r => r.page === 2).map(r => r.id);   // incl. the unpriced row, ticked by hand
  const plan = X.planImport(signImg, { items: [] }, Object.assign({ selectedRowIds: rows }, baseOpts));
  const add = X.planPhotos(signImg, { items: plan.items, photos: [] }, phOpts({ items: plan.items }));
  assert.ok(add.some(a => a.status === 'uncertain') && add.some(a => a.status === 'suggested'));
});
test('planPhotos: a re-run never touches existing decisions (confirmed, moved, removed) and finds edited items by printed identity', () => {
  const all = signImg.rows.filter(r => r.selectedByDefault).map(r => r.id);
  const plan = X.planImport(signImg, { items: [] }, Object.assign({ selectedRowIds: all }, baseOpts));
  const items = plan.items.map(i => Object.assign({}, i, { description: i.description + ' (edited)' }));
  const first = X.planPhotos(signImg, { items, photos: [] }, phOpts({ items }));
  first[0].status = 'removed'; first[1].status = 'confirmed'; first[1].target = { kind: 'item', itemId: items[items.length - 1].id };
  const before = JSON.stringify(first);
  const rerunId = 'ex_' + 'e'.repeat(24);
  const again = X.extract(signImgDoc);                     // a new extraction of the same document
  const add = X.planPhotos(again, { items, photos: first }, phOpts({ items, extractionId: rerunId }));
  assert.deepStrictEqual(add, [], 'no photo is suggested twice, removed ones included');
  assert.strictEqual(JSON.stringify(first), before);
  // a fresh draft of edited items (different extraction row ids) still gets each photo on the right item
  const moved = items.map(i => Object.assign({}, i, { sourceRef: Object.assign({}, i.sourceRef, { rowId: 'r' + (900 + items.indexOf(i)) }) }));
  const fresh = X.planPhotos(again, { items: moved, photos: [] }, phOpts({ items: moved, extractionId: rerunId }));
  assert.strictEqual(fresh.length, first.length);
  const lobbyItem = moved.find(i => /^Lobby sign/.test(i.description));
  assert.ok(fresh.some(a => a.target.kind === 'item' && a.target.itemId === lobbyItem.id));
});

// ── measurements printed only in the description stay with the item ───────
const DH = [[40, '#'], [70, 'Description'], [290, 'W'], [318, 'D'], [345, 'H'], [372, 'QTY'], [410, 'UNIT'], [436, 'COST'], [480, 'TOTAL']];
const dr = (ref, desc, w, d, h, q, up, tot) => [[40, ref], [70, desc], [300 - w.length * 5, w], [328 - d.length * 5, d], [355 - h.length * 5, h], [385 - q.length * 5, q], [440 - up.length * 5, up], [520 - tot.length * 5, tot]].filter(c => c[1] !== '');
const measDoc = X.extract({ pages: [page(1, [
  [100, DH],
  [115, dr('1', 'Outer panel', '1.00', '0.20', '2.00', '1', '500.00', '500.00')],
  [127, [[70, 'SIZE: W 1 x H 2 M + BASE W 1.2 M X D 0.20 M']]],
  [145, dr('2', 'Main gate branding', '6.00', '0.10', '1.20', '1', '900.00', '900.00')],
  [157, [[70, 'SIZE: 2+2+2 X H 1.2 M + POLE OF 1.5 M']]],
  [175, dr('3', 'Landmark', '5.00', '0.26', '2.00', '1', '800.00', '800.00')],
  [187, [[70, 'SIZE: W 2 x D 0.30 x H 5 m + 1m concrete base height with 1.5m underground']]],
  [205, dr('4', 'Gate sign', '1.00', '', '0.50', '2', '100.00', '200.00')],
  [217, [[70, 'SIZE: W 1 X D 0.5 X H 0.5 M']]],
  [235, dr('5', 'Plain panel', '1.70', '0.30', '2.42', '1', '300.00', '300.00')],
  [247, [[70, 'SIZE: W 1.7 x D 0.3 x H 2.42 M']]],
  [265, dr('6', 'Unitless panel', '1.70', '0.30', '2.42', '1', '300.00', '300.00')],
  [277, [[70, 'SIZE: W 1.7 x D 0.3 x H 2.42']]],
])] });
const measItem = re => X.planImport(measDoc, { items: [] }, Object.assign({ selectedRowIds: [byDesc(measDoc, re).id] }, baseOpts)).items[0];
test('description measurements: "+ BASE …" is kept word for word as a separate measurement of the same item', () => {
  const d = measItem(/^Outer panel/).dimensions;
  assert.deepStrictEqual(d.described.additional, ['BASE W 1.2 M X D 0.20 M']);
  assert.deepStrictEqual(d.values, { W: '1.00', D: '0.20', H: '2.00' }, 'columns untouched');
  assert.strictEqual(d.status, 'as_printed', 'the BASE depth is not the item\'s depth — no false conflict');
});
test('description measurements: "2+2+2" composition and "+ POLE OF 1.5 M" are both preserved', () => {
  const d = measItem(/^Main gate branding/).dimensions;
  assert.strictEqual(d.described.main, '2+2+2 X H 1.2 M');
  assert.deepStrictEqual(d.described.additional, ['POLE OF 1.5 M']);
});
test('description measurements: concrete base / underground text is kept; a conflicting main size stays for confirmation', () => {
  const d = measItem(/^Landmark/).dimensions;
  assert.deepStrictEqual(d.described.additional, ['1m concrete base height with 1.5m underground']);
  assert.strictEqual(d.described.main, 'W 2 x D 0.30 x H 5 m');
  assert.strictEqual(d.status, 'needs_confirmation');
  assert.deepStrictEqual(d.conflicts.map(c => c.dim), ['W', 'D', 'H']);
});
test('description measurements: a value printed where the column is blank needs confirmation; the blank cell stays blank', () => {
  const d = measItem(/^Gate sign/).dimensions;
  assert.strictEqual(d.values.D, '');
  assert.deepStrictEqual(d.conflicts, [{ dim: 'D', column: '', description: '0.5' }]);
  assert.strictEqual(d.status, 'needs_confirmation');
  assert.strictEqual(d.described.main, 'W 1 X D 0.5 X H 0.5 M');
  assert.ok(byDesc(measDoc, /^Gate sign/).flags.includes('spec_dimension_conflict'), 'also flagged in review');
});
test('description measurements: the size line\'s UNIT is kept when the columns print none', () => {
  const d = measItem(/^Plain panel/).dimensions;
  assert.deepStrictEqual(d.described, { main: 'W 1.7 x D 0.3 x H 2.42 M', additional: [] }, 'M is information the columns lack');
  assert.strictEqual(d.unit, '', 'the unit is not copied onto the column values — it stays with its printed text');
  assert.strictEqual(d.status, 'as_printed');
});
test('description measurements: only a size line with the same values, labels and no extra unit is not duplicated', () => {
  const d = measItem(/^Unitless panel/).dimensions;
  assert.deepStrictEqual(d.described, { main: '', additional: [] });
  assert.deepStrictEqual(d.values, { W: '1.70', D: '0.30', H: '2.42' });
});
test('dimension units printed in headers or cells are recognised, not assumed absent', () => {
  const HU = [[40, '#'], [70, 'Description'], [280, 'W (M)'], [318, 'D'], [328, '(M)'], [345, 'H(M)'], [372, 'QTY'], [410, 'UNIT'], [436, 'COST'], [480, 'TOTAL']];
  const hdoc = X.extract({ pages: [page(1, [
    [100, HU],
    [115, dr('1', 'Header-unit panel', '1.70', '0.30', '2.42', '1', '300.00', '300.00')],
    [127, [[70, 'SIZE: W 1.7 x D 0.3 x H 2.42 M']]],
    [145, dr('2', 'Other panel', '1.00', '0.20', '2.00', '1', '300.00', '300.00')],
    [157, [[70, 'SIZE: W 1 x D 0.2 x H 2 MM']]],
  ])] });
  const it1 = X.planImport(hdoc, { items: [] }, Object.assign({ selectedRowIds: [byDesc(hdoc, /^Header-unit/).id] }, baseOpts)).items[0];
  assert.strictEqual(it1.dimensions.unit, 'M', 'printed in the headers');
  assert.deepStrictEqual(it1.dimensions.described, { main: '', additional: [] }, 'same values, labels and unit → nothing lost');
  const it2 = X.planImport(hdoc, { items: [] }, Object.assign({ selectedRowIds: [byDesc(hdoc, /^Other panel/).id] }, baseOpts)).items[0];
  assert.strictEqual(it2.dimensions.described.main, 'W 1 x D 0.2 x H 2 MM', 'a different unit is kept');
  const cdoc = X.extract({ pages: [page(1, [
    [100, DH],
    [115, [[40, '1'], [70, 'Cell-unit panel'], [282, '1.3 m'], [311, '0.2 m'], [342, '0.9 m'], [380, '1'], [410, '300.00'], [490, '300.00']]],
    [127, [[70, 'SIZE: W 1.3 x D 0.2 x H 0.9 m']]],
  ])] });
  const it3 = X.planImport(cdoc, { items: [] }, Object.assign({ selectedRowIds: [cdoc.rows[0].id] }, baseOpts)).items[0];
  assert.deepStrictEqual(it3.dimensions.values, { W: '1.3 m', D: '0.2 m', H: '0.9 m' }, 'cells kept as printed');
  assert.strictEqual(it3.dimensions.unit, 'm');
  assert.strictEqual(it3.dimensions.status, 'as_printed', 'numbers compared without the unit');
});
test('description measurements: every component keeps its own measurements', () => {
  const plan = X.planImport(sign, { items: [] }, Object.assign({ selectedRowIds: sign.rows.filter(r => r.parent && r.parent.ref === '2').map(r => r.id) }, baseOpts));
  assert.deepStrictEqual(plan.items.map(i => i.dimensions.described.main), ['W 2 x H 1 M', '']);   // "M": a unit the columns lack
  assert.ok(plan.items.every(i => i.orig.parent === '2 Reception desk works'));
  // two components of one titled item, each with its OWN description-only part
  const compDoc = X.extract({ pages: [page(1, [
    [100, DH],
    [115, [[40, '7'], [70, 'Kiosk works']]],
    [127, dr('1', 'Counter', '2.00', '', '1.00', '1', '400.00', '400.00')],
    [139, [[70, 'SIZE: W 2 x H 1 M + PLINTH H 0.1 M']]],
    [151, dr('2', 'Canopy', '3.00', '', '0.40', '1', '600.00', '600.00')],
    [163, [[70, 'SIZE: W 3 x H 0.4 M + 2 POSTS OF 2.5 M']]],
  ])] });
  const cp = X.planImport(compDoc, { items: [] }, Object.assign({ selectedRowIds: compDoc.rows.map(r => r.id) }, baseOpts));
  assert.deepStrictEqual(cp.items.map(i => [i.orig.parent, i.dimensions.described.additional]),
    [['7 Kiosk works', ['PLINTH H 0.1 M']], ['7 Kiosk works', ['2 POSTS OF 2.5 M']]]);
  assert.deepStrictEqual(cp.items.map(i => i.dimensions.values.D), ['', ''], 'blank cells stay blank');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
