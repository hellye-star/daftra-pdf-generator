# CLAUDE_CONTEXT — Daftra PDF Generator

## Project

Single-file HTML tool (`daftra-pdf-generator_1.html`) for Vista United Co.
Connects to Daftra ERP via API, fetches invoices and quotations, and generates
branded bilingual (English + Arabic) PDF documents.

---

## Stack

- Pure HTML/CSS/JS — no build step, open directly in browser
- `html2pdf.js` (v0.10.1) via CDN for PDF export
- Fonts: Cormorant Garamond (headers), Jost (body), Cairo (Arabic)
- GitHub repo: `hellye-star/daftra-pdf-generator`
- Active branch: `fix/email-fallback-consistency`

---

## Data Sources

### Daftra API base
```
https://{subdomain}.daftra.com/api2
Authorization: APIKEY header
```

### Endpoints used
| Endpoint | Purpose |
|----------|---------|
| `GET /invoices.json?limit=100&page=1` | Fetch invoice list |
| `GET /estimates.json?limit=100&page=1` | Fetch quotation list |
| `GET /invoices/{id}.json` | Full invoice detail + line items |
| `GET /estimates/{id}.json` | Full quotation detail + line items |
| `GET /clients/{id}.json` | Fallback for client VAT/CR if missing from invoice |

**Do not use** `/customers` or `/contacts` — both return 404.

### Key Daftra field names (confirmed from live API)
| Field | Location | Meaning |
|-------|----------|---------|
| `data.Invoice.client_bn1` | Invoice response | Client VAT number |
| `data.Invoice.client_bn2` | Invoice response | Client CR number |
| `data.Client.bn1` | Client response | Client VAT (fallback) |
| `data.Client.bn2` | Client response | Client CR (fallback) |
| `data.Invoice.client_id` | Invoice response | Used for client fallback fetch |
| `InvoiceItem[]` / `EstimateItem[]` | Invoice/Estimate response | Line items |
| `it.price` | Line item | Unit price (not `unit_price`) |
| `it.product_name` / `it.name` | Line item | Item name |
| `doc.summary_subtotal` | Invoice | Subtotal before VAT |
| `doc.total_amount` | Invoice | Total including VAT |

### VAT Calculation
```js
const taxAmt = parseFloat(doc.summary_tax || doc.tax_amount || 0)
               || Math.max(0, total - subtotal);
```
Daftra sometimes returns `summary_tax = 0` even when VAT is included in total.
Fall back to `total - subtotal` when the explicit field is zero.

### Arabic numeral conversion
Daftra may return VAT/CR numbers in Arabic-Indic digits (٣٠٠...).
`toEnDigits()` converts display-only — source data is never altered.
```js
function toEnDigits(str) {
  return String(str).replace(/[٠-٩]/g, d => String(d.charCodeAt(0) - 0x0660));
}
```

---

## Pre-filled Company Defaults

These values are hardcoded into the sidebar form and JS fallbacks:

| Field | Value |
|-------|-------|
| Subdomain | `vistaunited` |
| API Key | `6643b05a97fbd5dfaca13ccec661f31b9c36889b` |
| Company Name | Vista United Co. |
| Tagline | Brand Experience Studio |
| Address | Jeddah, KSA, Al Muhammadiya Dist, Muhammad Ibn 23625 Hatib Street |
| Website | vistaunited.co |
| Phone | +966 55 568 4753 |
| Email | youssef@vistaunited.co |
| VAT | 314573358300003 |
| CR | 7053413683 |

---

## Design Decisions

### PDF page dimensions
- `width: 210mm`, no fixed height, `box-sizing: border-box`
- `padding: 28px 38px 24px 38px`
- No `min-height: 297mm` — page sizes to content to prevent blank second page
- No `display: flex` on the page — footer flows naturally after content

### Bilingual layout
- English labels rendered first, Arabic directly below as a sibling element
- Arabic uses Cairo font; English uses Jost/Cormorant Garamond
- `pp-info-grid` is CSS grid (2 columns, `gap: 34px`) — not flexbox
- Both "Issued To" and "Issued By" columns are structurally identical so
  headings always align at the same baseline
- Arabic section headings: `text-align: left` (no `direction: rtl` on the
  container) so they start from the left edge matching the English above

### VAT/CR display in invoice body
Three-column row: English label (fixed 48px) | Arabic label (fixed 64px) | value
Ensures number values always align regardless of label length.

### Footer
Contact info only — company name, email, phone, address, website.
**VAT and CR are intentionally excluded from the footer.**
Footer uses `margin-top: 32px`, natural flow, `page-break-inside: avoid`.

