/* ==========================================================================
   PO Generator — supplier quotation extraction (Phase 2)

   Pure parsing: positioned text (pdf.js text layer or local Tesseract OCR
   words) in → structured, evidence-carrying extraction out. No network,
   no DOM, no storage. Runs in the browser (window.POExtract) and in Node
   (module.exports) for tests.

   Patterns adapted from supplier-quotation-intelligence.html (line
   grouping by y, header-derived column bands, multi-line row accumulation,
   label/amount totals with cross-checks). SQI itself is not modified.

   Principles
   * Every value keeps its raw text, page, region (page units: PDF points
     for PDFs, pixels for images, top-left origin), method and confidence.
   * Nothing is guessed. Ambiguous or unreadable values keep value=null and
     carry flags; missing values are reported as missing.
   * Nothing is silently dropped: rows that are not clearly items
     (repeated headers, subtotals, carried-forward lines, unstructured
     numeric lines) are returned in `otherRows` with a reason.
   * Optional / alternative items are marked and NOT selected by default.
   * Amounts and quantities are returned as decimal strings.
   ========================================================================== */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.POExtract = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const VERSION = 'po-extract-5';
  const AR = /[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]/;
  const AR_G = /[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]/g;
  const LAT_G = /[A-Za-z]/g;
  const AR_LETTER = /[ء-يٮ-ٯٱ-ۓەۺ-ۿݐ-ݿﭐ-﷿ﹰ-ﻼ]/;
  const DIGIT_MAP = { '٠':'0','١':'1','٢':'2','٣':'3','٤':'4','٥':'5','٦':'6','٧':'7','٨':'8','٩':'9',
                      '۰':'0','۱':'1','۲':'2','۳':'3','۴':'4','۵':'5','۶':'6','۷':'7','۸':'8','۹':'9',
                      '٫':'.', '٬':',', '،':',', '٪':'%' };
  const LOW_CONF = 0.6;

  // ── text helpers ──────────────────────────────────────────────────────────
  function normDigits(s) { return String(s == null ? '' : s).replace(/[٠-٩۰-۹٫٬٪]/g, c => DIGIT_MAP[c]); }
  function normText(s) {
    return normDigits(s).toLowerCase()
      .replace(/[ً-ٰٟـ]/g, '')          // tashkeel + tatweel
      .replace(/[أإآ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي')
      .replace(/[:：]+\s*$/, '').replace(/\s+/g, ' ').trim();
  }
  const clean = s => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  const isRtl = s => ((s.match(AR_G) || []).length > (s.match(LAT_G) || []).length);

  // ── EF: evidence-carrying field ───────────────────────────────────────────
  function ef(value, o) {
    o = o || {};
    return { value: value, raw: o.raw != null ? String(o.raw) : (value == null ? '' : String(value)),
             page: o.page != null ? o.page : null, region: o.region || null, method: o.method || null,
             confidence: o.confidence != null ? Math.round(o.confidence * 100) / 100 : null,
             flags: (o.flags || []).slice() };
  }
  const missing = () => ({ value: null, raw: '', page: null, region: null, method: null, confidence: null, flags: ['missing'] });

  // ── numbers ───────────────────────────────────────────────────────────────
  // Returns { value: '1234.50' | null, flags: [] }. Never guesses between
  // decimal and thousands separators when the text is genuinely ambiguous.
  const CURRENCY_TOKENS = /(sar|s\.r\.?|sr|usd|us\$|eur|aed|gbp|qar|kwd|bhd|omr|egp|ر\.?\s?س\.?|ريال(\s*سعودي)?|\$|€|£)/gi;
  function parseAmount(raw) {
    const flags = [];
    let s = normDigits(raw).replace(CURRENCY_TOKENS, '').replace(/[\s ]/g, '');
    if (!s) return { value: null, flags: ['missing'] };
    if (/^[-–—−]+$/.test(s)) return { value: null, flags: ['dash_amount'] };      // "-" printed instead of an amount
    let neg = false;
    if (/^\(.*\)$/.test(s)) { neg = true; s = s.slice(1, -1); }
    if (/^[-–−]/.test(s)) { neg = true; s = s.replace(/^[-–−]/, ''); }
    s = s.replace(/[-–−]$/, '');
    let v = null;
    if (/^\d+(\.\d+)?$/.test(s)) {
      if (/^\d{1,3}\.\d{3}$/.test(s)) { flags.push('ambiguous_number'); }      // 1.500 = 1.5 or 1500?
      else v = s;
    } else if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) {
      v = s.replace(/,/g, '');
    } else if (/^\d{1,3}(\.\d{3})+(,\d+)?$/.test(s) || /^\d+,\d{1,2}$/.test(s)) {
      flags.push('ambiguous_number');                                          // 1.234,50 / 12,50
    } else {
      flags.push('unreadable_number');
    }
    if (v != null) {
      v = v.replace(/^0+(?=\d)/, '');
      if (neg) { v = '-' + v; flags.push('negative'); }
    }
    return { value: v, flags };
  }
  const toNum = v => (v == null || v === '' ? null : Number(v));
  const hasDigit = s => /\d/.test(normDigits(s));

  // Quantity: "10", "10 pcs", "2.5 m2", "LS", "Lump sum", "مقطوعية"
  const LUMP_RE = /^(l\.?s\.?|lump\s*-?\s*sum|lot|مقطوعيه|مقطوعية|مقطوع|اجمالي)$/i;
  function parseQty(raw) {
    const t = clean(normDigits(raw));
    if (!t) return { value: null, unit: '', flags: ['missing'] };
    if (LUMP_RE.test(t)) return { value: null, unit: t, flags: ['lump_sum'] };
    const m = t.match(/^([\d.,]+)\s*([^\d\s].{0,15})?$/);
    if (!m) return { value: null, unit: '', flags: ['unreadable_number'] };
    const a = parseAmount(m[1]);
    return { value: a.value, unit: m[2] ? m[2].trim() : '', flags: a.flags };
  }

  // Tax cell: "15%", "0%", "Exempt", "معفى", "15.00" (an amount, not a rate)
  function parseTaxCell(raw) {
    const t = clean(normDigits(raw));
    if (!t) return null;
    if (/(exempt|معفى|معفاة|معفي)/i.test(t)) return { treatment: 'exempt', rate: '', amount: null, text: t };
    if (/(zero[\s-]*rated|صفري|0\s*%$)/i.test(t) && !/[1-9]\d*\s*%/.test(t)) return { treatment: 'zero_rated', rate: '', amount: null, text: t };
    const pct = t.match(/(\d{1,2}(?:\.\d{1,2})?)\s*%/);
    if (pct) return { treatment: Number(pct[1]) === 0 ? 'zero_rated' : 'taxable', rate: Number(pct[1]) === 0 ? '' : pct[1], amount: null, text: t };
    const a = parseAmount(t);
    if (a.value != null) return { treatment: null, rate: '', amount: a.value, text: t };
    return { treatment: null, rate: '', amount: null, text: t, unreadable: true };
  }

  // ── dates ─────────────────────────────────────────────────────────────────
  const EN_MONTHS = { jan:1, january:1, feb:2, february:2, mar:3, march:3, apr:4, april:4, may:5, jun:6, june:6, jul:7, july:7,
                      aug:8, august:8, sep:9, sept:9, september:9, oct:10, october:10, nov:11, november:11, dec:12, december:12 };
  const AR_G_MONTHS = { 'يناير':1, 'فبراير':2, 'مارس':3, 'ابريل':4, 'أبريل':4, 'مايو':5, 'يونيو':6, 'يوليو':7, 'اغسطس':8, 'أغسطس':8,
                        'سبتمبر':9, 'اكتوبر':10, 'أكتوبر':10, 'نوفمبر':11, 'ديسمبر':12 };
  const AR_H_MONTHS = { 'محرم':1, 'صفر':2, 'ربيع الاول':3, 'ربيع الأول':3, 'ربيع الثاني':4, 'ربيع الاخر':4, 'ربيع الآخر':4,
                        'جمادى الاولى':5, 'جمادى الأولى':5, 'جمادى الاخرة':6, 'جمادى الآخرة':6, 'جمادى الثانية':6, 'رجب':7, 'شعبان':8,
                        'رمضان':9, 'شوال':10, 'ذو القعدة':11, 'ذي القعدة':11, 'ذو الحجة':12, 'ذي الحجة':12 };
  const pad = n => String(n).padStart(2, '0');
  const validYMD = (y, m, d) => m >= 1 && m <= 12 && d >= 1 && d <= 31;

  function calendarOf(y, hint) {
    if (hint) return hint;
    if (y >= 1300 && y <= 1600) return 'hijri';
    if (y >= 1900 && y <= 2200) return 'gregorian';
    return 'unknown';
  }
  function mkDate(raw, y, m, d, o) {
    o = o || {};
    const flags = (o.flags || []).slice();
    const cal = calendarOf(y, o.calendar);
    if (cal === 'hijri') flags.push('hijri_not_converted');
    if (cal === 'unknown') flags.push('calendar_unknown');
    const ok = validYMD(y, m, d) && !o.ambiguous;
    return { raw: raw, calendar: cal, year: y, month: ok ? m : null, day: ok ? d : null,
             iso: ok && cal === 'gregorian' ? y + '-' + pad(m) + '-' + pad(d) : null,
             hijri: ok && cal === 'hijri' ? y + '-' + pad(m) + '-' + pad(d) : null,
             ambiguous: !!o.ambiguous, alternatives: o.alternatives || [], flags: o.ambiguous ? flags.concat('ambiguous_date') : flags };
  }
  // Finds every date-looking token in a string; each keeps its original text.
  function parseDates(text) {
    const src = normDigits(text);
    const out = [];
    const hijriHint = /(هـ|ه(?![\u0600-\u06FF])|\bAH\b|\bH\b|هجري)/i.test(src);
    const gregHint = /((?<![\u0600-\u06FF])م(?![\u0600-\u06FF])|\bAD\b|ميلادي)/.test(src);
    let m;
    // ISO-like yyyy-mm-dd / yyyy/mm/dd
    const reY = /\b(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})\b/g;
    while ((m = reY.exec(src))) out.push({ at: m.index, d: mkDate(m[0], +m[1], +m[2], +m[3]) });
    // dd/mm/yyyy or mm/dd/yyyy (or 2-digit year)
    const reD = /\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})\b/g;
    while ((m = reD.exec(src))) {
      if (out.some(o => m.index >= o.at && m.index < o.at + o.d.raw.length)) continue;
      const a = +m[1], b = +m[2]; let y = +m[3];
      const flags = [];
      if (m[3].length === 2) { flags.push('two_digit_year'); }
      const after = src.slice(m.index + m[0].length, m.index + m[0].length + 4);
      const calHint = /^\s*(هـ|ه(?![\u0600-\u06FF])|AH\b|H\b)/i.test(after) ? 'hijri' : (/^\s*(م(?![\u0600-\u06FF])|AD\b)/.test(after) ? 'gregorian' : null);
      if (m[3].length === 2) { out.push({ at: m.index, d: mkDate(m[0], y, a, b, { ambiguous: true, flags, calendar: calHint || 'unknown' }) }); continue; }
      if (a > 12 && b <= 12) out.push({ at: m.index, d: mkDate(m[0], y, b, a, { flags, calendar: calHint }) });
      else if (b > 12 && a <= 12) out.push({ at: m.index, d: mkDate(m[0], y, a, b, { flags: flags.concat('month_first_format'), calendar: calHint }) });
      else if (a === b) out.push({ at: m.index, d: mkDate(m[0], y, b, a, { flags, calendar: calHint }) });
      else out.push({ at: m.index, d: mkDate(m[0], y, b, a, { ambiguous: true, flags, calendar: calHint,
        alternatives: [y + '-' + pad(b) + '-' + pad(a) + ' (day/month)', y + '-' + pad(a) + '-' + pad(b) + ' (month/day)'] }) });
    }
    // 20 Sep 2026 / Sep 20, 2026
    const reE1 = /\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})\.?[\s\-,]+(\d{4})\b/g;
    while ((m = reE1.exec(src))) { const mo = EN_MONTHS[m[2].toLowerCase()]; if (mo) out.push({ at: m.index, d: mkDate(m[0], +m[3], mo, +m[1], { calendar: 'gregorian' }) }); }
    const reE2 = /\b([A-Za-z]{3,9})\.?[\s\-]+(\d{1,2})(?:st|nd|rd|th)?,?[\s\-]+(\d{4})\b/g;
    while ((m = reE2.exec(src))) { const mo = EN_MONTHS[m[1].toLowerCase()]; if (mo) out.push({ at: m.index, d: mkDate(m[0], +m[3], mo, +m[2], { calendar: 'gregorian' }) }); }
    // Arabic month names (Gregorian or Hijri)
    for (const [name, mo] of Object.entries(AR_H_MONTHS)) {
      const re = new RegExp('(\\d{1,2})\\s*' + name + '\\s*(\\d{4})', 'g');
      while ((m = re.exec(src))) out.push({ at: m.index, d: mkDate(m[0], +m[2], mo, +m[1], { calendar: 'hijri' }) });
    }
    for (const [name, mo] of Object.entries(AR_G_MONTHS)) {
      const re = new RegExp('(\\d{1,2})\\s*' + name + '\\s*(\\d{4})', 'g');
      while ((m = re.exec(src))) out.push({ at: m.index, d: mkDate(m[0], +m[2], mo, +m[1], { calendar: 'gregorian' }) });
    }
    out.sort((x, y) => x.at - y.at);
    // A document-level Hijri/Gregorian marker only annotates numeric dates whose year range is unknown.
    return out.map(o => {
      if (o.d.calendar === 'unknown' && hijriHint && !gregHint) o.d.flags.push('calendar_hint_hijri');
      return o.d;
    });
  }

  // ── layout: items → lines → cells ─────────────────────────────────────────
  // Join positioned text items into reading-order text. Items that touch
  // (glyph-per-item PDFs, kerning splits) join without a space; visible gaps
  // become one space. RTL lines read right→left, but Latin/digit runs inside
  // them keep their left→right order.
  // OCR returns whole words (they carry a confidence), so OCR items never glue
  // together; PDF text items may be single glyphs and join when they touch.
  function touches(prev, it) {
    if (prev.conf != null || it.conf != null) return false;
    return it.x - (prev.x + prev.w) <= Math.max(0.8, Math.min(prev.h, it.h) * 0.18);
  }
  function joinItems(items, rtl) {
    const asc = items.slice().sort((a, b) => a.x - b.x);
    const groups = [];
    let g = null;
    for (const it of asc) {
      if (g && touches(g[g.length - 1], it)) { g.push(it); continue; }
      g = [it]; groups.push(g);
    }
    const groupText = grp => {
      if (!rtl) return grp.map(i => i.str).join('');
      const units = [];
      let u = null;
      for (const it of grp) {
        const ar = AR_LETTER.test(it.str);          // Arabic letters are RTL; digits (incl. Arabic-Indic) and Latin are LTR runs
        if (u && u.ar === ar) u.items.push(it); else { u = { ar, items: [it] }; units.push(u); }
      }
      // a percent sign drawn left of its digits in RTL text belongs after them ("٥٠٪")
      return units.reverse().map(x => x.ar ? x.items.slice().reverse().map(i => i.str).join('')
        : x.items.map(i => i.str).join('').replace(/^([%٪])(.+)$/, '$2$1')).join('');
    };
    const texts = groups.map(groupText);
    if (!rtl) return clean(texts.join(' '));
    // In RTL lines, adjacent Latin/number word groups form one left-to-right
    // phrase ("Q-2026 118") only when close together; separate cells stay separate.
    const units = [];
    let u = null;
    groups.forEach((grp, idx) => {
      const ar = grp.some(i => AR_LETTER.test(i.str));
      const prev = idx > 0 ? groups[idx - 1] : null;
      const near = prev && (grp[0].x - (prev[prev.length - 1].x + prev[prev.length - 1].w)) <= Math.max(4, grp[0].h * 1.2);
      if (u && !ar && !u.ar && near) u.idx.push(idx); else { u = { ar, idx: [idx] }; units.push(u); }
    });
    return clean(units.reverse().map(x => x.idx.map(i => texts[i]).join(' ')).join(' '));
  }

  function buildLines(items) {
    // NFKC turns Arabic presentation forms / ligatures (common in generated PDFs)
    // back into ordinary letters; Persian look-alike letters that such PDFs
    // map Arabic glyphs to are normalised so keywords and labels match.
    const sorted = items.filter(it => it && clean(it.str)).map(it => ({
      // invisible bidi control marks (OCR output inserts LRM/RLM) carry no content
      str: String(it.str).normalize('NFKC').replace(/[‎‏؜‪-‮⁦-⁩]/g, '')
        .replace(/ی/g, 'ي').replace(/ک/g, 'ك').replace(/ھ/g, 'ه'), x: +it.x || 0, y: +it.y || 0, w: +it.w || Math.max(4, String(it.str).length * 5),
      h: +it.h || 10, conf: it.conf != null ? +it.conf : null,
      src: it.src || (it.conf != null ? 'ocr' : 'text'),          // evidence source of THIS item
    })).sort((a, b) => (a.y + a.h / 2) - (b.y + b.h / 2) || a.x - b.x);
    const lines = [];
    for (const it of sorted) {
      const yc = it.y + it.h / 2;
      const tol = Math.max(2.5, it.h * 0.5);
      let line = null;
      for (let i = lines.length - 1; i >= 0 && i >= lines.length - 4; i--) {
        if (Math.abs(lines[i].yc - yc) <= tol) { line = lines[i]; break; }
      }
      if (!line) { line = { yc, items: [] }; lines.push(line); }
      line.items.push(it);
      line.yc = line.items.reduce((s, x) => s + x.y + x.h / 2, 0) / line.items.length;
    }
    for (const l of lines) {
      l.items.sort((a, b) => a.x - b.x);
      const raw = l.items.map(i => i.str).join(' ');
      l.rtl = isRtl(raw);
      l.text = joinItems(l.items, l.rtl);
      l.x = Math.min(...l.items.map(i => i.x));
      l.y = Math.min(...l.items.map(i => i.y));
      l.w = Math.max(...l.items.map(i => i.x + i.w)) - l.x;
      l.h = Math.max(...l.items.map(i => i.y + i.h)) - l.y;
      const confs = l.items.map(i => i.conf).filter(c => c != null);
      l.minConf = confs.length ? Math.min(...confs) : null;
      l.cells = toCells(l.items, l.rtl);
    }
    lines.sort((a, b) => a.y - b.y);
    return lines;
  }
  function toCells(items, rtl) {
    if (!items.length) return [];
    const avgH = items.reduce((s, i) => s + i.h, 0) / items.length;
    const gap = Math.max(8, avgH * 1.3);
    const cells = [];
    let cur = null;
    for (const it of items) {
      if (cur && it.x - (cur.x + cur.w) <= gap) { cur.items.push(it); cur.w = Math.max(cur.x + cur.w, it.x + it.w) - cur.x; }
      else { cur = { x: it.x, y: it.y, w: it.w, h: it.h, items: [it] }; cells.push(cur); }
    }
    for (const c of cells) c.text = joinItems(c.items, isRtl(c.items.map(i => i.str).join(' ')));
    return cells;
  }
  const regionOf = parts => {
    if (!parts.length) return null;
    const x = Math.min(...parts.map(p => p.x)), y = Math.min(...parts.map(p => p.y));
    return { x: round1(x), y: round1(y), w: round1(Math.max(...parts.map(p => p.x + p.w)) - x), h: round1(Math.max(...parts.map(p => p.y + p.h)) - y) };
  };
  const round1 = v => Math.round(v * 10) / 10;

  // ── table header detection ────────────────────────────────────────────────
  const HEADER_KEYWORDS = {
    ref: ['#', 'no', 'no.', 'sn', 's.no', 's/n', 'sr', 'sr.', 'sl', 'item no', 'item no.', 'item #', 'code', 'item code', 'ref', 'ref.', 'م', 'رقم', 'الرقم', 'ت', 'كود', 'الكود', 'رقم البند'],
    description: ['description', 'item description', 'items', 'item', 'details', 'specification', 'specifications', 'particulars', 'scope', 'scope of work',
                  'الوصف', 'البيان', 'الصنف', 'المواصفات', 'التفاصيل', 'وصف', 'البند', 'الاصناف'],
    unit: ['unit', 'uom', 'u/m', 'units', 'الوحده', 'وحده'],
    qty: ['qty', 'qty.', 'quantity', 'qnty', 'quan', 'الكميه', 'العدد', 'كميه'],
    unitPrice: ['unit price', 'price', 'rate', 'u.price', 'u. price', 'unit rate', 'price/unit', 'unit cost', 'سعر الوحده', 'السعر', 'سعر', 'الفئه', 'سعر الافرادي', 'السعر الافرادي'],
    discount: ['discount', 'disc', 'disc.', 'disc %', 'الخصم', 'خصم'],
    tax: ['vat', 'vat %', 'vat%', 'tax', 'vat amount', 'tax %', 'الضريبه', 'ضريبه', 'ضريبه القيمه المضافه', 'القيمه المضافه'],
    lineTotal: ['total', 'amount', 'line total', 'total price', 'total amount', 'net amount', 'net', 'ext. price', 'extended', 'sub total',
                'الاجمالي', 'المبلغ', 'المجموع', 'القيمه', 'الاجمالي بدون ضريبه', 'اجمالي'],
    // dimension columns — never quantities
    dimW: ['w', 'width', 'العرض', 'عرض'],
    dimD: ['d', 'depth', 'العمق', 'عمق'],
    dimH: ['h', 'height', 'الارتفاع', 'ارتفاع'],
    dimL: ['l', 'length', 'الطول', 'طول'],
  };
  const DIM_KEYS = ['dimW', 'dimD', 'dimH', 'dimL'];
  function matchHeaderKey(text) {
    const t = normText(text).replace(/\s*\((mm|cm|m|mtr|in|ft|مم|سم|م)\)\s*$/i, '')   // "W (M)": a printed unit, not part of the label
      .replace(/[()]/g, '').replace(/\s*\(?(sar|sr|ر\.?س\.?)\)?\s*$/i, '').trim();
    if (!t) return null;
    // "QTY/SQM", "Unit/Rate": each part is tried too
    const cands = [t].concat(t.includes('/') || t.includes('\\') ? t.split(/[\/\\]/).map(s => s.trim()).filter(Boolean) : []);
    let best = null, score = 0;
    for (const c of cands) {
      for (const [key, words] of Object.entries(HEADER_KEYWORDS)) {
        for (const w of words) {
          let s = 0;
          if (c === w) s = w.length + 20 - (c === t ? 0 : 1);
          else if (w.length > 2 && (c.startsWith(w + ' ') || c.endsWith(' ' + w) || c.includes(' ' + w + ' ') || (w.length > 4 && c.includes(w)))) s = w.length;
          if (s > score) { score = s; best = key; }
        }
      }
    }
    return best;
  }
  // Header lines are segmented into known column phrases word by word (longest
  // phrase first, in reading order), so tightly spaced labels such as
  // "Qty  Unit Price  Total" never merge into one cell.
  const MULTI_WORD_KEYS = Object.entries(HEADER_KEYWORDS).flatMap(([k, ws]) => ws.filter(w => w.includes(' ')).map(w => [w, k]));
  function headerPhrases(line) {
    const words = wordGroups(line.items);
    const ordered = line.rtl ? words.slice().reverse() : words;
    const out = [];
    let i = 0;
    while (i < ordered.length) {
      let taken = 0, key = null;
      for (let L = Math.min(4, ordered.length - i); L >= 2 && !taken; L--) {
        const t = normText(joinItems(ordered.slice(i, i + L).flat(), line.rtl)).replace(/[()]/g, '').trim();
        const hit = MULTI_WORD_KEYS.find(([w]) => w === t);
        if (hit) { key = hit[1]; taken = L; }
      }
      if (!taken) { taken = 1; key = matchHeaderKey(joinItems(ordered[i], line.rtl)); }
      const grp = ordered.slice(i, i + taken).flat();
      const r = regionOf(grp);
      out.push({ key, x: r.x, y: r.y, w: r.w, h: r.h, text: joinItems(grp, line.rtl) });
      i += taken;
    }
    return out;
  }
  // a unit printed with a dimension label: "W (M)", "H(mm)" or "W" followed by "(cm)"
  const UNIT_RE = /\((mm|cm|m|mtr|in|ft|مم|سم|م)\)/i;
  function headerInfo(line) {
    const keys = new Map();
    const ph = headerPhrases(line);
    ph.forEach((p, i) => {
      if (!p.key || keys.has(p.key)) return;
      if (DIM_KEYS.includes(p.key)) {
        const own = String(p.text || '').match(UNIT_RE);
        const next = ph[i + 1];
        const nextUnit = next && !next.key && next.x - (p.x + p.w) < 12 ? String(next.text || '').trim().match(new RegExp('^' + UNIT_RE.source + '$', 'i')) : null;
        if (own || nextUnit) p.unit = (own || nextUnit)[1];
      }
      keys.set(p.key, p);
    });
    return keys;
  }
  function isHeaderLine(line) {
    const keys = headerInfo(line);
    const numericKeys = ['qty', 'unitPrice', 'lineTotal'].filter(k => keys.has(k)).length;
    const hasNumber = line.cells.some(c => /\d/.test(normDigits(c.text)) && !matchHeaderKey(c.text));
    return keys.size >= 3 && keys.has('description') && numericKeys >= 1 && !hasNumber ? keys : null;
  }
  function buildColumns(keys) {
    const cols = [...keys.entries()].map(([key, c]) => ({ key, xc: c.x + c.w / 2, x0: c.x, x1: c.x + c.w, unit: c.unit || '' }));
    cols.sort((a, b) => a.xc - b.xc);
    cols.forEach((c, i) => {
      c.band0 = i === 0 ? -Infinity : (cols[i - 1].xc + c.xc) / 2;
      c.band1 = i === cols.length - 1 ? Infinity : (c.xc + cols[i + 1].xc) / 2;
    });
    // The description column is usually much wider than its label: widen
    // its band toward the neighbouring columns' labels (not past them).
    const d = cols.findIndex(c => c.key === 'description');
    if (d >= 0) {
      if (d > 0) cols[d].band0 = Math.min(cols[d].band0, cols[d - 1].x1 + 2);
      if (d < cols.length - 1) cols[d].band1 = Math.max(cols[d].band1, cols[d + 1].x0 - 2);
      if (d > 0) cols[d - 1].band1 = cols[d].band0;
      if (d < cols.length - 1) cols[d + 1].band0 = cols[d].band1;
    }
    return cols;
  }
  const colOf = (it, cols) => { const xc = it.x + it.w / 2; return cols.find(c => xc >= c.band0 && xc < c.band1) || null; };
  // Touching items (one word, possibly one item per glyph) go to ONE column,
  // chosen by the word's centre — a word is never split across columns.
  function wordGroups(items) {
    const asc = items.slice().sort((a, b) => a.x - b.x);
    const out = [];
    let g = null;
    for (const it of asc) {
      if (g && touches(g[g.length - 1], it)) { g.push(it); continue; }
      g = [it]; out.push(g);
    }
    return out;
  }
  const NUMERIC_KEYS = ['qty', 'unitPrice', 'discount', 'tax', 'lineTotal'];
  const VALUE_KEYS = NUMERIC_KEYS.concat(DIM_KEYS);           // columns holding numbers
  const NUMERIC_WORD = /^[\s(]*[-–−]?[\d٠-٩][\d٠-٩,.٫٬]*[)%٪]?\s*$/;
  const DASH_WORD = /^[-–—−]+$/;
  const CURRENCY_WORD = /^(sar|sr|s\.r\.?|usd|aed|eur|gbp|qar|kwd|bhd|omr|egp|ر\.?\s?س\.?|ريال)$/i;
  const groupText = g => normDigits(g.map(i => i.str).join(''));
  const groupBox = g => { const x0 = Math.min(...g.map(i => i.x)), x1 = Math.max(...g.map(i => i.x + i.w)); return { x0, x1, xc: (x0 + x1) / 2 }; };

  // Column anchors from the DATA: numbers in one column share an alignment
  // edge. Right edges, centres and left edges of numeric words (within the
  // numeric part of the table) are clustered; if one metric yields exactly
  // one cluster per numeric column, those clusters become the columns'
  // anchors (in visual order — columns never swap order).
  function computeAnchors(lines, cols) {
    const numCols = cols.filter(c => VALUE_KEYS.includes(c.key));
    if (numCols.length < 2) return null;
    const minX = Math.min(...numCols.map(c => c.band0 === -Infinity ? c.x0 - 30 : c.band0));
    const maxX = Math.max(...numCols.map(c => c.band1 === Infinity ? c.x1 + 60 : c.band1));
    const boxes = [];
    for (const l of lines) {
      if (!l.text || l.furniture) continue;
      for (const g of wordGroups(l.items)) {
        const t = groupText(g);
        if (!NUMERIC_WORD.test(t)) continue;
        const b = groupBox(g);
        if (b.xc >= minX && b.xc <= maxX) boxes.push(b);
      }
    }
    for (const m of ['x1', 'xc', 'x0']) {
      const vals = boxes.map(b => b[m]).sort((a, b) => a - b);
      const clusters = [];
      for (const v of vals) {
        const c = clusters[clusters.length - 1];
        if (c && v - c.last <= 4) { c.sum += v; c.n++; c.last = v; } else clusters.push({ sum: v, n: 1, last: v });
      }
      const strong = clusters.filter(c => c.n >= 2);
      if (strong.length === numCols.length) return { metric: m, cols: numCols.map((c, i) => ({ key: c.key, at: strong[i].sum / strong[i].n })) };
    }
    return null;
  }

  function assignColumns(items, cols) {
    const byCol = {};
    const placed = [];
    const anchors = cols.anchors;
    for (const g of wordGroups(items)) {
      const t = groupText(g);
      if (CURRENCY_WORD.test(t.trim())) continue;                // "SAR 1,200.00": the currency word is not content
      const b = groupBox(g);
      let c = colOf({ x: b.x0, w: b.x1 - b.x0 }, cols);
      if (anchors && (NUMERIC_WORD.test(t) || DASH_WORD.test(t)) && c && VALUE_KEYS.includes(c.key)) {
        let best = null, dist = Infinity;
        for (const a of anchors.cols) { const d = Math.abs(b[anchors.metric] - a.at); if (d < dist) { dist = d; best = a; } }
        if (best && dist <= 30) c = cols.find(x => x.key === best.key) || c;
      }
      if (c) { (byCol[c.key] = byCol[c.key] || []).push(...g); placed.push({ g, c, x0: b.x0 }); }
    }
    if (anchors) return byCol;
    // No anchors: when a row has exactly one numeric word per numeric column,
    // map them in visual order (labels are often left-aligned while numbers
    // are right-aligned). Anything else keeps the band assignment.
    const numCols = cols.filter(c => VALUE_KEYS.includes(c.key));
    const numWords = placed.filter(p => VALUE_KEYS.includes(p.c.key) && NUMERIC_WORD.test(groupText(p.g)));
    const others = placed.filter(p => VALUE_KEYS.includes(p.c.key) && !numWords.includes(p));
    if (numCols.length > 1 && numWords.length === numCols.length && !others.length) {
      for (const k of VALUE_KEYS) delete byCol[k];
      numWords.sort((a, b) => a.x0 - b.x0).forEach((p, i) => { byCol[numCols[i].key] = p.g.slice(); });
    }
    return byCol;
  }

  // ── row / line classification ────────────────────────────────────────────
  const TOTAL_LABELS = [
    { kind: 'carried_forward', re: /(carried\s*(forward|fwd|over)|brought\s*(forward|fwd)|\bc\/f\b|\bb\/f\b|balance\s*(b\/f|c\/f|forward)|مرحل|منقول|ما\s*قبله|ماقبله|المرحل)/i },
    { kind: 'grand_total', re: /(grand\s*total|total\s*(amount\s*)?(incl(uding|\.)?|with)\s*(vat|tax)|net\s*total|total\s*due|amount\s*due|total\s*payable|الاجمالي\s*(شامل|مع|بعد)\s*(ال)?ضريب|الإجمالي\s*(شامل|مع|بعد)\s*(ال)?ضريب|الاجمالي\s*النهائي|الإجمالي\s*النهائي|المبلغ\s*الاجمالي|المبلغ\s*الإجمالي|الصافي|صافي\s*المبلغ)/i },
    { kind: 'subtotal', re: /(sub\s*-?\s*total|total\s*(before|excl(uding|\.)?|without)\s*(vat|tax)|المجموع\s*الفرعي|الاجمالي\s*(قبل|بدون)\s*(ال)?ضريب|الإجمالي\s*(قبل|بدون)\s*(ال)?ضريب|المجموع\s*قبل)/i },
    { kind: 'vat', re: /(\bvat\b|\btax\b|value\s*added|ضريبة\s*القيمة\s*المضافة|ضريبه\s*القيمه\s*المضافه|الضريبة|الضريبه)/i },
    { kind: 'discount', re: /(\bdiscount\b|\bdisc\.?\b|الخصم|خصم)/i },
    { kind: 'delivery', re: /(delivery\s*(charges?|fees?|cost)?|shipping|freight|transport(ation)?|التوصيل|الشحن|النقل)/i },
    { kind: 'installation', re: /(installation\s*(charges?|fees?|cost)|تكلفة\s*التركيب|رسوم\s*التركيب)/i },
    { kind: 'total', re: /^(total\b|(الاجمالي|الإجمالي|المجموع|الاجمالى|اجمالي|إجمالي)(?![\u0600-\u06FF]))|(\btotal\b\s*[:\-]?\s*$)/i },
  ];
  const REG_NUMBER_LINE = /(vat\s*(reg(istration)?\s*)?(no\.?|number|#)|tax\s*(reg(istration)?\s*)?(no\.?|number|#|id)|\btrn\b|c\.?r\.?\s*(no\.?|#|number)|commercial\s*reg|الرقم\s*الضريبي|رقم\s*التسجيل\s*الضريبي|السجل\s*التجاري|س\.?\s*ت(?![\u0600-\u06FF])|\bvat\s*[:#]?\s*(\d[\s-]?){10,}|\bc\.?r\.?\s*[:#]?\s*(\d[\s-]?){7,}|\biban\b|\ba\/c\s*no|\bbank\s*:)/i;
  const END_MARKER_CELL = /^[\s\-–—=_*.]*(end|نهاية)?[\s\-–—=_*.]*$/i;
  const OPTIONAL_RE = /(\boptional\b|\boption\s*\d*\b|\(opt\.?\)|اختياري|اختيارية|خيار(?![\u0600-\u06FF]))/i;
  const ALTERNATIVE_RE = /(\balternative\b|\balt\.?\s*\d*\b|\bor\s*equivalent\s*alternative|بديل|البديل)/i;
  const PAGE_FOOTER_RE = /^(page\s*\d+(\s*(of|\/)\s*\d+)?|\d+\s*\/\s*\d+|صفحة\s*\d+(\s*(من|\/)\s*\d+)?|-\s*\d+\s*-)$/i;

  function totalKind(text) {
    if (REG_NUMBER_LINE.test(text)) return null;
    for (const t of TOTAL_LABELS) if (t.re.test(text)) return t.kind;
    return null;
  }
  // last money-looking amount in a string (skips percentages and long IDs)
  function lastAmount(text) {
    const s = normDigits(text);
    const toks = [...s.matchAll(/\(?-?\d[\d,.]*\d\)?|\b\d\b/g)]
      .filter(m => !/^\s*%/.test(s.slice(m.index + m[0].length)))
      .map(m => m[0])
      .filter(t => t.replace(/\D/g, '').length < 12);
    if (!toks.length) return null;
    return toks[toks.length - 1];
  }

  // ── main entry ────────────────────────────────────────────────────────────
  /**
   * extract({ pages: [{ page, width, height, method: 'text'|'ocr'|'none',
   *                     items: [{ str, x, y, w, h, conf? }] }] })
   */
  function extract(input) {
    const pagesIn = (input && input.pages) || [];
    const warnings = [];
    // Whole-document coverage: a partial read is reported, never presented as complete.
    const docIn = (input && input.document) || {};
    const processed = pagesIn.map(p => p.page);
    const totalPages = Math.max(docIn.totalPages || 0, processed.length ? Math.max(...processed) : 0);
    const document = { totalPages, processedPages: processed, complete: processed.length === totalPages && totalPages > 0 };
    if (!document.complete && totalPages) warnings.push({ code: 'document_incomplete', page: null,
      message: `INCOMPLETE: only ${processed.length} of ${totalPages} pages were read (${rangeText(processed)}). Items, totals and terms on the other pages are missing from this extraction.` });
    const pageMeta = [];
    const pageLines = [];
    for (const p of pagesIn) {
      let lines = [];
      try { lines = buildLines(p.items || []); }
      catch (e) { warnings.push({ code: 'page_layout_failed', page: p.page, message: 'Layout could not be reconstructed on this page: ' + (e && e.message) }); }
      const chars = lines.reduce((s, l) => s + l.text.replace(/\s/g, '').length, 0);
      const confs = (p.items || []).map(i => i.conf).filter(c => c != null);
      const avg = confs.length ? confs.reduce((a, b) => a + b, 0) / confs.length : null;
      const low = confs.filter(c => c < LOW_CONF).length;
      pageMeta.push({ page: p.page, width: p.width, height: p.height, method: p.method || 'text', charCount: chars,
                      avgConfidence: avg != null ? Math.round(avg * 100) / 100 : null, lowConfidenceWords: low, lineCount: lines.length });
      if (p.method === 'none' || chars === 0) warnings.push({ code: 'page_unreadable', page: p.page, message: 'No readable text on page ' + p.page + ' — check it against the original.' });
      else if (p.method === 'ocr' && avg != null && avg < 0.7) warnings.push({ code: 'page_low_ocr_confidence', page: p.page, message: 'OCR confidence on page ' + p.page + ' is low (' + Math.round(avg * 100) + '%). Verify values against the original.' });
      pageLines.push({ page: p.page, method: p.method || 'text', lines, height: p.height || null });
    }
    markPageFurniture(pageLines);

    const table = extractTable(pageLines, warnings);
    const header = extractHeader(pageLines, table, docIn.fileName);
    if (header.quotationDate.flags && header.quotationDate.flags.includes('filename_date_differs'))
      warnings.push({ code: 'filename_date_differs', page: header.quotationDate.page, message: 'The file name contains a date (' + header.quotationDate.fileNameDate +
        ') that differs from the date printed on the quotation (' + header.quotationDate.raw + '). The printed date is used; the file name is ignored.' });
    const totals = extractTotals(pageLines, table);
    const tax = extractTaxStatement(pageLines);

    // cross-document flags
    markDuplicates(table.rows);
    // item photos, when the page image placements were supplied
    const photos = pagesIn.some(p => Array.isArray(p.images)) ? locatePhotos(pagesIn, pageLines, table) : [];
    if (!table.rows.length) warnings.push({ code: 'no_items_detected', page: null, message: 'No item table was detected. Unstructured numeric lines are listed under “Other rows” for manual review.' });

    return {
      version: VERSION,
      document,
      pages: pageMeta,
      header: Object.assign(header, { taxBasis: tax.basis, taxRate: tax.rate }),
      rows: table.rows,
      otherRows: table.otherRows,
      photos,
      totals,
      warnings: warnings.concat(tax.warnings),
      pageText: pageLines.map(pl => ({ page: pl.page, method: pl.method,
        lines: pl.lines.map(l => ({ text: l.text, region: regionOf([l]), method: srcOf(l.items),
                                    conf: l.minConf != null ? Math.round(l.minConf * 100) / 100 : null })) })),
    };
  }
  // Letterhead / footer furniture: the same text in the top or bottom band of
  // two or more pages (company banner, address + VAT/CR footer). Such lines are
  // never item rows; header/totals extraction still sees them.
  function markPageFurniture(pageLines) {
    if (pageLines.length < 2) return;
    // same text at (about) the same height; lines carrying money amounts are never furniture
    const key = l => normText(l.text).replace(/\d+/g, '#') + '@' + Math.round(l.y / 6);
    const MONEY = /\d[\d,]*[.,]\d{2}(?!\d)/;
    const inBand = (l, h) => h && (l.y < h * 0.18 || l.y > h * 0.85) && !MONEY.test(normDigits(l.text));
    const pagesByKey = new Map();
    for (const pl of pageLines) for (const l of pl.lines) {
      if (!l.text || !inBand(l, pl.height)) continue;
      const k = key(l);
      if (k.length < 3) continue;
      if (!pagesByKey.has(k)) pagesByKey.set(k, new Set());
      pagesByKey.get(k).add(pl.page);
    }
    for (const pl of pageLines) for (const l of pl.lines)
      if (l.text && inBand(l, pl.height) && (pagesByKey.get(key(l)) || new Set()).size >= 2) l.furniture = true;
  }
  // 'text' | 'ocr' | 'mixed' for a set of items (evidence stays distinguishable)
  function srcOf(items) {
    const s = new Set((items || []).map(i => i.src || (i.conf != null ? 'ocr' : 'text')));
    return s.size > 1 ? 'mixed' : (s.values().next().value || null);
  }
  function rangeText(pages) {
    if (!pages.length) return 'none';
    const out = [];
    let a = pages[0], b = pages[0];
    for (const p of pages.slice(1).concat([null])) {
      if (p === b + 1) { b = p; continue; }
      out.push(a === b ? String(a) : a + '–' + b);
      a = b = p;
    }
    return 'pages ' + out.join(', ');
  }

  // ── table rows ────────────────────────────────────────────────────────────
  function extractTable(pageLines, warnings) {
    const rows = [], otherRows = [];
    const usedLineIds = new Set();
    const itemLineIds = new Set();      // lines that belong to item rows (never header/terms evidence)
    let cols = null, section = null, headerSeen = false, tableClosed = false;
    let open = null;      // row currently accumulating
    let rowN = 0, otherN = 0;
    const pushOther = (kind, line, pageNo, reason, extra) => {
      otherRows.push(Object.assign({ id: 'o' + (++otherN), kind, page: pageNo, text: line.text, region: regionOf([line]), reason, method: srcOf(line.items),
        amount: null, flags: line.minConf != null && line.minConf < LOW_CONF ? ['low_confidence'] : [] }, extra || {}));
      usedLineIds.add(line);
    };
    const finish = () => { if (open) { rows.push(finalizeRow(open, cols ? cols.filter(c => DIM_KEYS.includes(c.key)) : [])); open = null; } };

    // Reference-cell semantics: a bullet ("-", "•") marks a continuation line,
    // a number / code / "#" starts a row. Sub-numbers below a titled item
    // (1, 2, 3 under item 11; 11.1 …) are that item's priced components.
    const BULLET = /^[-–—•*·]+$/;
    const realRef = t => !!t && !BULLET.test(t.trim());
    const isSubRef = (ref, parentRef) => {
      const r = String(ref || '').trim(), p = String(parentRef || '').trim();
      if (/^\d+$/.test(r) && /^\d+$/.test(p)) return Number(r) < Number(p);
      const m = r.match(/^(\d+)[.\-](\d+)$/);
      if (m) return m[1] === p;
      return /^[a-z]\)?$/i.test(r);
    };
    // the next component number (…, 2 → 3) continues the components, unless it is also the
    // parent's own successor (heading 3 with components 1, 2, 3 → a following "4" is the next item)
    const nextInSequence = (ref, g) => {
      const r = String(ref || '').trim();
      return /^\d+$/.test(r) && /^\d+$/.test(g.lastRef || '') && /^\d+$/.test(g.ref) &&
        Number(r) === Number(g.lastRef) + 1 && Number(r) !== Number(g.ref) + 1;
    };
    let group = null;     // current titled item: { ref, title, heading }

    for (const pl of pageLines) {
      const method = pl.method;
      const lines = pl.lines;
      let lastTableY = null, lastTableH = 10;
      // A table carried over from the previous page without a repeated header
      // resumes at the first line that fits the table (a priced line, or an
      // item line with its own reference), so letterhead is never attached.
      let awaitingResume = !!(cols && !tableClosed), skippedBeforeResume = 0;
      if (awaitingResume) cols.anchors = computeAnchors(lines, cols) || cols.anchors;
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (!line.text) continue;
        if (PAGE_FOOTER_RE.test(normText(line.text))) { usedLineIds.add(line); continue; }
        const hk = isHeaderLine(line);
        if (hk) {
          finish();
          if (headerSeen) pushOther('repeated_header', line, pl.page, 'Table header repeated on a later page — not an item.');
          else usedLineIds.add(line);
          headerSeen = true; tableClosed = false; awaitingResume = false;
          cols = buildColumns(hk);
          cols.anchors = computeAnchors(lines.slice(i + 1), cols);
          lastTableY = line.y; lastTableH = line.h;
          continue;
        }
        if (!cols || tableClosed) continue;
        if (line.furniture) continue;            // letterhead / footer repeated on several pages

        const tk = totalKind(line.text);
        const byCol = assignColumns(line.items, cols);
        const cellText = k => byCol[k] ? joinItems(byCol[k], isRtl(byCol[k].map(i => i.str).join(' '))) : '';
        const numericCols = ['qty', 'unitPrice', 'lineTotal'].filter(k => byCol[k] && hasDigit(cellText(k)));
        const refText = cellText('ref');
        const hasRealRef = realRef(refText);
        const desc = cellText('description');

        if (awaitingResume) {
          if (!numericCols.length && !tk && !(hasRealRef && desc)) { skippedBeforeResume++; continue; }
          awaitingResume = false;
          if (skippedBeforeResume) warnings.push({ code: 'table_continues_without_header', page: pl.page,
            message: 'The table continues on page ' + pl.page + ' without a repeated header. ' + skippedBeforeResume +
                     ' text line(s) above the first table line were not attached to any item — check the original.' });
        }

        // Far below the last table line: page notes / footer, not item rows.
        if (lastTableY != null && line.y - lastTableY > Math.max(60, lastTableH * 6)) {
          if (tk && lastAmount(line.text)) { finish(); pushOther(tk, line, pl.page, 'Totals-style line well below the table — review in totals.', { amountRaw: lastAmount(line.text) }); }
          else if (numericCols.length) { finish(); pushOther('unclassified', line, pl.page, 'Numeric line well below the item table — review manually.'); }
          continue;
        }

        // "--- End ---" filler rows
        if (line.cells.length && line.cells.every(c => END_MARKER_CELL.test(c.text)) && /(end|نهاية|-{3,})/i.test(line.text)) {
          finish(); pushOther('end_marker', line, pl.page, 'Table end marker — not an item.');
          lastTableY = line.y; lastTableH = line.h;
          continue;
        }

        if (tk && !(byCol.qty && hasDigit(cellText('qty')) && byCol.unitPrice && hasDigit(cellText('unitPrice')))) {
          finish();
          const amt = lastAmount(line.text);
          const parsed = amt ? parseAmount(amt) : { value: null, flags: ['missing'] };
          pushOther(tk, line, pl.page, {
            carried_forward: 'Carried/brought-forward amount — a running subtotal, not an item.',
            subtotal: 'Subtotal line — not an item.', grand_total: 'Grand total line — not an item.', total: 'Total line — not an item.',
            vat: 'Tax line — not an item.', discount: 'Document discount line — review in totals.', delivery: 'Delivery/shipping charge — review in totals.',
            installation: 'Installation charge — review in totals.' }[tk], { amount: parsed.value, amountRaw: amt || '', amountFlags: parsed.flags });
          if (tk === 'grand_total') { tableClosed = true; section = null; }
          lastTableY = line.y; lastTableH = line.h;
          continue;
        }

        const next = nextNonEmpty(lines, i);
        const nextIsNumbersOnly = !!(next && isNumbersOnlyLine(next, cols));
        // A keyword line is a section heading only if it is not itself the
        // title of the priced line directly below it.
        const isSectionHeading = !numericCols.length && desc && desc.length <= 80 && !hasRealRef && !nextIsNumbersOnly &&
          (OPTIONAL_RE.test(desc) || ALTERNATIVE_RE.test(desc)) && !/\d+\s*(pcs|nos|units?)\b/i.test(desc);
        if (isSectionHeading) {
          finish();
          section = ALTERNATIVE_RE.test(desc) ? 'alternative' : 'optional';
          pushOther('section_heading', line, pl.page, 'Section heading — following rows are marked ' + section + ' and are not selected by default.');
          lastTableY = line.y; lastTableH = line.h;
          continue;
        }

        const part = { line, byCol, cellText, page: pl.page, method };
        const priced = numericCols.length || (byCol.lineTotal && DASH_WORD.test(cellText('lineTotal')));
        if (priced) {
          if (open && !open.hasNumbers && !hasRealRef) {
            // title line, then its (first) priced specification line
            addNumbers(open, part);
          } else if (open && !open.hasNumbers && hasRealRef && isSubRef(refText, open.cells.ref && open.cells.ref.text)) {
            // a titled item whose priced lines are sub-numbered: the title is a heading, the lines are components
            const heading = open; open = null;
            group = { ref: heading.cells.ref.text, title: heading.descLines.map(d => d.text).join(' '), heading: true };
            otherRows.push({ id: 'o' + (++otherN), kind: 'item_heading', page: heading.page, text: heading.parts.map(p => p.line.text).join('\n'),
              parent: { ref: group.ref, title: group.title },
              region: regionOf(heading.parts.map(p => p.line)), reason: 'Item heading — its priced components are listed as separate rows.',
              method: srcOf(heading.parts.flatMap(p => p.line.items)), amount: null, flags: [] });
            open = newRow(++rowN, part, section, group); addNumbers(open, part);
            group.lastRef = refText.trim();
          } else if (hasRealRef && group && group.heading && (isSubRef(refText, group.ref) || nextInSequence(refText, group))) {
            finish(); open = newRow(++rowN, part, section, group); addNumbers(open, part);
            group.lastRef = refText.trim();
          } else if (!hasRealRef && desc && cols.some(c => c.key === 'ref') && group && open && open.hasNumbers) {
            // an extra priced line under the current item without its own number: a component of that item
            // (the item's first priced line is then its first component)
            if (!open.parent && open.cells.ref && open.cells.ref.text === group.ref) open.parent = { ref: group.ref, title: group.title };
            finish(); open = newRow(++rowN, part, section, group); addNumbers(open, part);
          } else {
            finish(); open = newRow(++rowN, part, section, null); addNumbers(open, part);
            group = hasRealRef ? { ref: refText, title: desc, heading: false } : group;
          }
          lastTableY = line.y; lastTableH = line.h;
          usedLineIds.add(line); itemLineIds.add(line);
          continue;
        }
        if (!desc && !hasRealRef && !byCol.unit) {
          // numbers in unexpected columns only, or stray text inside the table area
          if (hasDigit(line.text) && !byCol.ref) { finish(); pushOther('unclassified', line, pl.page, 'Line inside the table with numbers outside the item columns — review manually.'); }
          continue;
        }
        // description-only line
        if (hasRealRef) {
          finish(); open = newRow(++rowN, part, section, null);
          group = { ref: refText, title: desc, heading: false };
        } else if (!byCol.ref && nextIsNumbersOnly && !(open && !open.hasNumbers)) {
          finish(); open = newRow(++rowN, part, section, null);
        } else if (open) {
          appendDesc(open, part);                // continuation (bullet or plain specification line)
        } else {
          open = newRow(++rowN, part, section, null);
        }
        lastTableY = line.y; lastTableH = line.h;
        usedLineIds.add(line); itemLineIds.add(line);
      }
      // A row never spans a page break: a description that continues after a
      // repeated header becomes its own flagged row (no_numbers), never merged silently.
      finish();
    }
    finish();
    if (!headerSeen) {
      // no table at all — keep numeric lines visible for manual review
      for (const pl of pageLines) for (const line of pl.lines) {
        if (!line.text || !hasDigit(line.text) || totalKind(line.text) || REG_NUMBER_LINE.test(line.text)) continue;
        if (PAGE_FOOTER_RE.test(normText(line.text))) continue;
        if (!lastAmount(line.text)) continue;
        otherRows.push({ id: 'o' + (++otherN), kind: 'unstructured', page: pl.page, text: line.text, region: regionOf([line]),
          reason: 'No item table detected — numeric line kept for manual review.', amount: null,
          flags: ['no_table_detected'].concat(line.minConf != null && line.minConf < LOW_CONF ? ['low_confidence'] : []) });
      }
    }
    return { rows, otherRows, usedLineIds, itemLineIds };
  }
  function nextNonEmpty(lines, i) { for (let j = i + 1; j < lines.length; j++) if (lines[j].text) return lines[j]; return null; }
  function isNumbersOnlyLine(line, cols) {
    const byCol = assignColumns(line.items, cols);
    const desc = !!(byCol.description && byCol.description.some(i => clean(i.str)));
    const nums = ['qty', 'unitPrice', 'lineTotal'].some(k => byCol[k] && byCol[k].some(i => hasDigit(i.str)));
    return nums && !desc;
  }
  function newRow(n, part, section, parent) {
    const row = { id: 'r' + n, page: part.page, section, parts: [], hasNumbers: false, cells: {}, descLines: [], method: part.method,
                  parent: parent ? { ref: parent.ref, title: parent.title } : null };
    appendDesc(row, part);
    for (const k of ['ref', 'unit']) if (part.byCol[k]) row.cells[k] = { text: part.cellText(k), items: part.byCol[k], page: part.page };
    return row;
  }
  function appendDesc(row, part) {
    row.parts.push(part);
    const d = part.cellText('description');
    if (d) row.descLines.push({ text: d, items: part.byCol.description, page: part.page });
    for (const k of ['ref', 'unit']) if (part.byCol[k] && !row.cells[k]) row.cells[k] = { text: part.cellText(k), items: part.byCol[k], page: part.page };
  }
  function addNumbers(row, part) {
    if (!row.parts.includes(part)) row.parts.push(part);
    const d = part.cellText('description');
    if (d && !row.descLines.some(x => x.items === part.byCol.description)) row.descLines.push({ text: d, items: part.byCol.description, page: part.page });
    for (const k of ['ref', 'unit', 'qty', 'unitPrice', 'discount', 'tax', 'lineTotal'].concat(DIM_KEYS))
      if (part.byCol[k] && !row.cells[k]) row.cells[k] = { text: part.cellText(k), items: part.byCol[k], page: part.page };
    row.hasNumbers = true;
  }
  // "SIZE: W 1.3 X D 0.85 X H 1.11 M" → { W: 1.3, D: 0.85, H: 1.11 } (first value per letter)
  function specDims(text) {
    const out = {};
    const re = /(?:^|[^A-Za-z])([WDHL])\s*[:=]?\s*(\d+(?:[.,]\d+)?)/g;
    let m;
    while ((m = re.exec(normDigits(text)))) { const k = m[1].toUpperCase(); if (!(k in out)) out[k] = Number(m[2].replace(',', '.')); }
    return out;
  }
  // Measurements printed in the description ("SIZE: W 1 x H 2 M + BASE W 1.2 M X D 0.20 M"):
  // the first size line's first part is the item's own size (main); every further part
  // ("+ BASE …", "+ POLE OF 1.5 M") and any further size line are separate measurements,
  // kept word for word with their own labels and units.
  const SIZE_LABEL = /(?:^|[^A-Za-z؀-ۿ])(?:size|dimensions?|dims|المقاسات|المقاس|مقاس|الأبعاد|الابعاد)\s*[:\-]?\s*/i;
  function describedDims(text) {
    let main = null;
    const additional = [];
    for (const line of String(text || '').split('\n')) {
      const m = line.match(SIZE_LABEL);
      if (!m) continue;
      const rest = line.slice(m.index + m[0].length).trim();
      if (!/\d/.test(normDigits(rest))) continue;
      const parts = rest.split(/\s+\+\s+/).map(s => s.trim()).filter(Boolean);   // "2+2+2" (no spaces) stays one part
      if (main == null) main = parts.shift() || null;
      additional.push(...parts);
    }
    // the item's own size letters come from the main part only (a BASE D is not the item's D)
    return { main, additional, spec: specDims(main != null ? main : String(text || '')) };
  }
  // "1.30 m" printed in a dimension cell: the number, and the unit printed with it
  const CELL_UNIT_RE = /^\s*[\d٠-٩][\d٠-٩.,٫]*\s*(mm|cm|m|mtr|in|ft|مم|سم|م)\.?\s*$/i;
  const dimNum = v => { const m = normDigits(String(v == null ? '' : v)).match(/^\s*(\d+(?:[.,]\d+)?)/); return m ? Number(m[1].replace(',', '.')) : null; };
  const SIZE_UNIT_RE = /(?:^|[\d\s])(mm|cm|m|mtr|in|ft)\.?\s*$/i;
  // true when a size text holds nothing but W/D/H/L numbers and separators — plus, at most, the
  // SAME unit the columns print. Any other unit (e.g. "M" when the columns print none) is
  // information the columns lack, so that text is kept.
  function onlyLetterDims(t, colUnit) {
    let x = normDigits(t);
    const u = x.match(SIZE_UNIT_RE);
    if (u) {
      if (!colUnit || u[1].toLowerCase() !== String(colUnit).toLowerCase()) return false;
      x = x.slice(0, u.index + (u[0].length - u[0].trimStart().length)) ;
    }
    return !x.replace(/[WDHL]\s*[:=]?\s*\d+(?:[.,]\d+)?/gi, '').replace(/\b(x|by)\b/gi, '').replace(/[\s×*,;:\-]/g, '');
  }
  function cellEF(cell, method, value, flags) {
    if (!cell) return missing();
    const confs = cell.items.map(i => i.conf).filter(c => c != null);
    const conf = confs.length ? Math.min(...confs) : (method === 'ocr' ? 0.6 : 0.85);
    const f = (flags || []).slice();
    if (confs.length && conf < LOW_CONF) f.push('low_confidence');
    return ef(value, { raw: cell.text, page: cell.page, region: regionOf(cell.items), method: srcOf(cell.items) || method, confidence: conf, flags: f });
  }
  function finalizeRow(row, tableDims) {
    const method = row.method;
    const flags = [];
    const fields = {};
    fields.ref = row.cells.ref ? cellEF(row.cells.ref, method, row.cells.ref.text) : missing();
    if (row.descLines.length) {
      const items = row.descLines.flatMap(d => d.items || []);
      const confs = items.map(i => i.conf).filter(c => c != null);
      const conf = confs.length ? Math.min(...confs) : (method === 'ocr' ? 0.6 : 0.85);
      const text = row.descLines.map(d => d.text).join('\n');
      const f = confs.length && conf < LOW_CONF ? ['low_confidence'] : [];
      if (row.descLines.length > 1) f.push('multiline');
      fields.description = ef(text, { raw: text, page: row.descLines[0].page, region: regionOf(items), method: srcOf(items) || method, confidence: conf, flags: f });
      if (row.descLines.some(d => d.page !== row.descLines[0].page)) fields.description.flags.push('continues_next_page');
    } else fields.description = missing();

    const q = row.cells.qty ? parseQty(row.cells.qty.text) : null;
    fields.qty = row.cells.qty ? cellEF(row.cells.qty, method, q.value, q.flags) : missing();
    let unitText = row.cells.unit ? row.cells.unit.text : '';
    if (!unitText && q && q.unit) unitText = q.unit;
    fields.unit = row.cells.unit ? cellEF(row.cells.unit, method, row.cells.unit.text)
      : (q && q.unit ? ef(q.unit, { raw: row.cells.qty.text, page: row.cells.qty.page, region: regionOf(row.cells.qty.items), method, confidence: 0.6, flags: ['unit_from_quantity_cell'] }) : missing());
    for (const k of ['unitPrice', 'discount', 'lineTotal']) {
      if (!row.cells[k]) { fields[k] = missing(); continue; }
      const a = parseAmount(row.cells[k].text);
      fields[k] = cellEF(row.cells[k], method, a.value, a.flags);
    }
    if (row.cells.tax) {
      const t = parseTaxCell(row.cells.tax.text);
      fields.tax = cellEF(row.cells.tax, method, t ? { treatment: t.treatment, rate: t.rate, amount: t.amount } : null, t && t.unreadable ? ['unreadable_number'] : []);
    } else fields.tax = missing();
    // dimension columns (W/D/H/L) are part of the item's specification — never quantities
    // Every dimension column of the table is kept in printed order, a blank cell as '' —
    // values are never moved to another letter, even when the description suggests it.
    const dimCells = DIM_KEYS.filter(k => row.cells[k] && row.cells[k].text.trim());
    if (dimCells.length) {
      const dimKeys = DIM_KEYS.filter(k => (tableDims || []).some(c => c.key === k) || dimCells.includes(k));
      const dims = {};
      for (const k of dimKeys) dims[k.slice(3)] = row.cells[k] ? row.cells[k].text.trim() : '';   // as printed (a cell unit stays in the text)
      // a unit printed in the dimension headers or in the cells themselves — never inferred
      const units = (tableDims || []).filter(c => dimKeys.includes(c.key) && c.unit).map(c => c.unit)
        .concat(dimCells.map(k => (row.cells[k].text.match(CELL_UNIT_RE) || [])[1]).filter(Boolean));
      const dimUnit = units.length && units.every(u => u.toLowerCase() === units[0].toLowerCase()) ? units[0] : '';
      const items = dimCells.flatMap(k => row.cells[k].items);
      fields.dimensions = ef(dims, { raw: dimKeys.map(k => k.slice(3) + ' ' + (dims[k.slice(3)] || '(blank)')).join(' × '),
        page: row.cells[dimCells[0]].page, region: regionOf(items), method: srcOf(items) || method, confidence: 0.85, flags: [] });
      // a size stated in the description that contradicts the dimension columns is flagged, never corrected
      // (a value printed in the description where the column cell is BLANK is a discrepancy too;
      //  the blank cell stays blank)
      const spec = describedDims(row.descLines.map(d => d.text).join('\n')).spec;
      const conflicts = Object.keys(dims).filter(L => spec[L] != null && (dimNum(dims[L]) == null || Math.abs(spec[L] - dimNum(dims[L])) > 1e-9));
      if (dimUnit) fields.dimensions.unit = dimUnit;
      if (conflicts.length) {
        flags.push('spec_dimension_conflict');
        fields.dimensions.flags.push('spec_dimension_conflict');
        fields.dimensions.conflicts = conflicts.map(L => ({ dim: L, column: dims[L], description: spec[L] }));
      }
    }

    // flags (never "fix" anything)
    if (fields.lineTotal.flags.includes('dash_amount')) flags.push('dash_amount');
    if (fields.lineTotal.value == null && fields.unitPrice.value != null && fields.qty.value == null) flags.push('no_amount');
    const qn = toNum(fields.qty.value), pn = toNum(fields.unitPrice.value), tn = toNum(fields.lineTotal.value), dn = toNum(fields.discount.value);
    if (fields.qty.flags.includes('lump_sum')) flags.push('lump_sum');
    else if (tn != null && (qn == null || pn == null)) flags.push('lump_sum_or_missing_breakdown');
    if (!row.hasNumbers) flags.push('no_numbers');
    if (qn != null && pn != null && tn != null) {
      const expected = qn * pn - (dn != null ? Math.abs(dn) : 0);
      const tol = Math.max(0.011, Math.abs(expected) * 1e-6);
      if (Math.abs(expected - tn) > tol) {
        flags.push('line_total_mismatch');
        const taxRate = fields.tax.value && fields.tax.value.rate ? Number(fields.tax.value.rate) : 15;
        if (Math.abs(expected * (1 + taxRate / 100) - tn) <= Math.max(0.02, expected * 1e-4)) flags.push('line_total_may_include_tax');
      }
    }
    for (const f of Object.values(fields)) for (const fl of f.flags || [])
      if (['low_confidence', 'ambiguous_number', 'unreadable_number'].includes(fl) && !flags.includes(fl)) flags.push(fl);
    const allText = row.descLines.map(d => d.text).join(' ');
    let kind = 'item';
    if (row.section === 'alternative' || ALTERNATIVE_RE.test(allText)) kind = 'alternative';
    else if (row.section === 'optional' || OPTIONAL_RE.test(allText)) kind = 'optional';
    const rawText = row.parts.map(p => p.line.text).join('\n');
    return {
      id: row.id, page: row.page, kind, fields, flags,
      parent: row.parent || null,
      region: regionOf(row.parts.map(p => p.line)),
      pages: [...new Set(row.parts.map(p => p.page))],
      rawText,
      // a price without quantity or amount ("-") is shown but not selected: nothing is invented
      // rows read wholly or partly by OCR are never pre-selected, whatever their confidence or numbering
      selectedByDefault: kind === 'item' && row.hasNumbers && !flags.includes('no_amount') &&
        !['description', 'qty', 'unitPrice', 'lineTotal', 'ref'].some(k => fields[k].method === 'ocr' || fields[k].method === 'mixed'),
    };
  }
  function markDuplicates(rows) {
    const seen = new Map();
    for (const r of rows) {
      const sig = [normText(r.fields.description.value || ''), r.fields.qty.value, r.fields.unitPrice.value, r.fields.lineTotal.value].join('|');
      if (!r.fields.description.value) continue;
      if (seen.has(sig)) {
        const first = rows.find(x => x.id === seen.get(sig));
        const refA = clean(first.fields.ref.value || ''), refB = clean(r.fields.ref.value || '');
        if (refA && refB && refA !== refB && !/^[-–—•*#]+$/.test(refA + refB)) {
          r.flags.push('same_values_as_other_row'); r.sameValuesAs = first.id;   // printed as its own numbered line
          continue;
        }
        r.flags.push('possible_duplicate');
        r.duplicateOf = seen.get(sig);
        r.selectedByDefault = false;
      } else seen.set(sig, r.id);
    }
  }

  // ── header fields ─────────────────────────────────────────────────────────
  // Header labels are matched against a line's CELLS joined with " ¦ ": a
  // value never runs into a neighbouring cell ("Payment Terms: As Agreed ¦
  // Sub Total ¦ SAR 13,905.00"), but a label cell may take its value from the
  // next cell ("Quote Ref. #: ¦ AB-26-7") or, when a label stands alone
  // ("PAYMENT TERMS:"), from the lines directly below it.
  const SEP = '¦';
  const BUYER_CTX = /(\bto\s*[:\-]|bill\s*to|ship\s*to|customer|client|attn|attention|messrs|vista\s*united|العميل|السادة|السيد|المحترمين|الى\s*[:\-]|إلى\s*[:\-])/i;
  const BUYER_NAME = /vista\s*united|فيستا\s*يونايتد/i;       // the buyer (this platform's company)
  const LS = '\\s*[:\\-#.]*\\s*(?:' + SEP + '\\s*)?';             // label separator, may cross into the next cell
  const REF_V = '([A-Za-z0-9\\u0600-\\u06FF][\\w\\-\\/.\\u0600-\\u06FF]*)';
  const TXT_V = '([^' + SEP + ']*)';
  const lab = (label, value, fl) => new RegExp(label + LS + value, fl || 'i');
  const LABELS = {
    quotationRef: [
      lab('(?:quotation|quote|qtn|offer|proposal|rfq)\\s*(?:no\\.?|number|#|ref\\.?)(?:\\s*(?:no\\.?|#))?', REF_V),
      lab('\\bref(?:erence)?\\.?\\s*(?:no\\.?|#)?', REF_V),
      lab('(?:رقم\\s*(?:العرض|عرض\\s*السعر|المرجع)|عرض\\s*سعر\\s*رقم|المرجع)', REF_V, ''),
    ],
    validity: [lab('(?:validity|valid\\s*(?:for|until|till|up\\s*to)|offer\\s*valid(?:ity)?|quotation\\s*valid(?:ity)?)', TXT_V),
               lab('(?:صلاحية\\s*العرض|مدة\\s*(?:صلاحية|سريان)\\s*العرض|العرض\\s*(?:ساري|صالح)(?:\\s*لمدة)?)', TXT_V, '')],
    paymentTerms: [lab('(?:payment\\s*terms?|terms\\s*of\\s*payment|payment\\s*conditions?)', TXT_V), lab('^payment\\s*(?=[:\\-])', TXT_V),
                   lab('(?:شروط\\s*الدفع|طريقة\\s*الدفع|شروط\\s*السداد|طريقة\\s*السداد)', TXT_V, ''), lab('^(?:الدفع|السداد)\\s*(?=[:\\-])', TXT_V, '')],
    delivery: [lab('(?:delivery\\s*(?:time|period|terms?|schedule|lead\\s*time)|lead\\s*time)', TXT_V), lab('^delivery\\s*(?=[:\\-])', TXT_V),
               lab('(?:مدة\\s*(?:التوريد|التسليم|التنفيذ)|فترة\\s*(?:التوريد|التسليم))', TXT_V, ''), lab('^(?:التوريد|التسليم)\\s*(?=[:\\-])', TXT_V, '')],
    exclusions: [lab('(?:exclusions?|excluded|not\\s*included)\\s*(?=[:\\-])', TXT_V), lab('(?:الاستثناءات|لا\\s*يشمل(?:\\s*العرض)?|غير\\s*مشمول)', TXT_V, '')],
    notes: [lab('^(?:notes?|remarks?|terms\\s*(?:&|and)\\s*conditions)\\s*(?=[:\\-])', TXT_V), lab('^(?:ملاحظات|ملاحظة|الشروط\\s*والأحكام)\\s*(?=[:\\-])', TXT_V, '')],
    supplierLabel: [lab('^(?:from|supplier|vendor|company(?:\\s*name)?)\\s*(?=[:\\-])', TXT_V), lab('^(?:المورد|من|اسم\\s*الشركة)\\s*(?=[:\\-])', TXT_V, '')],
    customerName: [lab('(?:\\bclient|\\bcustomer|bill\\s*to|\\bmessrs\\.?|\\bto)\\s*(?=[:\\-])', TXT_V), lab('(?:العميل|السادة|إلى|الى)\\s*(?=[:\\-/])', TXT_V, '')],
    projectName: [lab('\\bproject(?:\\s*\\/\\s*campaign)?(?:\\s*name)?\\s*(?=[:\\-])', TXT_V), lab('(?:اسم\\s*المشروع|المشروع)\\s*(?=[:\\-])', TXT_V, '')],
  };
  // fields whose value may continue on the following lines ("PAYMENT TERMS:" ⏎ "50% Advance …")
  const MULTILINE_FIELDS = new Set(['paymentTerms', 'notes', 'exclusions', 'delivery', 'validity']);
  const DATE_LABEL = /(quotation\s*date|quote\s*date|date\s*of\s*(quotation|offer|issue)|issue\s*date|\bdate\b|تاريخ\s*(العرض|الإصدار|الاصدار|عرض\s*السعر)|التاريخ|تاريخ)/i;
  const NOT_QUOTE_DATE = /(delivery|due|expiry|expire|valid|until|required|deadline|تسليم|التسليم|توريد|التوريد|انتهاء|الانتهاء|استحقاق|صلاحية)/i;
  const NEW_LABEL = /^[^\d¦]{2,40}:\s*/;                            // "NOTE:", "DELIVERY:" … starts another field
  // generic trade words: a letterhead tagline made only of these is not a company name
  const GENERIC_WORDS = new Set(('engineering procurement construction production contracting contractors trading general services ' +
    'industrial industries industry manufacturing solutions company co est establishment group and for of the & ltd llc limited ' +
    'advertising printing signage media events exhibitions design interior fitout fit out').split(' '));
  const isTagline = t => {
    const w = normText(t).replace(/[^a-z\u0600-\u06FF& ]+/g, ' ').split(/\s+/).filter(Boolean);
    return w.length > 0 && w.every(x => GENERIC_WORDS.has(x) || (x.length > 3 && x[0] === 'l' && GENERIC_WORDS.has(x.slice(1))));
  };
  const COMPANY_WORD = /\b(company|co\.|est\.?|establishment|trading|factory|ltd|llc|group|contracting)\b|شركة|مؤسسة|مصنع|مجموعة/i;
  const FREE_MAIL = /^(gmail|hotmail|outlook|yahoo|live|icloud|aol|proton|protonmail|msn)$/i;

  function extractHeader(pageLines, used, fileName) {
    const itemLines = used.itemLineIds || used;              // lines of item rows are never header evidence
    const all = [];
    for (const pl of pageLines) {
      pl.lines.forEach((l, idx) => all.push({ l, page: pl.page, method: pl.method, idx, pl,
        t: normDigits((l.cells && l.cells.length ? l.cells.map(c => c.text) : [l.text]).join(' ' + SEP + ' ')) }));
    }
    // buyer block: the cells next to a buyer label ("To:", "Attn:", "CLIENT:") —
    // same column band, a few lines above/below. Supplier identity is never read from it.
    const buyer = new Set();                                 // cells
    const buyerLines = new Set();
    for (const pl of pageLines) {
      const cells = pl.lines.flatMap(l => (l.cells || []).map(c => ({ c, l })));
      for (const { c, l } of cells) {
        if (!BUYER_CTX.test(c.text) || /(from|supplier|المورد)\s*[:\-]/i.test(c.text)) continue;
        buyer.add(c);
        const span = Math.max(30, l.h * 4);
        for (const o of cells) {
          if (o.c === c || Math.abs(o.l.y - l.y) > span) continue;
          if (o.c.x >= c.x - 10 && o.c.x <= c.x + c.w + 80 && !/(from|supplier|المورد)\s*[:\-]/i.test(o.c.text)) buyer.add(o.c);
        }
      }
      for (const l of pl.lines) if ((l.cells || []).length && l.cells.every(c => buyer.has(c))) buyerLines.add(l);
    }
    const efLine = (value, e, raw, flags, conf, lines) => ef(value, { raw: raw != null ? raw : e.l.text, page: e.page, region: regionOf(lines || [e.l]), method: srcOf((lines || [e.l]).flatMap(l => l.items)) || e.method,
      confidence: conf != null ? conf : (e.l.minConf != null ? e.l.minConf : (e.method === 'ocr' ? 0.6 : 0.8)),
      flags: (flags || []).concat(e.l.minConf != null && e.l.minConf < LOW_CONF ? ['low_confidence'] : []) });
    // continuation lines below a label that stands alone: same page, same left column, no gap, until another label
    const continuation = (e) => {
      const lab = e.l.cells && e.l.cells.length ? e.l.cells[0] : { x: e.l.x, w: 0 };
      const got = [];
      let prev = e.l;
      for (let j = e.idx + 1; j < e.pl.lines.length && got.length < 8; j++) {
        const nx = e.pl.lines[j];
        if (!nx.text) continue;
        if (nx.y - (prev.y + prev.h) > Math.max(6, prev.h * 1.6)) break;
        if (itemLines.has(nx) || nx.furniture) break;
        const first = (nx.cells && nx.cells[0]) || { text: nx.text, x: nx.x };
        if (Math.abs(first.x - lab.x) > 25) break;
        if (NEW_LABEL.test(first.text) && !/^\s*[-*•\d]/.test(first.text)) break;
        got.push({ line: nx, text: clean(first.text) });
        prev = nx;
      }
      return got;
    };
    const firstLabel = (key) => {
      for (const e of all) {
        if (itemLines.has(e.l)) continue;
        for (const re of LABELS[key]) {
          const m = e.t.match(re);
          if (!m) continue;
          let v = clean(m[m.length - 1] || '').replace(/^[:\-#.\s]+/, '');
          if (key === 'customerName' || key === 'projectName' || key === 'supplierLabel') v = v.replace(/\s*[:\-]\s*$/, '');
          if (v) return efLine(v, e, e.l.text);
          if (MULTILINE_FIELDS.has(key)) {
            const more = continuation(e);
            if (more.length) return efLine(more.map(x => x.text).join('\n'), e, [e.l.text].concat(more.map(x => x.line.text)).join('\n'),
              more.length > 1 ? ['multiline'] : [], null, [e.l].concat(more.map(x => x.line)));
          }
          const nx = e.pl.lines[e.idx + 1];
          if (nx && nx.text && !itemLines.has(nx) && key !== 'customerName' && key !== 'projectName') return efLine(clean(nx.text), e, e.l.text + '\n' + nx.text, ['value_on_next_line'], null, [e.l, nx]);
        }
      }
      return missing();
    };
    const out = {};
    for (const k of ['quotationRef', 'validity', 'paymentTerms', 'delivery', 'exclusions', 'notes', 'customerName', 'projectName'])
      out[k] = firstLabel(k);
    // the buyer named in the "To:" block when no explicit client label exists
    if (out.customerName.value == null) {
      for (const e of all) {
        const c = (e.l.cells || []).find(x => buyer.has(x) && BUYER_NAME.test(x.text));
        if (c) { out.customerName = efLine(clean(c.text), e, e.l.text, ['from_buyer_block'], 0.7); break; }
      }
    }

    // quotation date — never a delivery / expiry / due date
    const dateHits = [];
    const DATE_LABEL_G = new RegExp(DATE_LABEL.source, 'gi');
    for (const e of all) {
      if (itemLines.has(e.l)) continue;
      const text = e.t;
      for (const m of text.matchAll(DATE_LABEL_G)) {
        // skip "Delivery Date", "Due date", "تاريخ التسليم" …: an excluded word right before or after the label
        const before = text.slice(Math.max(0, m.index - 18), m.index);
        const labelAndNext = text.slice(m.index, m.index + m[0].length + 14);
        if (NOT_QUOTE_DATE.test(before) || NOT_QUOTE_DATE.test(labelAndNext)) continue;
        // the value runs until the next excluded keyword on the same line ("Date: … Valid until: …")
        let seg = text.slice(m.index + m[0].length).replace(/^[\s:\-]*¦?/, '');
        const cut = seg.search(new RegExp(NOT_QUOTE_DATE.source + '|' + SEP, 'i'));
        if (cut >= 0) seg = seg.slice(0, cut);
        let ds = parseDates(seg);
        if (!ds.length && !clean(seg.replace(/[:\-]/g, ''))) {
          const nx = e.pl.lines[e.idx + 1];
          if (nx && !NOT_QUOTE_DATE.test(nx.text)) ds = parseDates(nx.text);
        }
        if (ds.length) { dateHits.push({ e, ds }); break; }
      }
    }
    if (dateHits.length) {
      const h = dateHits[0];
      const d = h.ds[0];
      const flags = d.flags.slice();
      const distinct = new Set(dateHits.map(x => x.ds[0].raw));
      if (distinct.size > 1) flags.push('conflicting_dates');
      out.quotationDate = efLine(d, h.e, h.e.l.text, flags);
      if (h.ds.length > 1) out.quotationDate.value.otherCalendars = h.ds.slice(1).map(x => ({ raw: x.raw, calendar: x.calendar, iso: x.iso, hijri: x.hijri, ambiguous: x.ambiguous }));
    } else out.quotationDate = missing();
    // a date in the FILE NAME is never used; if it disagrees with the printed date, say so
    const fileDates = fileName ? parseDates(String(fileName).replace(/\.[a-z0-9]{2,5}$/i, '').replace(/_/g, ' ')) : [];
    if (fileDates.length && out.quotationDate.value && out.quotationDate.value.iso) {
      const printed = out.quotationDate.value.iso;
      const fits = fileDates.some(fd => fd.iso === printed || (fd.alternatives || []).some(a => a.startsWith(printed)));
      if (!fits) {
        out.quotationDate.flags.push('filename_date_differs');
        out.quotationDate.fileNameDate = fileDates[0].raw;
      }
    }

    // supplier identity (outside the buyer block)
    const idLines = all.filter(e => !buyerLines.has(e.l) && !itemLines.has(e.l));
    const idText = e => (e.l.cells && e.l.cells.length ? e.l.cells.filter(c => !buyer.has(c)).map(c => c.text).join(' ' + SEP + ' ') : e.t);
    const find = (re, grp, flags, check) => {
      for (const e of idLines) {
        const m = normDigits(idText(e)).match(re);
        if (!m) continue;
        const raw = m[grp];
        const v = clean(raw).replace(/[\s-]/g, '');
        if (check && !check(v)) continue;
        return efLine(v, e, null, flags.concat(/[\s-]/.test(clean(raw)) ? ['digits_spaced_in_source'] : []));
      }
      return missing();
    };
    const vatLabel = '(?:vat|tax|trn|الرقم\\s*الضريبي|رقم\\s*التسجيل\\s*الضريبي)\\s*(?:reg(?:istration)?\\.?\\s*)?(?:no\\.?|number|#)?\\s*[:\\-]?\\s*';
    out.supplierVat = find(/(?:^|\D)(3\d{13}3)(?:\D|$)/, 1, []);
    if (out.supplierVat.value == null) out.supplierVat = find(new RegExp(vatLabel + '((?:\\d[ \\-]?){14}\\d)(?!\\d)', 'i'), 1, [], v => /^3\d{13}3$/.test(v));
    if (out.supplierVat.value == null) out.supplierVat = find(new RegExp(vatLabel + '((?:\\d[ \\-]?){9,19}\\d)(?!\\d)', 'i'), 1, ['not_saudi_vat_format']);
    out.supplierCr = find(/(?:c\.?\s?r\.?|commercial\s*reg(?:istration)?|س\.?\s*ت|السجل\s*التجاري|سجل\s*تجاري)\s*(?:no\.?|number|#)?\s*[:\-.]?\s*((?:\d[ \-]?){6,11}\d)(?!\d)/i, 1, []);
    out.supplierEmail = find(/([\w.+-]+@[\w-]+(?:\.[\w-]+)+)/, 1, []);
    out.supplierPhone = (() => {
      for (const e of idLines) {
        const m = normDigits(idText(e)).match(/((?:\+|00)?966[\s-]?\d[\d\s-]{7,11}\d|\b05\d[\s-]?\d{3}[\s-]?\d{4}\b|\b01\d[\s-]?\d{3}[\s-]?\d{4}\b)/);
        if (m) return efLine(clean(m[1]), e);
      }
      return missing();
    })();
    let name = firstLabel('supplierLabel');
    if (name.value == null && pageLines.length) {
      const p1 = pageLines[0];
      const pageH = p1.height || Math.max(1, ...p1.lines.map(l => l.y + l.h));
      const cands = [];
      // letterhead area only: above the item table's header line
      const th = p1.lines.find(l => isHeaderLine(l));
      const limit = Math.min(pageH * 0.35, th ? th.y : Infinity);
      for (const l of p1.lines) {
        if (l.y >= limit || itemLines.has(l)) continue;
        for (const c of l.cells || []) {
          const t = clean(c.text);
          if (buyer.has(c) || /\d/.test(t) || !/[A-Za-z\u0600-\u06FF]{3}/.test(t) || t.length > 80) continue;
          if (/(quotation|quote|invoice|date|tel|phone|fax|email|www\.|http|vat|c\.?r\b|prepared|project|location|attn|kingdom|saudi|\barea\b|street|road|district|building|p\.?\s?o\.?\s*box|حي|شارع|طريق|عرض\s*سعر|عرض\s*الاسعار|التاريخ|هاتف|جوال|فاكس|الرقم\s*الضريبي|السجل|المملكة)/i.test(t)) continue;
          if (/:\s*$/.test(t) || isTagline(t)) continue;
          cands.push({ l, c, t, score: (COMPANY_WORD.test(t) ? 100 : 0) + c.h * 2 - l.y / 100 });
        }
      }
      if (cands.length) {
        const best = cands.sort((a, b) => b.score - a.score)[0];
        const e = all.find(x => x.l === best.l);
        name = efLine(best.t, e, best.l.text, ['heuristic_supplier_name'], 0.4);
      } else {
        // last resort: the supplier's own web/e-mail domain (never a free-mail provider)
        for (const e of idLines) {
          const m = idText(e).match(/(?:www\.|@)([a-z0-9-]{3,})\.[a-z.]{2,}/i);
          if (m && !FREE_MAIL.test(m[1])) { name = efLine(m[1].toUpperCase(), e, e.l.text, ['heuristic_supplier_name', 'name_from_website'], 0.3); break; }
        }
      }
    }
    out.supplierName = name;
    out.currency = extractCurrency(all);
    return out;
  }
  const CURRENCY_PATTERNS = [
    { code: 'SAR', re: /\bSAR\b|ريال\s*(ال)?سعودي|ر\.\s?س\.?|ر\.س/i },
    { code: 'SAR', re: /\bS\.?R\.?\b/, flag: 'currency_inferred_from_SR' },
    { code: 'USD', re: /\bUSD\b|US\s?\$/i }, { code: 'USD', re: /\$/, flag: 'currency_from_symbol_only' },
    { code: 'EUR', re: /\bEUR\b|€/i }, { code: 'AED', re: /\bAED\b|درهم/i }, { code: 'GBP', re: /\bGBP\b|£/i },
    { code: 'KWD', re: /\bKWD\b/i }, { code: 'BHD', re: /\bBHD\b/i }, { code: 'OMR', re: /\bOMR\b/i }, { code: 'QAR', re: /\bQAR\b/i }, { code: 'EGP', re: /\bEGP\b/i },
  ];
  function extractCurrency(all) {
    const found = new Map();
    for (const e of all) for (const c of CURRENCY_PATTERNS) {
      if (c.re.test(e.l.text) && !found.has(c.code)) found.set(c.code, { e, c });
    }
    if (!found.size) return missing();
    const codes = [...found.keys()];
    const first = found.get(codes[0]);
    const flags = [];
    if (first.c.flag) flags.push(first.c.flag);
    if (codes.length > 1) flags.push('multiple_currencies');
    return ef(codes.length > 1 ? null : codes[0], { raw: first.e.l.text, page: first.e.page, region: regionOf([first.e.l]), method: first.e.method,
      confidence: codes.length > 1 ? 0.3 : (first.c.flag ? 0.6 : 0.9), flags: codes.length > 1 ? flags.concat('candidates:' + codes.join(',')) : flags });
  }

  // ── tax statement ─────────────────────────────────────────────────────────
  const INCL_RE = /((prices?|amounts?|rates?)\s*(are\s*)?(inclusive\s*of|include|includes|including)\s*(the\s*)?(vat|tax)|inclusive\s*of\s*(vat|tax)|incl\.?\s*(vat|tax)|(vat|tax)\s*inclusive|شامل(ة)?\s*(ال)?ضريبة|شامله\s*(ال)?ضريبه|الأسعار\s*شاملة|الاسعار\s*شامله|شاملة\s*ضريبة\s*القيمة\s*المضافة)/i;
  const EXCL_RE = /((prices?|amounts?|rates?)\s*(are\s*)?(exclusive\s*of|exclude|excludes|excluding|do\s*not\s*include)\s*(the\s*)?(vat|tax)|exclusive\s*of\s*(vat|tax)|excl\.?\s*(vat|tax)|(vat|tax)\s*exclusive|excluding\s*(vat|tax)|\+\s*(vat|tax)|plus\s*(vat|tax)|غير\s*شامل(ة)?\s*(ال)?ضريب|لا\s*تشمل\s*(ال)?ضريب|بدون\s*(ال)?ضريب|الأسعار\s*لا\s*تشمل|يضاف\s*(إليها|اليها)?\s*(ال)?ضريب)/i;
  function extractTaxStatement(pageLines) {
    let incl = null, excl = null, rate = null;
    const warnings = [];
    for (const pl of pageLines) for (const l of pl.lines) {
      const t = normDigits(l.text);
      if (REG_NUMBER_LINE.test(t)) continue;
      if (!excl && EXCL_RE.test(t)) excl = { l, pl };
      else if (!incl && INCL_RE.test(t)) incl = { l, pl };
      if (!rate) {
        const m = t.match(/(?:vat|tax|ضريبة|ضريبه|الضريبة|القيمة\s*المضافة)[^0-9%]{0,30}(\d{1,2}(?:\.\d{1,2})?)\s*%/i) || t.match(/(\d{1,2}(?:\.\d{1,2})?)\s*%\s*(?:vat|tax|ضريبة|ضريبه)/i);
        if (m) rate = { l, pl, v: m[1] };
      }
    }
    const mk = (v, hit, flags, conf) => ef(v, { raw: hit.l.text, page: hit.pl.page, region: regionOf([hit.l]), method: hit.pl.method, confidence: conf, flags });
    let basis;
    if (incl && excl) { basis = ef(null, { raw: incl.l.text + ' | ' + excl.l.text, page: incl.pl.page, method: incl.pl.method, confidence: 0.2, flags: ['conflicting_tax_statements'] });
      warnings.push({ code: 'conflicting_tax_statements', page: incl.pl.page, message: 'The document states both tax-inclusive and tax-exclusive pricing. Decide manually.' }); }
    else if (incl) basis = mk('inclusive', incl, [], 0.8);
    else if (excl) basis = mk('exclusive', excl, [], 0.8);
    else basis = missing();
    return { basis, rate: rate ? mk(rate.v, rate, [], 0.8) : missing(), warnings };
  }

  // ── totals ────────────────────────────────────────────────────────────────
  function extractTotals(pageLines, table) {
    const out = { subtotal: missing(), discount: missing(), delivery: missing(), installation: missing(), vat: missing(), grandTotal: missing(), total: missing(),
                  carriedForward: [], otherCharges: [] };
    const fromOther = table.otherRows.filter(o => ['subtotal', 'grand_total', 'total', 'vat', 'discount', 'delivery', 'installation', 'carried_forward'].includes(o.kind));
    const seen = new Set(fromOther.map(o => o.page + ':' + o.text));
    const list = fromOther.map(o => ({ kind: o.kind, page: o.page, text: o.text, region: o.region, amt: o.amountRaw || '', method: o.method || null, flags: o.flags || [] }));
    // totals printed outside the table area (below it, or on a later page)
    for (const pl of pageLines) for (const l of pl.lines) {
      if (seen.has(pl.page + ':' + l.text) || table.usedLineIds.has(l)) continue;
      const k = totalKind(l.text);
      if (!k) continue;
      // "Delivery: 3 weeks", "VAT registered since 2019"… are terms, not amounts
      if (/(\bdays?\b|\bweeks?\b|\bmonths?\b|\bworking\b|يوم|أيام|ايام|أسبوع|اسبوع|أسابيع|شهر|أشهر)/i.test(l.text)) continue;
      if (parseDates(l.text).length) continue;               // "Delivery Date: 15/10/2026" is a date, not a charge
      // "50% After delivery  229,234.05": a payment-schedule instalment, not a charge or total
      if (k !== 'vat' && k !== 'discount' && /^\s*\d{1,3}(?:\.\d+)?\s*%/.test(normDigits(l.text))) continue;
      const amt = lastAmount(l.text);
      if (!amt || normDigits(amt).replace(/\D/g, '').length < 2) continue;
      list.push({ kind: k, page: pl.page, text: l.text, region: regionOf([l]), amt, method: srcOf(l.items) || pl.method, flags: l.minConf != null && l.minConf < LOW_CONF ? ['low_confidence'] : [] });
    }
    const keyOf = { subtotal: 'subtotal', discount: 'discount', delivery: 'delivery', installation: 'installation', vat: 'vat', grand_total: 'grandTotal', total: 'total' };
    for (const t of list) {
      const a = t.amt ? parseAmount(t.amt) : { value: null, flags: ['missing'] };
      const field = ef(a.value, { raw: t.text, page: t.page, region: t.region, method: t.method, confidence: a.value != null ? 0.75 : 0.3, flags: a.flags.concat(t.flags) });
      if (t.kind === 'carried_forward') { out.carriedForward.push(field); continue; }
      const key = keyOf[t.kind];
      if (!key) continue;
      if (out[key].value == null && !(out[key].flags || []).includes('present')) { out[key] = field; out[key].flags.push('present'); }
      else if (key === 'delivery' || key === 'installation') out.otherCharges.push(Object.assign(field, { label: t.text }));
      else out[key].flags.includes('multiple_candidates') || out[key].flags.push('multiple_candidates');
    }
    for (const k of Object.keys(out)) if (out[k] && out[k].flags) out[k].flags = out[k].flags.filter(f => f !== 'present');
    // arithmetic cross-checks — reported, never used to change a value
    const n = k => toNum(out[k].value);
    const checks = [];
    // An unlabelled "Total" printed before VAT and a "Grand Total" is the pre-tax subtotal;
    // it is reported as such (flagged), never silently renamed.
    if (out.subtotal.value == null && out.total.value != null && out.grandTotal.value != null && out.vat.value != null &&
        Math.abs(toNum(out.total.value) + toNum(out.vat.value) - toNum(out.grandTotal.value)) <= 0.011) {
      out.subtotal = Object.assign({}, out.total, { flags: (out.total.flags || []).concat('from_total_line') });
    }
    const sub = n('subtotal'), vat = n('vat'), disc = n('discount'), gt = n('grandTotal') != null ? n('grandTotal') : n('total');
    const charges = ['delivery', 'installation'].map(n).filter(v => v != null).reduce((a, b) => a + b, 0);
    if (sub != null && vat != null && gt != null) {
      const expected = sub - (disc != null ? Math.abs(disc) : 0) + charges + vat;
      checks.push({ check: 'subtotal − discount + charges + VAT = total', expected: expected.toFixed(2), printed: gt.toFixed(2), ok: Math.abs(expected - gt) <= 0.011 });
    }
    // rows without a printed amount (price only, "-" amount) are listed, never given an invented amount
    const itemRows = table.rows.filter(r => r.kind === 'item');
    const priced = itemRows.filter(r => toNum(r.fields.lineTotal.value) != null);
    const unpriced = itemRows.filter(r => toNum(r.fields.lineTotal.value) == null);
    if (sub != null && priced.length) {
      const s = priced.reduce((a, r) => a + toNum(r.fields.lineTotal.value), 0);
      const c = { check: unpriced.length ? 'sum of ' + priced.length + ' item rows with an amount = subtotal' : 'sum of item line totals = subtotal',
                  expected: s.toFixed(2), printed: sub.toFixed(2), ok: Math.abs(s - sub) <= 0.011 };
      if (unpriced.length) { c.excludedRows = unpriced.map(r => r.id); c.note = unpriced.length + ' item row(s) without a printed amount are not included.'; }
      checks.push(c);
    }
    out.checks = checks;
    return out;
  }

  // ── item photos: where each embedded picture sits relative to the items ──
  // Input: page image placements (pages[].images = [{x, y, w, h}] in page
  // points, y from the top). Each picture is either a CANDIDATE with the item
  // block it lies in, or EXCLUDED with the reason (logo, header/footer,
  // signature area, decoration, background). A picture inside the block of a
  // titled item whose price is split into components belongs to that PARENT
  // item — it is never guessed onto one component.
  const TABLE_END_KINDS = new Set(['subtotal', 'total', 'grand_total', 'vat', 'discount', 'delivery', 'installation', 'end_marker', 'carried_forward']);
  const r1 = v => Math.round(v * 10) / 10;
  const photoKey = (page, b) => ['p' + page, r1(b.x), r1(b.y), r1(b.w), r1(b.h)].join(':');
  const groupKeyOf = parent => clean([parent.ref, parent.title].filter(Boolean).join(' '));

  function locatePhotos(pagesIn, pageLines, table) {
    const out = [];
    const seen = new Map();                      // repeated placements (letterhead logos) across pages
    for (const p of pagesIn) for (const b of p.images || []) {
      const k = [Math.round(b.x / 3), Math.round(b.y / 3), Math.round(b.w / 3), Math.round(b.h / 3)].join(':');
      if (!seen.has(k)) seen.set(k, new Set());
      seen.get(k).add(p.page);
    }
    // item blocks: one anchor per top-level item, or per titled item with components
    const groupRows = new Map();
    for (const r of table.rows) if (r.parent) {
      const g = groupKeyOf(r.parent);
      if (!groupRows.has(g)) groupRows.set(g, []);
      groupRows.get(g).push(r.id);
    }
    const anchors = [];
    for (const r of table.rows) {
      const target = r.parent
        ? { kind: 'group', parent: groupKeyOf(r.parent), parentRef: r.parent.ref || '', parentTitle: r.parent.title || '', rowIds: groupRows.get(groupKeyOf(r.parent)) }
        : { kind: 'row', rowId: r.id };
      anchors.push({ page: r.page, y: r.region.y, target });
    }
    for (const o of table.otherRows) if (o.kind === 'item_heading' && o.parent) {
      const g = groupKeyOf(o.parent);
      anchors.push({ page: o.page, y: o.region.y, target: { kind: 'group', parent: g, parentRef: o.parent.ref || '', parentTitle: o.parent.title || '', rowIds: groupRows.get(g) || [] } });
    }
    const tkey = t => t.kind === 'row' ? 'r:' + t.rowId : 'g:' + t.parent;
    anchors.sort((a, b) => a.page - b.page || a.y - b.y);
    const blocks = [];                           // consecutive anchors of the same parent form one block
    for (const a of anchors) {
      const last = blocks[blocks.length - 1];
      if (last && last.page === a.page && tkey(last.target) === tkey(a.target)) continue;
      blocks.push(a);
    }
    for (const p of pagesIn) {
      const H = p.height || 842, W = p.width || 595;
      const pl = pageLines.find(x => x.page === p.page);
      const header = pl ? pl.lines.find(l => isHeaderLine(l)) : null;
      const pageBlocks = blocks.filter(b => b.page === p.page);
      const firstY = pageBlocks.length ? pageBlocks[0].y : null;
      const lastY = pageBlocks.length ? pageBlocks[pageBlocks.length - 1].y : null;
      const ends = table.otherRows.filter(o => o.page === p.page && TABLE_END_KINDS.has(o.kind) && lastY != null && o.region.y > lastY).map(o => o.region.y);
      const tableEnd = ends.length ? Math.min(...ends) : null;
      const prevBlock = blocks.filter(b => b.page < p.page).slice(-1)[0] || null;
      for (const b of p.images || []) {
        const region = { x: r1(b.x), y: r1(b.y), w: r1(b.w), h: r1(b.h) };
        const e = { key: photoKey(p.page, b), page: p.page, region, status: 'excluded', exclude: '', target: null, confidence: 'low', reasons: [] };
        const k = [Math.round(b.x / 3), Math.round(b.y / 3), Math.round(b.w / 3), Math.round(b.h / 3)].join(':');
        const cy = b.y + b.h / 2;
        if (b.w * b.h >= W * H * 0.5) e.exclude = 'page_background';
        else if (b.w < 12 || b.h < 12 || b.w * b.h < 300) e.exclude = 'too_small';
        else if (b.w / b.h > 8 || b.h / b.w > 8) e.exclude = 'decorative_strip';
        else if (seen.get(k).size >= 2) e.exclude = 'repeated_on_pages';
        else if (!pageBlocks.length && !prevBlock) e.exclude = 'outside_item_table';
        else if (header && b.y + b.h <= header.y + header.h) e.exclude = 'above_item_table';
        else if (tableEnd != null && cy >= tableEnd) e.exclude = 'after_item_table';
        else if (!pageBlocks.length && (!prevBlock || header)) e.exclude = 'outside_item_table';
        // page margins count as header / footer only OUTSIDE the item rows: a picture of the first
        // item on a page without letterhead can sit near the top of that page
        else if (firstY != null && cy < firstY && b.y + b.h <= H * 0.12) e.exclude = 'header_or_footer_area';
        else if (!pageBlocks.length && b.y + b.h <= H * 0.12) e.exclude = 'header_or_footer_area';
        else if (lastY != null && b.y > lastY && b.y >= H * 0.9) e.exclude = 'header_or_footer_area';
        if (e.exclude) { out.push(e); continue; }
        e.status = 'candidate';
        // the block containing the picture's centre; before the first item on a continuation page
        // the picture belongs to the item carried over from the previous page (low confidence)
        let blk = null, i = -1;
        for (let j = 0; j < pageBlocks.length; j++) if (pageBlocks[j].y <= cy + 1) { blk = pageBlocks[j]; i = j; }
        if (!blk) {
          blk = prevBlock;
          e.reasons.push('before_first_item_on_page');
          e.reasons.push('continues_from_previous_page');
        }
        if (!blk) { e.status = 'excluded'; e.exclude = 'outside_item_table'; out.push(e); continue; }
        e.target = blk.target;
        e.confidence = 'high';
        e.reasons.push(blk.target.kind === 'group' ? 'inside_parent_item_block' : 'inside_item_block');
        const next = i >= 0 ? pageBlocks[i + 1] : pageBlocks[0];
        if (next && b.y + b.h > next.y + Math.min(4, b.h * 0.25)) { e.confidence = 'low'; e.reasons.push('overlaps_next_item'); }
        if (blk.y > b.y + Math.min(4, b.h * 0.25)) { e.confidence = 'low'; e.reasons.push('starts_above_item'); }
        if (e.reasons.includes('continues_from_previous_page')) e.confidence = 'low';
        out.push(e);
      }
    }
    return out;
  }

  // Draft item(s) that a located photo belongs to, if they are in the draft:
  // a row → the imported item of that printed row (same or earlier extraction);
  // a parent → every imported component of that parent from the same source.
  function photoTargetInDraft(ex, target, items, extractionId, sourceId) {
    if (!target) return null;
    if (target.kind === 'group') {
      const members = (items || []).filter(i => i.orig && i.sourceRef && i.sourceRef.sourceId === sourceId && clean(i.orig.parent || '') === target.parent);
      return members.length ? { kind: 'group', sourceId, parent: target.parent } : null;
    }
    const row = (ex.rows || []).find(r => r.id === target.rowId);
    if (!row) return null;
    const occ = occurrences(ex).get(row.id);
    const key = printedKey(sourceId, rowPrinted(row.fields), occ);
    const it = (items || []).find(i => i.sourceRef && ((i.sourceRef.extractionId === extractionId && i.sourceRef.rowId === row.id) ||
      (i.orig && printedKey(i.sourceRef.sourceId, i.orig, i.orig.occurrence) === key)));
    return it ? { kind: 'item', itemId: it.id } : null;
  }

  /**
   * planPhotos(extraction, draft, opts) → new photo associations for the draft.
   * opts: { extractionId, sourceId, items (draft items incl. those being applied),
   *         stored: [{id, page, region, kind}] (photos stored for this source), newId, now }
   * A photo that already has an association in the draft — suggested,
   * uncertain, confirmed or removed — is never touched again, so a re-run
   * cannot overwrite what the user decided.
   */
  function planPhotos(ex, draft, opts) {
    const have = new Set((draft.photos || []).map(a => a.photoId));
    const items = opts.items || draft.items || [];
    const out = [];
    for (const ph of ex.photos || []) {
      if (ph.status !== 'candidate') continue;
      const st = (opts.stored || []).find(s => s.kind === 'embedded' && s.page === ph.page &&
        ['x', 'y', 'w', 'h'].every(k => Math.abs(s.region[k] - ph.region[k]) <= 0.15));
      if (!st || have.has(st.id)) continue;
      const target = photoTargetInDraft(ex, ph.target, items, opts.extractionId, opts.sourceId);
      if (!target) continue;                     // its item is not in the draft (not imported)
      have.add(st.id);
      out.push({ id: opts.newId(), photoId: st.id, sourceId: opts.sourceId, page: ph.page, region: Object.assign({}, st.region),
        kind: 'embedded', target, status: ph.confidence === 'high' ? 'suggested' : 'uncertain', origin: 'auto', includeInPdf: false,
        reasons: ph.reasons.slice(0, 10), updatedAt: opts.now || '',
        suggestion: { extractionId: opts.extractionId, rowIds: ph.target.kind === 'group' ? (ph.target.rowIds || []).slice(0, 60) : [ph.target.rowId],
                      confidence: ph.confidence } });
    }
    return out;
  }
  // Photos shown with a draft item: its own, plus those of its parent item.
  function photosForItem(draft, item) {
    return (draft.photos || []).filter(a => a.target && (
      (a.target.kind === 'item' && a.target.itemId === item.id) ||
      (a.target.kind === 'group' && item.orig && item.sourceRef && item.sourceRef.sourceId === a.target.sourceId && clean(item.orig.parent || '') === a.target.parent)));
  }

  // ── import planning (extraction → draft), pure ───────────────────────────
  const DEFAULT_TAX = { treatment: 'taxable', rate: '15', origin: 'default' };
  const DEFAULT_TAX_FLAG = 'default_tax_requires_review';
  const IMPORTABLE_FLAGS = new Set(['low_confidence', 'ambiguous_number', 'unreadable_number', 'line_total_mismatch',
    'line_total_may_include_tax', 'lump_sum', 'lump_sum_or_missing_breakdown', 'possible_duplicate', 'multiline',
    'no_numbers', 'unit_from_quantity_cell', 'continues_next_page', 'spec_dimension_conflict', 'dash_amount', 'no_amount',
    'same_values_as_other_row']);
  const QTY_OK = v => typeof v === 'string' && /^\d{1,15}(\.\d{1,3})?$/.test(v) && Number(v) > 0;
  const PRICE_OK = v => typeof v === 'string' && /^\d{1,15}(\.\d{1,4})?$/.test(v);
  const itemSig = (desc, qty, price) => [normText(desc || ''), qty || '', price || ''].join('|');
  // Identity of a printed row within one stored document: its ORIGINAL printed
  // text (unaffected by later edits to the PO values) plus its occurrence
  // number among rows printed identically — so two legitimate identical rows
  // stay two different rows, on any page and after a re-run.
  const printedText = (o) => [o.ref, o.description, o.qty, o.unitPrice, o.lineTotal].map(v => normText(v || '')).join('|');
  const rowPrinted = f => ({ ref: f.ref.raw, description: f.description.raw, qty: f.qty.raw, unitPrice: f.unitPrice.raw, lineTotal: f.lineTotal.raw });
  function occurrences(ex) {                       // rowId → '1', '2', … in document order
    if (ex.__occ) return ex.__occ;
    const count = new Map(), out = new Map();
    for (const r of ex.rows || []) {
      const t = printedText(rowPrinted(r.fields));
      const n = (count.get(t) || 0) + 1;
      count.set(t, n); out.set(r.id, String(n));
    }
    Object.defineProperty(ex, '__occ', { value: out, enumerable: false });
    return out;
  }
  const printedKey = (sourceId, orig, occ) => sourceId + '#' + printedText(orig) + '#' + (occ || '1');
  function importedKeys(items) {
    const bySource = new Set(), byPrinted = new Set();
    for (const i of items || []) {
      if (!i.sourceRef) continue;
      bySource.add(i.sourceRef.extractionId + ':' + i.sourceRef.rowId);
      if (i.orig) byPrinted.add(printedKey(i.sourceRef.sourceId, i.orig, i.orig.occurrence));
    }
    return { bySource, byPrinted };
  }
  // Why a row cannot be imported again into this draft ('' if it can).
  function alreadyImportedReason(ex, r, draftItems, extractionId, sourceId, keys) {
    keys = keys || importedKeys(draftItems);
    if (keys.bySource.has(extractionId + ':' + r.id)) return 'already imported from this extraction';
    if (r.fields && keys.byPrinted.has(printedKey(sourceId, rowPrinted(r.fields), occurrences(ex).get(r.id))))
      return 'this printed row of the same document is already in the draft (possibly edited there)';
    return '';
  }
  // Document-level adjustments the PO does not apply yet (Phase 3).
  function unappliedAdjustments(ex) {
    const t = ex.totals || {};
    const out = [];
    for (const [k, label] of [['discount', 'document discount'], ['delivery', 'delivery / shipping charge'], ['installation', 'installation charge']])
      if (t[k] && t[k].value != null) out.push({ kind: k, label, amount: t[k].value, raw: t[k].raw || '' });
    for (const c of t.otherCharges || []) if (c.value != null) out.push({ kind: 'charge', label: 'other charge', amount: c.value, raw: c.raw || '' });
    return out;
  }

  /**
   * planImport(extraction, draft, opts) → { items, skipped, quotation, currency, priceTaxBasis }
   * opts: { extractionId, sourceId, selectedRowIds: [..], headerKeys: [..],
   *         applyCurrency: bool, applyTaxBasis: bool, newId: () => 'it_…', now: ISO string }
   * Never modifies draft.items; only proposes NEW items. Rows already imported
   * (same extraction row) or identical to an existing item are skipped.
   */
  function planImport(ex, draft, opts) {
    const existing = draft.items || [];
    const imported = importedKeys(existing);
    const bySource = imported.bySource;
    const bySig = new Set(existing.map(i => itemSig(i.description, i.qty, i.unitPrice)));
    const selected = new Set(opts.selectedRowIds || []);
    const h = ex.header || {};
    const docRate = h.taxRate && h.taxRate.value ? String(h.taxRate.value) : '';
    const docBasis = h.taxBasis && h.taxBasis.value ? h.taxBasis.value : '';
    const adjustments = unappliedAdjustments(ex);
    const incomplete = !!(ex.document && ex.document.complete === false);
    const out = { items: [], skipped: [], quotation: null, currency: null, priceTaxBasis: null, adjustments, incomplete, blocked: null };
    // A partial read may only be applied with an explicit acknowledgement.
    if (incomplete && !opts.acknowledgeIncomplete) { out.blocked = 'incomplete_not_acknowledged'; return out; }
    const occ = occurrences(ex);
    for (const r of ex.rows || []) {
      if (!selected.has(r.id)) continue;
      const f = r.fields;
      // PO item = editable NAME (printed title; a component keeps its parent item's title) +
      // DIMENSIONS exactly as printed in the W/D/H/L columns (never quantities, never inferred).
      // The full description / material specification stays internal with its evidence.
      const parentText = r.parent ? clean([r.parent.ref, r.parent.title].filter(Boolean).join(' ')) : '';
      let desc = f.description.value || '';
      if (r.parent && r.parent.title && !normText(desc).startsWith(normText(r.parent.title))) desc = r.parent.title + ' — ' + desc;
      const name = desc.split('\n')[0].trim().slice(0, 300);
      // Measurements printed only in this row's description stay with THIS item (a component keeps
      // its own), word for word — hiding the description from the PO never loses them.
      const colVals = f.dimensions && f.dimensions.value ? Object.assign({}, f.dimensions.value) : {};
      const dd = describedDims(f.description.value || '');
      const colUnit = f.dimensions && f.dimensions.unit ? f.dimensions.unit : '';
      const conflicts = Object.keys(colVals).filter(L => dd.spec[L] != null &&
        (dimNum(colVals[L]) == null || Math.abs(dd.spec[L] - dimNum(colVals[L])) > 1e-9))
        .map(L => ({ dim: L, column: colVals[L], description: String(dd.spec[L]) }));
      // the description's own size is kept unless it only repeats the column values exactly
      const mainAdds = dd.main != null && (!onlyLetterDims(dd.main, colUnit) || conflicts.length ||
        Object.keys(dd.spec).some(L => !(L in colVals)));
      const described = { main: mainAdds ? dd.main.slice(0, 300) : '', additional: dd.additional.slice(0, 8).map(s => s.slice(0, 300)) };
      const hasDescribed = !!(described.main || described.additional.length);
      const dimensions = Object.keys(colVals).length || hasDescribed ? {
        values: colVals, unit: colUnit, origin: 'extracted',
        status: conflicts.length ? 'needs_confirmation' : 'as_printed',
        conflicts: conflicts.slice(0, 8),
        described,
      } : null;
      const why = alreadyImportedReason(ex, r, existing, opts.extractionId, opts.sourceId, imported);
      if (why) { out.skipped.push({ rowId: r.id, reason: why }); continue; }
      const qty = QTY_OK(f.qty.value) ? f.qty.value : '';
      const price = PRICE_OK(f.unitPrice.value) ? f.unitPrice.value : '';
      const flags = new Set((r.flags || []).filter(x => IMPORTABLE_FLAGS.has(x)));
      // identical PO values elsewhere in the draft: shown, never silently dropped
      if (desc && bySig.has(itemSig(desc, qty, price))) flags.add('same_as_existing_item');
      if (r.kind === 'optional') flags.add('optional_selected');
      if (r.kind === 'alternative') flags.add('alternative_selected');
      if (f.discount && f.discount.value != null) flags.add('line_discount_present');
      if (adjustments.length) flags.add('document_adjustments_unapplied');
      if (incomplete) flags.add('from_incomplete_extraction');
      if (r.parent) flags.add('component_of_item');
      let tax;
      const cell = f.tax && f.tax.value;
      if (cell && cell.treatment === 'taxable' && cell.rate) tax = { treatment: 'taxable', rate: cell.rate, origin: 'extracted' };
      else if (cell && (cell.treatment === 'zero_rated' || cell.treatment === 'exempt')) tax = { treatment: cell.treatment, rate: '', origin: 'extracted' };
      else if (docRate && Number(docRate) > 0) tax = { treatment: 'taxable', rate: docRate, origin: 'extracted' };
      else { tax = Object.assign({}, DEFAULT_TAX); flags.add(DEFAULT_TAX_FLAG); }
      if (cell && cell.amount != null && !cell.rate) flags.add('tax_amount_without_rate');
      const unit = (f.unit.value || '').slice(0, 40);
      out.items.push({
        id: opts.newId(), name, description: desc.slice(0, 5000), dimensions, unit, qty, unitPrice: price, included: true,
        tax, source: 'extracted', createdAt: opts.now || '', excludedAt: '',
        sourceRef: { sourceId: opts.sourceId, extractionId: opts.extractionId, rowId: r.id },
        orig: {
          ref: f.ref.raw || '', description: f.description.raw || '', unit: f.unit.raw || '', qty: f.qty.raw || '',
          unitPrice: f.unitPrice.raw || '', discount: f.discount.raw || '', lineTotal: f.lineTotal.raw || '', tax: f.tax.raw || '',
          occurrence: occ.get(r.id) || '1', parent: parentText, dimensions: f.dimensions ? f.dimensions.raw || '' : '',
          proposedDescription: desc !== (f.description.raw || '') ? desc.slice(0, 5000) : '',
        },
        reviewFlags: [...flags].sort(),
      });
      bySig.add(itemSig(desc, qty, price));
    }
    // "other rows" (unstructured / uncertain lines) the user explicitly chose to
    // import: description only — quantity and price are never inferred.
    const otherSel = new Set(opts.selectedOtherIds || []);
    for (const o of ex.otherRows || []) {
      if (!otherSel.has(o.id)) continue;
      if (bySource.has(opts.extractionId + ':' + o.id)) { out.skipped.push({ rowId: o.id, reason: 'already imported from this extraction' }); continue; }
      out.items.push({
        id: opts.newId(), description: String(o.text || '').slice(0, 5000), unit: '', qty: '', unitPrice: '', included: true,
        tax: Object.assign({}, DEFAULT_TAX), source: 'extracted', createdAt: opts.now || '', excludedAt: '',
        sourceRef: { sourceId: opts.sourceId, extractionId: opts.extractionId, rowId: o.id },
        orig: { ref: '', description: String(o.text || ''), unit: '', qty: '', unitPrice: '', discount: '', lineTotal: o.amountRaw || '', tax: '', occurrence: '1' },
        reviewFlags: [DEFAULT_TAX_FLAG, 'no_numbers'].concat((o.flags || []).filter(x => IMPORTABLE_FLAGS.has(x)))
          .concat(adjustments.length ? ['document_adjustments_unapplied'] : []).concat(incomplete ? ['from_incomplete_extraction'] : []).sort(),
      });
    }
    const keys = new Set(opts.headerKeys || []);
    if (keys.size) {
      const v = k => (h[k] && h[k].value != null && typeof h[k].value !== 'object') ? String(h[k].value) : '';
      const d = h.quotationDate && h.quotationDate.value;
      const q = { sourceId: opts.sourceId, extractionId: opts.extractionId, appliedAt: opts.now || '',
        ref: keys.has('quotationRef') ? v('quotationRef') : '',
        dateRaw: keys.has('quotationDate') && d ? d.raw : '', dateIso: keys.has('quotationDate') && d && d.iso ? d.iso : '',
        dateHijri: keys.has('quotationDate') && d && d.hijri ? d.hijri : '', dateCalendar: keys.has('quotationDate') && d ? d.calendar : '',
        dateAmbiguous: !!(keys.has('quotationDate') && d && d.ambiguous),
        validity: keys.has('validity') ? v('validity') : '', supplierName: keys.has('supplierName') ? v('supplierName') : '',
        supplierVat: keys.has('supplierVat') ? v('supplierVat') : '', supplierCr: keys.has('supplierCr') ? v('supplierCr') : '',
        supplierEmail: keys.has('supplierEmail') ? v('supplierEmail') : '', supplierPhone: keys.has('supplierPhone') ? v('supplierPhone') : '',
        currency: keys.has('currency') ? v('currency') : '', currencyRaw: keys.has('currency') && h.currency ? h.currency.raw : '',
        paymentTerms: keys.has('paymentTerms') ? v('paymentTerms') : '', delivery: keys.has('delivery') ? v('delivery') : '',
        taxBasis: keys.has('taxBasis') ? v('taxBasis') : '', taxRate: keys.has('taxBasis') ? docRate : '',
        taxStatementRaw: keys.has('taxBasis') && h.taxBasis ? h.taxBasis.raw : '',
        exclusions: keys.has('exclusions') ? v('exclusions') : '', notes: keys.has('notes') ? v('notes') : '',
        customerName: keys.has('customerName') ? v('customerName') : '', projectName: keys.has('projectName') ? v('projectName') : '',
        totals: {}, flags: {} };
      const t = ex.totals || {};
      if (keys.has('totals')) for (const k of ['subtotal', 'discount', 'delivery', 'installation', 'vat', 'grandTotal', 'total'])
        q.totals[k] = t[k] && t[k].value != null ? String(t[k].value) : '';
      for (const k of keys) if (h[k] && h[k].flags && h[k].flags.length) q.flags[k] = h[k].flags.slice(0, 20);
      if (incomplete) q.flags.document = ['incomplete_extraction_acknowledged', 'read ' + rangeText(ex.document.processedPages) + ' of ' + ex.document.totalPages];
      if (adjustments.length) q.flags.adjustments = adjustments.slice(0, 20).map(a => ('unapplied ' + a.label + ' ' + a.amount).slice(0, 80));
      for (const k of Object.keys(q)) if (typeof q[k] === 'string') q[k] = q[k].slice(0, 2000);
      out.quotation = q;
      if (opts.applyCurrency && keys.has('currency') && v('currency')) out.currency = v('currency');
      if (opts.applyTaxBasis && keys.has('taxBasis') && (docBasis === 'inclusive' || docBasis === 'exclusive')) out.priceTaxBasis = docBasis;
    }
    return out;
  }

  // ── Daftra supplier suggestions (never auto-selected) ─────────────────────
  const NAME_STOP = new Set(['co', 'company', 'est', 'establishment', 'trading', 'for', 'and', 'the', 'ltd', 'llc', 'group', 'corp',
    'شركة', 'شركه', 'مؤسسة', 'مؤسسه', 'للتجارة', 'للتجاره', 'التجارية', 'التجاريه', 'المحدودة', 'المحدوده', 'و']);
  const nameTokens = s => normText(s).replace(/[^\p{L}\p{N}\s]/gu, ' ').split(' ').filter(t => t && !NAME_STOP.has(t));
  function nameSimilarity(a, b) {
    const x = new Set(nameTokens(a)), y = new Set(nameTokens(b));
    if (!x.size || !y.size) return 0;
    let inter = 0; for (const t of x) if (y.has(t)) inter++;
    return inter / Math.min(x.size, y.size);
  }
  function suggestSuppliers(header, suppliers) {
    const vat = header && header.supplierVat && header.supplierVat.value ? String(header.supplierVat.value) : '';
    const name = header && header.supplierName && header.supplierName.value ? String(header.supplierName.value) : '';
    const out = [];
    for (const s of suppliers || []) {
      const reasons = [];
      let score = 0;
      if (vat && s.snapshot && s.snapshot.vatNumber && s.snapshot.vatNumber === vat) { score += 100; reasons.push('VAT number matches'); }
      const sim = name ? nameSimilarity(name, s.name) : 0;
      if (sim >= 0.5) { score += Math.round(sim * 50); reasons.push('name similar'); }
      if (score) out.push({ id: s.id, name: s.name, score, reasons });
    }
    return out.sort((a, b) => b.score - a.score).slice(0, 3);
  }
  function supplierMismatch(header, supplier) {
    if (!header || !supplier) return [];
    const out = [];
    const vat = header.supplierVat && header.supplierVat.value;
    const sv = supplier.snapshot && supplier.snapshot.vatNumber;
    if (vat && sv && vat !== sv) out.push({ code: 'vat_mismatch', message: 'Quotation VAT ' + vat + ' ≠ Daftra VAT ' + sv });
    if (vat && !sv) out.push({ code: 'vat_not_in_daftra', message: 'Quotation shows VAT ' + vat + '; the Daftra supplier has no VAT number recorded.' });
    const name = header.supplierName && header.supplierName.value;
    if (name && supplier.name && nameSimilarity(name, supplier.name) < 0.5)
      out.push({ code: 'name_differs', message: 'Quotation name “' + name + '” differs from Daftra name “' + supplier.name + '”.' });
    return out;
  }

  return { planPhotos, photosForItem, photoTargetInDraft, VERSION, extract, parseAmount, parseQty, parseDates, parseTaxCell, buildLines, matchHeaderKey, normDigits, normText, LOW_CONF,
           planImport, alreadyImportedReason, unappliedAdjustments, occurrences, suggestSuppliers, supplierMismatch, nameSimilarity, DEFAULT_TAX, DEFAULT_TAX_FLAG };
});
