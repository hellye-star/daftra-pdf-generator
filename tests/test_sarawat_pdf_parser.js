/* Sarawat quotation PDF parser — regression tests.
   Run:  node tests/test_sarawat_pdf_parser.js

   Runs the REAL parser (extracted verbatim from daftra-pdf-generator_1.html) on REAL PDFs, with
   pdf.js doing the text extraction exactly as the browser does. Needs pdf.js for Node:
       npm install --prefix tests/.pdfjs pdfjs-dist@3.11.174        (or set PDFJS_DIST to its folder)
   PDFs are looked up in tests/fixtures/sarawat/ first, then in $SARAWAT_REGRESSION_DIR, then in
   ~/Downloads. A PDF that cannot be found is SKIPPED (not failed) so the suite runs anywhere;
   the primary fixture (Estimate 44035) is the one that must be present for a green "primary" run. */
'use strict';
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(process.env.SARAWAT_PARSER_HTML || path.join(ROOT, 'daftra-pdf-generator_1.html'), 'utf8');

// ── load pdf.js (Node build) ────────────────────────────────────────────────
function loadPdfjs() {
  const cands = [process.env.PDFJS_DIST, path.join(__dirname, '.pdfjs', 'node_modules', 'pdfjs-dist'),
    path.join(__dirname, 'node_modules', 'pdfjs-dist'), 'pdfjs-dist'].filter(Boolean);
  for (const c of cands) {
    for (const rel of ['legacy/build/pdf.js', 'build/pdf.js']) {
      try { return require(c.startsWith('.') || path.isAbsolute(c) ? path.join(c, rel) : c + '/' + rel); } catch (e) { /* try next */ }
    }
  }
  return null;
}
const _warn = console.warn; console.warn = () => {};   // pdf.js prints canvas/font warnings that do not matter for text extraction
const pdfjsLib = loadPdfjs();
console.warn = _warn;
if (!pdfjsLib) {
  console.log('SKIP all — pdf.js for Node not installed. Run: npm install --prefix tests/.pdfjs pdfjs-dist@3.11.174');
  process.exit(0);
}

// ── pull the parser region out of the page and run it in a sandbox ───────────
const a = HTML.indexOf('const SARAWAT_OCR_MIN_PAGE_CHARS');
const b = HTML.indexOf('// ── Excel file reading');
assert(a > 0 && b > a, 'parser region not found in daftra-pdf-generator_1.html');
const helperFns = ['sarawatRound2', 'sarawatMoney']
  .map(n => HTML.match(new RegExp('function ' + n + '\\(n\\) \\{[\\s\\S]*?\\n\\}\\n'))[0]).join('\n');
// pdf.js is told to stay quiet; everything else is the unmodified browser code
const quietPdfjs = { getDocument: o => pdfjsLib.getDocument(Object.assign({ verbosity: 0 }, o)) };
const sandbox = { pdfjsLib: quietPdfjs, console, Math, Number, String, Array, Object, JSON, RegExp, Set, Map, isNaN, parseFloat, parseInt };
vm.createContext(sandbox);
vm.runInContext(helperFns + '\n' + HTML.slice(a, b) + `
this.__api = { extractSarawatPdfData, parseSarawatHeaderFields, parseSarawatDocumentFromPdf };`, sandbox);
const P = sandbox.__api;

const DIRS = [path.join(__dirname, 'fixtures', 'sarawat'), process.env.SARAWAT_REGRESSION_DIR,
  path.join(os.homedir(), 'Downloads')].filter(Boolean);
const findPdf = name => { for (const d of DIRS) { const f = path.join(d, name); if (fs.existsSync(f)) return f; } return null; };

async function parse(file) {
  const buf = fs.readFileSync(file);
  const ex = await P.extractSarawatPdfData({ arrayBuffer: async () => buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) });
  const header = P.parseSarawatHeaderFields(ex.fullText);
  const result = P.parseSarawatDocumentFromPdf(ex.fullText, ex.pages);
  return { ex, header, result };
}

let passed = 0, failed = 0, skipped = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('ok   ' + name); }
  catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + (e && e.message)); }
}
// the parser runs in a vm context, so its objects have a foreign prototype — compare their JSON form
const eq = (got, want, msg) => assert.deepStrictEqual(JSON.parse(JSON.stringify(got)), want, msg);
const tier = it => it.tiers[0];