### PDF export (html2pdf settings)
```js
// IMPORTANT: .set() MUST come before .from() — options must be set before element capture
html2pdf().set({
  margin: 0,
  filename: filename,
  image: { type: 'jpeg', quality: 0.98 },
  pagebreak: {
    mode: ['css', 'legacy'],
    avoid: ['.pp-footer', '.pp-summary-block', '.pp-info-grid', '.pp-totals']
  },
  html2canvas: { scale: 2, useCORS: true, scrollY: 0, logging: false },
  jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
}).from(el).save()
```
- `scrollY: 0` prevents phantom blank page from scroll offset
- No `width` or `windowWidth` constraints on html2canvas — caused left-side clipping
- `unit: 'mm'` matches `210mm` page width
- **Never reorder `.set()` after `.from()`** — doing so breaks option application and silently corrupts the export
- **Future layout changes must not touch:** `downloadPDF()` function body, html2pdf options, the `#pdfPage` selector, or the Download PDF button's `onclick` attribute. Isolate all layout work to CSS and the `renderPreview()` HTML template only.
- **Logo must stay as base64 data URL** — do not replace `${LOGO_DATA_URL}` with a file path. Local paths break html2canvas during PDF export.

### Preview scaling
The on-screen preview wrapper uses `transform: scale(1.8)` for readability.
This does **not** affect the exported PDF — html2pdf captures `#pdfPage` directly.

### Logo
Logo is embedded as a base64 data URL in the JS constant `LOGO_DATA_URL` at the top of the script block.
The `<img>` in `renderPreview()` uses `src="${LOGO_DATA_URL}"` — this works in both the browser preview and html2pdf export.
CSS: `width: 145px; height: auto; display: block; object-fit: contain`
Source file: `logo.png` (copied from `logo.png.png.png`, Windows triple-extension artifact).
**Never switch back to a local file path (`./logo.png`) in the template** — html2canvas cannot load local file paths, which silently breaks PDF export.
To regenerate the base64: `base64 -w 0 logo.png` then prefix with `data:image/png;base64,` and update `LOGO_DATA_URL`.

---

## QR Code

### Rule — Never generate QR locally
Only use the official QR field returned by Daftra. Do NOT use any QR library, construct ZATCA QR from invoice data, or generate a replacement if the field is missing.

### Detection
`openDoc()` scans these candidate keys on both the raw response wrapper and the parsed Invoice object:
`qr`, `qr_code`, `qr_image`, `qr_url`, `zatca_qr`, `e_invoice_qr`, `barcode`

Result is stored in `doc._qrCode` (empty string if not found).
Console will log either:
- `[Daftra QR] Found at key: "KEY"` — with the confirmed JSON path
- `[Daftra QR] No QR field found` — plus a list of all invoice keys for manual inspection

### Status
QR field not yet confirmed from a live API response. The header renders a center QR slot **only if** `doc._qrCode` is non-empty. If empty, the header stays two-column (logo | title) with no visible change.

### Placement
When present: `[ Logo ] [ QR 72×72px ] [ Tax Invoice ]` — three-column flex header.
CSS class: `.pp-header-qr` — centered flex slot with `image-rendering: pixelated`.

---

## Solved Issues

| Issue | Resolution |
|-------|-----------|
| Line items not showing | Daftra uses `price` not `unit_price` for unit price field |
| Client VAT/CR showing `—` | Fields are `client_bn1` / `client_bn2` on Invoice, `bn1` / `bn2` on Client |
| VAT showing SAR 0.00 | Daftra's `summary_tax` can be 0 even with VAT included — fall back to `total - subtotal` |
| Footer overlapping content on long invoices | Removed `position: absolute` and `display: flex` from page; footer now in normal flow |
| Blank second page on short invoices | Removed `min-height: 297mm`; added `scrollY: 0` to html2canvas |
| White gap on right side of PDF | Removed `width: 595 / windowWidth: 595` constraints; switched to `unit: 'mm'` |
| Content clipped on left in PDF | Those px constraints forced a 595px viewport on a 210mm element |
| Arabic numerals in VAT/CR | `toEnDigits()` converts at display time only |
| Arabic headings misaligned | Removed `direction: rtl` from label container; Arabic now left-aligned matching English |
| Summary block splitting across pages | `page-break-inside: avoid` on `.pp-summary-block` and `.pp-totals` |
| Download PDF button broken after layout/footer changes | Reverted `daftra-pdf-generator_1.html` to commit `c9dc4f8` (last confirmed working state). Root cause: subsequent layout commits corrupted the `downloadPDF()` function. |

---

## Current Known Issues

1. **Pagination on very long invoices** — footer behavior on 2+ page invoices
   not fully verified. `page-break-inside: avoid` on footer is set but html2pdf
   rendering may still vary by browser.

2. **Logo filename** — logo is `logo.png` (copied from `logo.png.png.png`, a Windows
   triple-extension artifact). HTML references `./logo.png`. Both files exist in the
   project folder. Do not delete `logo.png`.

3. **API key in source** — the Daftra API key is hardcoded in the HTML file.
   Acceptable for internal single-user tooling; not suitable for sharing or
   hosting publicly.

4. **Fetches up to 100 documents only** — list endpoints use `?limit=100&page=1`.
   Pagination not implemented. Accounts with more than 100 invoices will see
   a truncated list.

5. **CORS dependency** — all Daftra API calls are made client-side. If Daftra
   changes its CORS policy the tool will stop working without a proxy.

6. **No offline fallback** — requires live internet for Google Fonts and the
   html2pdf CDN. PDFs generated offline may have missing fonts.
