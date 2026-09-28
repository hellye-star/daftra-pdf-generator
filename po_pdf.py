"""
Vista Platform — PO Generator: purchase-order PDF (draft preview and issued PO).

build_html() lays out one PO from a validated draft, its server-computed totals
and the PO settings, in the approved order for every item:

    item name → dimensions / printed measurements → quantity & pricing → selected photos

The internal description / material specification is never printed. Photos
appear only when ticked "Include in PO PDF"; a photo shared by a parent item is
printed once, under the parent's first included component.

html_to_pdf() prints that HTML with the locally installed Microsoft Edge (or
Chrome) in headless mode, with a throwaway profile, no network resources (all
images are embedded as data URIs) and no header/footer. Nothing leaves this PC.

The document is self-contained: an issued PDF is rendered once, stored as an
immutable file with its SHA-256, and every reprint serves those stored bytes —
later template changes cannot alter a historical PO.
"""
import base64
import datetime
import html
import os
import shutil
import subprocess
import tempfile

TEMPLATE_VERSION = 'po-pdf-3'   # po-pdf-1/2: earlier layouts (their stored PDFs are never re-rendered)

# The approved Vista United logo (the same logo.png the Delivery Note prints), embedded as a
# data URI so rendering never loads anything from outside the document.
_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo.png')
_logo_uri = None


def logo_data_uri():
    global _logo_uri
    if _logo_uri is None:
        try:
            with open(_LOGO_PATH, 'rb') as f:
                data = f.read()
        except OSError:
            raise PdfRenderError('The Vista logo (logo.png) is missing, so the PO PDF cannot be produced.')
        if not data.startswith(b'\x89PNG'):
            raise PdfRenderError('The Vista logo (logo.png) is not a PNG image.')
        _logo_uri = 'data:image/png;base64,' + base64.b64encode(data).decode()
    return _logo_uri

_BROWSERS = (
    os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
    os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
    os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
)


class PdfRenderError(Exception):
    pass


def e(v):
    return html.escape('' if v is None else str(v), quote=True)


def item_title(it):
    """The printed item name: the user's name, else the FULL first line of the
    description (legacy items) — never the whole narrative specification."""
    return (it.get('name') or '').strip() or (it.get('description') or '').split('\n')[0].strip() or '—'


def dims_lines(it):
    d = it.get('dimensions')
    if not d:
        return []
    out = []
    if d.get('values'):
        out.append(' × '.join(f'{k} {v if v else "—"}' for k, v in d['values'].items()) + (f' {d["unit"]}' if d.get('unit') else ''))
    des = d.get('described') or {}
    if des.get('main'):
        out.append(des['main'])
    out.extend(x for x in des.get('additional', []) if x)
    return out


def _money(v):
    if v is None or v == '':
        return '—'   # e.g. a missing unit price: the totals block lists it as a missing input
    neg = v.startswith('-')
    whole, _, frac = v.lstrip('-').partition('.')
    whole = f'{int(whole):,}'
    return ('-' if neg else '') + whole + ('.' + frac if frac else '')


def _date(ts):
    if not ts:
        return ''
    try:
        return datetime.datetime.strptime(ts[:10], '%Y-%m-%d').strftime('%d %b %Y')
    except ValueError:
        return ts[:10]


TAX_TEXT = {'taxable': 'VAT', 'zero_rated': 'Zero-rated', 'exempt': 'Exempt', 'out_of_scope': 'Out of scope'}


def missing_inputs(doc, computed):
    """Why the server could not calculate the totals, as printable lines ([] when complete)."""
    t = computed['totals']
    st = t.get('status')
    if st == 'complete':
        return []
    if st == 'currency_unresolved':
        return ['Currency not selected.']
    if st == 'no_items':
        return ['No items are included in this PO.']
    if st == 'negative_total':
        return ['The total excluding VAT is negative — check discounts and adjustments.']
    out = []
    lines = {ln['id']: ln for ln in computed['lines']}
    items = [it for it in doc.get('items', []) if it['included']]
    for n, it in enumerate(items, 1):
        issues = lines.get(it['id'], {}).get('issues') or []
        if issues:
            out.append(f'Item {n} ({item_title(it)}): ' + ', '.join(issues) + '.')
    adj = {a['id']: a for a in computed.get('adjustments', [])}
    for a in doc.get('adjustments', []):
        issues = adj.get(a['id'], {}).get('issues') or []
        if issues:
            out.append(f'{a.get("label") or ("Discount" if a["kind"] == "discount" else "Charge")}: ' + ', '.join(issues) + '.')
    return out or [f'Totals not calculated ({st}).']