(async () => {
  // ══ PRIMARY: Estimate 44035 — Red Sea Global (3 items, 2 pages, combined column labels) ══
  const redSeaFile = findPdf('Printing Items Red Sea Gobal Quotation-13-10-2026.pdf');
  if (!redSeaFile) { skipped++; console.log('SKIP primary fixture "Printing Items Red Sea Gobal Quotation-13-10-2026.pdf" not found'); }
  else {
    const { header, result } = await parse(redSeaFile);
    const it = result.items;
    await test('44035: header — client, number, date, project, pages', () => {
      eq(header, { quoteNo: '44035', quoteDate: '10/12/2026', client: 'Red Sea Global', project: 'Owner Manual Printing + LH + Content Card', pages: '2' });
    });
    await test('44035: parsed with the S.No template (not the guessing parser), exactly 3 items', () => {
      assert.strictEqual(result.source, 'sarawat-template');
      assert.strictEqual(it.length, 3);
      assert.strictEqual(it.some(x => /vat|terms|riyals|regards|electronically/i.test(x.itemName + ' ' + x.description)), false);
    });
    await test('44035: item codes and names (name is NOT merged into the specification)', () => {
      eq(it.map(x => x.sno), ['01_01', '02_01', '03_01']);
      eq(it.map(x => x.itemName), ['Owner Manual Printing Book', 'LH A4', 'Content Card']);
      assert(!/Owner Manual Printing Book/.test(it[0].description));
    });
    await test('44035: quantities are numbers ("31 Copies" -> 31)', () => {
      eq(it.map(x => tier(x).qty), [31, 100, 31]);
    });
    await test('44035: stored unit prices ARE the printed ones (161.29 / 15 / 48.387 — never re-derived)', () => {
      eq(it.map(x => tier(x).supplierUnitPrice), [161.29, 15, 48.387]);
      eq(it.map(x => tier(x).source.unitPrice), [161.29, 15, 48.387]);
      eq(it.map(x => tier(x).source.priceDerived), [false, false, false]);
    });
    await test('44035: stored line totals ARE the printed ones (5,000.00 / 1,500.00 / 1,500.00)', () => {
      eq(it.map(x => tier(x).source.lineTotal), [5000, 1500, 1500]);
      eq(it.map(x => tier(x).total), [5000, 1500, 1500]);
      eq(it.map(x => tier(x).source.quantity), [31, 100, 31]);
    });
    await test('44035: 01_01 source line = 31 / 161.29 / 5000 / rounding / -0.01 (values untouched, discrepancy recorded)', () => {
      eq(tier(it[0]).source, { quantity: 31, unitPrice: 161.29, lineTotal: 5000, priceDerived: false,
        reconciliationStatus: 'rounding', reconciliationDifference: -0.01 });
      assert.strictEqual(it[0].qtyReview, false);            // quantity itself is not in doubt
      assert(/1 line\(s\) do not multiply out/.test(result.sheetNote), result.sheetNote);
    });
    await test('44035: lines that multiply out exactly are "matched" with no difference (02_01, 03_01 — 31 × 48.387 = 1,500.00)', () => {
      eq(tier(it[1]).source, { quantity: 100, unitPrice: 15, lineTotal: 1500, priceDerived: false, reconciliationStatus: 'matched', reconciliationDifference: 0 });
      eq(tier(it[2]).source, { quantity: 31, unitPrice: 48.387, lineTotal: 1500, priceDerived: false, reconciliationStatus: 'matched', reconciliationDifference: 0 });
    });
    await test('44035: the persisted metadata does not reuse sourceUnitPrice (that name drives the Excel currency conversion)', () => {
      it.forEach(x => assert.strictEqual(tier(x).sourceUnitPrice, undefined));
    });
    await test('44035: multi-section specification keeps its sections and lines', () => {
      eq(it[0].description, [
        'Wrapper', 'Finish Size: 21.00 X 29.70', 'Open Size: 42.00 X 29.70', 'No of Pages: 4', 'Colors: 4c x 4c',
        'Paper Type: Splendorlux 2HB Extra White, 240gsm', '',
        'Text', 'Finish Size: 21.00 X 29.70', 'Open Size: 42.00 X 29.70', 'No of Pages: 28', 'Colors: 4c x 4c',
        'Paper Type: Corolla Pentagram P.W., 120gsm', 'Job Finishing: Perfect Binding'].join('\n'));
      eq(it[1].description, [
        'Cover Section Details', 'Finish Size: 21.00 X 29.70', 'Open Size: 21.00 X 29.70', 'No of Pages: 2', 'Colors: 4c x 0c',
        'Paper Type: Fedrigoni Arena Natural Rough 140 gsm', 'Section Finishing: Gold Foiling'].join('\n'));
    });
    await test('44035: wrapped Paper Type ("140 gsm" / value printed above its label) is one field', () => {
      eq(it[2].description, [
        'Cover Section Details', 'Finish Size: 10.50 X 14.80', 'Open Size: 10.50 X 14.80', 'No of Pages: 2', 'Colors: 4c x 0c',
        'Paper Type: Fedrigoni Arena Natural Rough 140 gsm', 'Section Finishing: Gold Foiling'].join('\n'));
    });
    await test('44035: totals — subtotal 8,000 / VAT 1,200 (15%) / grand total 9,200, no fake VAT item', () => {
      eq(result.sourceTotals, { subtotal: 8000, vat: 1200, vatPct: 15, grandTotal: 9200, lineTotalsSum: 8000 });
      assert(/source subtotal SAR 8,000\.00 reconciled/.test(result.sheetNote), result.sheetNote);
    });
    await test('44035: nothing from the footer reaches the last item; page-2 header row is not content', () => {
      assert(!/Terms|Thanks|Regards|Electronically|Saudi|Total Price|Description/i.test(it[2].description + it[2].itemName));
    });
  }

  // ══ REGRESSION: documents that already parsed — their results must not change ══
  const summary = r => ({ source: r.result.source, n: r.result.items.length, codes: r.result.items.map(x => x.sno),
    lines: r.result.items.map(x => x.tiers.map(t => [t.qty, Math.round(t.total * 100) / 100])), header: [r.header.quoteNo, r.header.quoteDate, r.header.client] });
  const REG = [
    ['Printing Items Quotation.pdf', 'older multi-item S.No template (split header labels)', {
      source: 'sarawat-template', n: 4, codes: ['01_01', '02_01', '03_01', '04_01'],
      lines: [[[4500000, 51750]], [[500000, 40250]], [[1000, 3300.5]], [[2000, 1357]]], header: ['43463', '04/06/2026', 'QUDWA'] }],
    ['Poster A4 Quotation.pdf', 'single-item S.No template', {
      source: 'sarawat-template', n: 1, codes: ['01_01'], lines: [[[20000, 7590]]], header: ['43840', '08/05/2026', 'Vista United Co.'] }],
    ['Trifold Brochure Quotation.pdf', 'non-S.No document stays on the smart parser', { source: 'smart', n: 3 }],
    ['GSE_Quotation_VistaUnited.pdf', 'different table layout stays on the smart parser', { source: 'smart', n: 12 }],
    ['NIYYAH Production Quotation.pdf', 'free-text quotation stays on the fallback', { source: 'fallback-generic', n: 1 }],
    ['Request for Quotation (RFQ).pdf', 'RFQ stays on the fallback', { source: 'fallback-generic', n: 2 }],
  ];
  for (const [file, label, want] of REG) {
    const f = findPdf(file);
    if (!f) { skipped++; console.log('SKIP ' + file + ' — not found'); continue; }
    const r = await parse(f);
    await test('regression: ' + file + ' — ' + label, () => {
      const got = summary(r);
      for (const k of Object.keys(want)) eq(got[k], want[k], k + ' changed');
    });
  }

  // Ritz Carlton AMAALA collateral quotations (05_01 / 14_01 family): the item table has combined
  // "Total Quantity | Unit Cost | Total Cost" labels. Before the fix these fell through to the
  // guessing parser (20 nameless items, no prices); now every printed S.No becomes one item and the
  // item totals reconcile with the printed VAT / grand total.
  const RITZ = [
    ['The Ritz Carlton Amaala Collateral Items 1of2.pdf', { n: 19, no: '43761', sub: 118600, vat: 17790, grand: 136390,
      first: ['01_01', 'Anniversary Card with Envelope'], t16: ['16_01', 'Disposable Lunch Box'], missing: '09_01', flagged: [] }],
    ['The Ritz Carlton Amaala Collateral Items 2of2.pdf', { n: 19, no: '43766', sub: 147650, vat: 22147.5, grand: 169797.5,
      first: ['01_01', 'In Room Robe Card'], t16: ['16_01', 'Tray Removal Card'], missing: null, flagged: ['15_01:mismatch'] }],
  ];
  for (const [file, w] of RITZ) {
    const f = findPdf(file);
    if (!f) { skipped++; console.log('SKIP ' + file + ' — not found'); continue; }
    const { header, result } = await parse(f);
    await test('regression: ' + file + ' — one item per S.No, named, priced, reconciled', () => {
      assert.strictEqual(result.source, 'sarawat-template');
      assert.strictEqual(result.items.length, w.n);
      assert.strictEqual(header.quoteNo, w.no);
      const by = Object.fromEntries(result.items.map(x => [x.sno, x]));
      assert.strictEqual(by[w.first[0]].itemName, w.first[1]);
      assert.strictEqual(by[w.t16[0]].itemName, w.t16[1]);   // title printed a hair above its code
      if (w.missing) assert.strictEqual(by[w.missing], undefined);
      assert(result.items.every(x => x.itemName && x.tiers[0].qty > 0 && x.tiers[0].total > 0), 'every item has a name, quantity and total');
      assert.strictEqual(result.sourceTotals.subtotal, w.sub);
      assert.strictEqual(result.sourceTotals.vat, w.vat);
      assert.strictEqual(Math.round(result.items.reduce((s, x) => s + x.tiers[0].total, 0) * 100) / 100, w.sub);
      if (w.grand) assert.strictEqual(Math.round((w.sub + w.vat) * 100) / 100, w.grand);
    });
    await test('regression: ' + file + ' — printed values kept; only genuinely inconsistent lines are flagged', () => {
      const flagged = result.items.filter(x => ['rounding', 'mismatch'].includes(tier(x).source.reconciliationStatus)).map(x => x.sno + ':' + tier(x).source.reconciliationStatus);
      eq(flagged, w.flagged);
      result.items.forEach(x => assert.strictEqual(tier(x).supplierUnitPrice, tier(x).source.unitPrice));   // never re-derived
    });
  }

  console.log(`\n${passed} passed, ${failed} failed, ${skipped} skipped`);
  process.exit(failed ? 1 : 0);
})();