def item_photos(doc):
    """{item_id: [assoc, ...]} for photos ticked for the PDF. Parent (group) photos go
    under the parent's first INCLUDED component, in draft order."""
    out = {}
    items = [it for it in doc.get('items', []) if it['included']]
    for a in doc.get('photos', []):
        if a['status'] == 'removed' or not a['includeInPdf'] or not a['target']:
            continue
        t = a['target']
        if t['kind'] == 'item':
            if any(it['id'] == t['itemId'] for it in items):
                out.setdefault(t['itemId'], []).append(a)
        else:
            first = next((it for it in items if it.get('orig') and it.get('sourceRef')
                          and it['sourceRef']['sourceId'] == t['sourceId'] and (it['orig'].get('parent') or '').strip() == t['parent']), None)
            if first:
                out.setdefault(first['id'], []).append(a)
    return out


def build_html(doc, computed, settings, issue=None, photo_bytes=None):
    """issue: None for a draft preview, else {displayNo, revisionNo, issuedAt, approvedBy,
    reason, previous: {displayNo, issuedAt} | None}. photo_bytes: {photoId: (mime, bytes)}."""
    photo_bytes = photo_bytes or {}
    t = computed['totals']
    lines = {ln['id']: ln for ln in computed['lines']}
    adj_rows = {a['id']: a for a in computed.get('adjustments', [])}
    buyer = settings.get('buyer') or {}
    sup = doc.get('supplier') or {}
    snap = sup.get('snapshot') or {}
    q = doc.get('quotation') or {}
    cur = doc.get('currency') or ''
    items = [it for it in doc.get('items', []) if it['included']]
    photos = item_photos(doc)
    any_disc = any(lines.get(it['id'], {}).get('discount') for it in items)
    is_draft = issue is None
    number = e(issue['displayNo']) if issue else 'DRAFT — not issued'

    rows = []
    for n, it in enumerate(items, 1):
        ln = lines.get(it['id'], {})
        dl = ''.join(f'<div class="dim">{e(x)}</div>' for x in dims_lines(it))
        tax = it['tax']
        tax_txt = TAX_TEXT.get(tax['treatment'], '') + (f' {tax["rate"]}%' if tax['treatment'] == 'taxable' and tax.get('rate') else '')
        block = [f'''<tr class="it"><td class="n">{n}</td>
          <td><div class="nm" dir="auto">{e(item_title(it))}</div>{dl}</td>
          <td class="r">{e(it["qty"])}</td><td>{e(it["unit"])}</td><td class="r">{e(_money(it["unitPrice"]))}</td>
          {f'<td class="r">{e(_money(ln.get("discount")) if ln.get("discount") else "")}</td>' if any_disc else ''}
          <td class="r">{e(_money(ln.get("lineAmount")))}<div class="tx">{e(tax_txt)}</div></td></tr>''']
        ph = photos.get(it['id']) or []
        imgs = []
        for a in ph:
            got = photo_bytes.get(a['photoId'])
            if got:
                mime, data = got
                imgs.append(f'<img src="data:{e(mime)};base64,{base64.b64encode(data).decode()}" alt="">')
        if imgs:
            parent = next((x['target']['parent'] for x in ph if x['target']['kind'] == 'group'), None)
            block.append(f'<tr class="ph"><td></td><td colspan="{6 if any_disc else 5}">'
                        f'{"<div class=shared>Photos of " + e(parent) + " (shared by its components)</div>" if parent else ""}'
                        f'<div class="phs">{"".join(imgs)}</div></td></tr>')
        # one table body per item: the item and its photos are never split across pages
        rows.append('<tbody class="blk">' + ''.join(block) + '</tbody>')

    missing = missing_inputs(doc, computed)
    tot = [f'<tr><td>Items subtotal</td><td class="r">{e(_money(t.get("itemsNet")))}</td></tr>'] if doc.get('adjustments') else []
    for a in doc.get('adjustments', []):
        r = adj_rows.get(a['id'], {})
        tot.append(f'<tr><td>{e(a["label"] or ("Discount" if a["kind"] == "discount" else "Charge"))}</td><td class="r">{e(_money(r.get("net")))}</td></tr>')
    tot.append(f'<tr class="sub"><td>Total excluding VAT</td><td class="r">{e(_money(t.get("net")))}</td></tr>')
    for g in t.get('taxGroups', []):   # VAT per rate on its taxable base (ZATCA BR-CO-17), shown for payment planning
        label = f'VAT {e(g["rate"])}% on {e(_money(g["net"]))}' if g['treatment'] == 'taxable' else f'{e(TAX_TEXT.get(g["treatment"], g["treatment"]))} — no VAT, on {e(_money(g["net"]))}'
        tot.append(f'<tr><td>{label}</td><td class="r">{e(_money(g["tax"]))}</td></tr>')
    tot.append(f'<tr class="sub"><td>VAT total</td><td class="r">{e(_money(t.get("tax")))}</td></tr>')
    tot.append(f'<tr class="gt"><td>Total including VAT ({e(cur)})</td><td class="r">{e(_money(t.get("gross")))}</td></tr>')
    if missing:   # never print blank or zero totals: say exactly what is missing instead
        tot_html = ('<div class="miss"><b>Totals not calculated</b> — missing or unresolved inputs:<ul>'
                    + ''.join(f'<li dir="auto">{e(x)}</li>' for x in missing) + '</ul></div>')
    else:
        tot_html = f'<table class="tot">{"".join(tot)}</table>'

    pt = doc.get('paymentTerms') or {}
    ms = {m['id']: m for m in computed.get('milestones', [])}
    ms_rows = ''.join(f'<tr><td>{e(m["label"])}</td><td class="r">{e(m["pct"])}%</td><td class="r">{e(_money(ms.get(m["id"], {}).get("amount")) if not missing else "not calculated")}</td></tr>'
                      for m in pt.get('milestones', []))

    rev_html = ''
    if issue and issue.get('revisionNo'):
        prev = issue.get('previous') or {}
        rev_html = (f'<div class="rev"><b>Revision {e(issue["revisionNo"])}</b> — supersedes {e(prev.get("displayNo"))} issued '
                    f'{e(_date(prev.get("issuedAt")))}. Reason: {e(issue.get("reason"))}</div>')
    approval = ''
    if issue and issue.get('approvedBy'):
        approval = f'<div>Approved by: <b>{e(issue["approvedBy"])}</b></div>'
    basis = {'exclusive': 'Prices exclude VAT', 'inclusive': 'Prices include VAT'}.get(doc.get('priceTaxBasis'), '')

    DRAFT_PAGE_CSS = (' @top-center {{ content: "DRAFT — NOT ISSUED — NOT A VALID PURCHASE ORDER"; font: 700 8pt "Segoe UI", Arial, sans-serif; color: #b3261e; letter-spacing: .1em; }}'
                      ' @bottom-center {{ content: "DRAFT — NOT ISSUED"; font: 700 8pt "Segoe UI", Arial, sans-serif; color: #b3261e; letter-spacing: .1em; }}'
                      ).replace('{{', '{').replace('}}', '}') if is_draft else ''
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>Purchase Order — Not a Tax Invoice — {number}</title><style>
@page {{ size: A4; margin: 14mm 12mm 16mm;{DRAFT_PAGE_CSS} }}
body {{ font-family: "Segoe UI", Tahoma, Arial, sans-serif; font-size: 9.5pt; color: #1c1c1a; margin: 0; }}
.top {{ display: flex; justify-content: space-between; gap: 16px; border-bottom: 2px solid #1c1c1a; padding-bottom: 8px; }}
.top h1 {{ font-size: 17pt; margin: 0; letter-spacing: .06em; }} .nti {{ font-size: 8pt; letter-spacing: .14em; text-transform: uppercase; color: #6b6b67; margin-bottom: 4px; }}
.muted {{ color: #6b6b67; }} .r {{ text-align: right; }} .n {{ width: 18px; color: #6b6b67; }}
.blocks {{ display: flex; gap: 16px; margin: 10px 0; }} .blocks > div {{ flex: 1; border: 1px solid #d8d5cf; padding: 6px 8px; }}
.lbl {{ font-size: 7.5pt; letter-spacing: .12em; text-transform: uppercase; color: #6b6b67; margin-bottom: 3px; }}
table.items {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
table.items th {{ font-size: 7.5pt; text-transform: uppercase; letter-spacing: .08em; color: #6b6b67; text-align: left; border-bottom: 1px solid #1c1c1a; padding: 4px; }}
table.items td {{ padding: 5px 4px; vertical-align: top; }}
tr.it td {{ border-top: 1px solid #d8d5cf; }} tbody.blk {{ break-inside: avoid; page-break-inside: avoid; }}
.nm {{ font-weight: 600; }} .dim {{ color: #3d3d3a; margin-top: 1px; }} .tx {{ font-size: 7.5pt; color: #6b6b67; }}
.phs {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 6px; }} .phs img {{ height: 34mm; max-width: 60mm; object-fit: contain; border: 1px solid #d8d5cf; }}
.shared {{ font-size: 7.5pt; color: #6b6b67; }}
.bottom {{ display: flex; gap: 16px; margin-top: 10px; page-break-inside: avoid; }} .bottom > div {{ flex: 1; }}
table.tot {{ width: 100%; border-collapse: collapse; }} table.tot td {{ padding: 3px 4px; border-bottom: 1px solid #eeece8; }}
table.tot tr.sub td {{ font-weight: 600; }} table.tot tr.gt td {{ font-weight: 700; font-size: 11pt; border-top: 2px solid #1c1c1a; }}
table.ms {{ width: 100%; border-collapse: collapse; margin-top: 4px; }} table.ms td {{ padding: 2px 4px; border-bottom: 1px solid #eeece8; }}
.rev {{ border: 1px solid #b06b1a; background: #fef3e7; padding: 5px 8px; margin-top: 8px; }}
.test {{ border: 2px solid #b3261e; color: #b3261e; font-weight: 700; text-align: center; padding: 4px; margin-bottom: 6px; }}
/* fixed = repeated on every printed page; drawn ABOVE photos and rows so no page hides it */
.wm {{ position: fixed; top: 36%; left: 0; right: 0; text-align: center; font-size: 110pt; letter-spacing: .08em; color: rgba(179,38,30,.16); transform: rotate(-28deg); font-weight: 700; z-index: 10; pointer-events: none; }}
.logo {{ position: relative; width: 35mm; height: 13.3mm; overflow: hidden; margin-bottom: 4px; }}
.logo img {{ position: absolute; width: 58.73mm; left: -14.07mm; top: -22.68mm; }}   /* crops the padded 1024px logo.png to the mark, as on the Delivery Note */
.miss {{ border: 2px solid #b3261e; color: #b3261e; padding: 6px 8px; }} .miss ul {{ margin: 4px 0 0 16px; padding: 0; }}
.sign {{ display: flex; gap: 24px; margin-top: 22px; page-break-inside: avoid; }} .sign > div {{ flex: 1; border-top: 1px solid #1c1c1a; padding-top: 3px; }}
.foot {{ margin-top: 14px; font-size: 7pt; color: #6b6b67; }}
</style></head><body>
{'<div class="wm">DRAFT</div>' if is_draft else ''}
{'<div class="test">TEST DOCUMENT — fictional settings — not a valid purchase order</div>' if settings.get('testMode') else ''}
<div class="top"><div><div class="logo"><img src="{logo_data_uri()}" alt="Vista United"></div>
  <div style="font-size:12pt;font-weight:700" dir="auto">{e(buyer.get("name"))}</div>
  {f'<div dir="rtl">{e(buyer.get("nameAr"))}</div>' if buyer.get("nameAr") else ''}
  <div class="muted" dir="auto">{e(buyer.get("address"))}</div>
  <div class="muted">VAT {e(buyer.get("vat"))}{(" · CR " + e(buyer.get("cr"))) if buyer.get("cr") else ""}</div>
  <div class="muted">{e(" · ".join(x for x in (buyer.get("phone"), buyer.get("email")) if x))}</div></div>
  <div class="r"><h1>PURCHASE ORDER</h1><div class="nti">Not a tax invoice</div><div><b>{number}</b></div>
  <div class="muted">{("Date " + e(_date(issue["issuedAt"]))) if issue else "Not a valid purchase order until issued"}</div></div></div>
{rev_html}
<div class="blocks"><div><div class="lbl">Supplier</div><div dir="auto"><b>{e(sup.get("name"))}</b></div>
  <div class="muted" dir="auto">{e(" ".join(x for x in (snap.get("address1"), snap.get("address2"), snap.get("city")) if x))}</div>
  <div class="muted">{("VAT " + e(snap.get("vatNumber"))) if snap.get("vatNumber") else ""}{(" · CR " + e(snap.get("crNumber"))) if snap.get("crNumber") else ""}</div>
  <div class="muted">{e(" · ".join(x for x in (snap.get("phone1"), snap.get("email")) if x))}</div></div>
  <div><div class="lbl">Reference</div>
  {f'<div>Supplier quotation: <b dir="auto">{e(q.get("ref"))}</b>{(" · " + e(q.get("dateRaw"))) if q.get("dateRaw") else ""}</div>' if q.get("ref") else ''}
  {f'<div dir="auto">Project: {e(doc.get("projectRef"))}</div>' if doc.get("projectRef") else ''}
  {f'<div dir="auto">Delivery to: {e(doc.get("deliveryLocation"))}</div>' if doc.get("deliveryLocation") else ''}
  <div>Currency: {e(cur)}{(" · " + basis) if basis else ""}</div></div></div>
<table class="items"><thead><tr><th>#</th><th>Item</th><th class="r">Qty</th><th>Unit</th><th class="r">Unit price</th>
  {'<th class="r">Discount</th>' if any_disc else ''}<th class="r">Line total</th></tr></thead>{"".join(rows)}</table>
<div class="bottom"><div><div class="lbl">Payment terms</div><div dir="auto" style="white-space:pre-wrap">{e(pt.get("text"))}</div>
  <table class="ms">{ms_rows}</table>
  {f'<div class="lbl" style="margin-top:8px">Notes</div><div dir="auto" style="white-space:pre-wrap">{e(doc.get("poNotes"))}</div>' if doc.get("poNotes") else ''}</div>
  <div>{tot_html}</div></div>
{approval}
<div class="sign"><div>Authorised signature — {e(buyer.get("name"))}</div><div>Supplier acknowledgement</div></div>
<div class="foot">{number} · {e(TEMPLATE_VERSION)}{(" · " + e(issue.get("displayNo")) + " issued " + e(_date(issue["issuedAt"]))) if issue else " · draft preview — not issued"}</div>
</body></html>'''


def find_browser():
    override = os.environ.get('VISTA_PO_PDF_BROWSER')
    for c in ((override,) if override else ()) + _BROWSERS:
        if c and os.path.isfile(c):
            return c
    return None


def html_to_pdf(doc_html, timeout=90):
    """Print HTML to PDF with a local headless browser. Returns the PDF bytes."""
    browser = find_browser()
    if not browser:
        raise PdfRenderError('No Microsoft Edge or Google Chrome found on this PC to produce the PDF.')
    work = tempfile.mkdtemp(prefix='vista-po-pdf-')
    try:
        src = os.path.join(work, 'po.html')
        out = os.path.join(work, 'po.pdf')
        with open(src, 'w', encoding='utf-8') as f:
            f.write(doc_html)
        url = 'file:///' + src.replace('\\', '/')
        try:
            subprocess.run([browser, '--headless=new', '--disable-gpu', '--no-first-run', '--disable-extensions',
                            '--no-pdf-header-footer', f'--user-data-dir={os.path.join(work, "profile")}',
                            f'--print-to-pdf={out}', url],
                           check=True, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError) as ex:
            raise PdfRenderError(f'The PDF could not be produced ({type(ex).__name__}).')
        if not os.path.isfile(out):
            raise PdfRenderError('The PDF could not be produced.')
        with open(out, 'rb') as f:
            data = f.read()
        if not data.startswith(b'%PDF-'):
            raise PdfRenderError('The PDF output is not a PDF.')
        return data
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render(doc, computed, settings, issue=None, photo_bytes=None):
    return html_to_pdf(build_html(doc, computed, settings, issue, photo_bytes))
