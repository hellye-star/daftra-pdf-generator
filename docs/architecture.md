# Architecture — Vista Platform

## Overview

A suite of local, single-file HTML tools for Vista United Co. No cloud hosting, no build step. All tools open from the same local folder and are served by `proxy.py` on `localhost:8080`. The Document Generator connects directly to Daftra (CORS allowed). All Notion-connected tools route through the local proxy (CORS not allowed from browser).

---

## Platform Modules

| Module | File | Data source | Status |
|---|---|---|---|
| Homepage | `index.html` | None | ✅ Live |
| Document Generator | `daftra-pdf-generator_1.html` | Daftra ERP (browser-direct) + Local purchasing invoices folder | ✅ Live — includes Purchasing Invoice manager |
| Social Media Control Center | `social-dashboard.html` | Notion — Vista/Hussam workspace | ✅ Live — Phase 2A complete + detail unification |
| Financial Dashboard | `financial-dashboard.html` | Daftra ERP (via `/daftra/...` proxy) | 🌿 Feature branch — not yet merged to `stable-reviewed-history` |
| Personal Task Center | `personal-dashboard.html` | Notion — Youssef private workspace | ⏳ Planned |
| Local Proxy | `proxy.py` | — relays Notion API + Daftra API | ✅ Live |

---

## Proxy Routes — Purchasing Invoice Manager

All purchasing invoice routes are handled by `proxy.py`. Files are served from `PURCHASE_INVOICE_DIR = C:\Users\YousefMokaled\Documents\Vista United Co\purchasing invoices`. All routes require path-traversal validation via `_safe_purchase_path(rel_path)`.

| Route | Method | Purpose |
|---|---|---|
| `/purchasing-invoices/list` | GET | Directory walk; returns JSON array of `{ folder, name, relativePath, size }`; `Cache-Control: no-store` |
| `/purchasing-invoices/file?path=…` | GET | Serve a file inline; RFC 5987 `Content-Disposition` for Unicode filenames; three-phase (validate → headers → 64KB chunks) |
| `/purchasing-invoices/upload` | POST | Multipart upload into a `D-M-YYYY` dated subfolder; allowed ext: `.pdf .png .jpg .jpeg .webp` |
| `/purchasing-invoices/combine` | POST | PyMuPDF server-side merge; accepts `{ "paths": [...] }`; streams combined PDF; returns `503` if PyMuPDF not installed |

**PyMuPDF dependency:** `pip install PyMuPDF` (v1.27.2.3 tested). Proxy returns `503` with install instructions if import fails.

**Allowed extensions:** `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`

---

## Stack

| Layer | Technology |
|---|---|
| UI + logic | Vanilla HTML / CSS / JS |
| PDF export | `html2pdf.js` v0.10.1 (CDN) |
| QR rendering | `qrcodejs` v1.0.0 (CDN) |
| Fonts | Google Fonts — Cormorant Garamond, Jost, Cairo |
| API — documents | Daftra ERP REST API v2 (browser-side, CORS allowed) |
| API — Notion | Notion REST API v1 (server-side via `proxy.py`, CORS blocked in browser) |
| Local proxy | `proxy.py` — Python stdlib only, serves HTML + relays Notion API calls |
| Media index cache | `localStorage` key `vista_media_index_v1` — task metadata only, no signed URLs. Does not gate live media loading. |
| Review store | `localStorage` key `vista_reviews_v1` — reviewed task IDs + `last_edited_time` snapshot (Phase 2A, implemented) |
| Detail task guard | `let _detailTaskId` — in-memory only, tracks open task ID to discard stale async fetch results |
| Purchasing file tags | `localStorage` key `vista_purchasing_file_tags_v1` — manual classification overrides: `{ "folder/file.pdf": "invoice"|"payment"|"other" }` |
| Config | `config.json` (git-ignored) — all tokens and database IDs |

---

## File Structure

```
index.html                     — Vista Platform homepage
daftra-pdf-generator_1.html    — Document Generator (invoices + quotations)
social-dashboard.html          — Social Media Control Center  [live — Phase 2A + detail unification]
financial-dashboard.html       — Financial Dashboard  [feature branch — not yet merged to stable-reviewed-history]
personal-dashboard.html        — Personal Task Center  [planned]
proxy.py                       — Local proxy: serves HTML files + relays Notion API + Daftra API  [live]
config.json                    — Live config: tokens + IDs  (git-ignored — never commit)
config.example.json            — Safe template with placeholder values (safe to commit)
.gitignore                     — Excludes config.json and OS artifacts
logo.png                       — Company logo (also embedded as base64 in document generator)
docs/
  architecture.md
  roadmap.md
  decisions.md
  changelog.md
CLAUDE_CONTEXT.md              — Permanent implementation rules (all modules)
.claude/
  launch.json                  — Preview server config (port 8080)
```

---

## Notion Integration Architecture

### Why a local proxy is required
The Notion API does not include CORS headers. Any `fetch()` call to `api.notion.com` from a browser page is blocked regardless of origin. The local proxy (`proxy.py`) receives requests from the browser on `localhost:8080/notion/...`, injects the `Authorization: Bearer <token>` header, forwards the request to Notion, and returns the response. The token never appears in any HTML file.

### Two separate Notion sources

| Source | Purpose | Config key | Integration |
|---|---|---|---|
| **Social Media Control Center** | Hussam/Vista shared database — tasks, media, meetings, approvals | `notion.social_media` | Separate integration token, read-only |
| **Personal Task Center** | Youssef's private database — personal reminders, follow-ups | `notion.personal` | Separate integration token, read-only |

Each source has its own integration token and database ID(s). They are isolated — the Social Media integration cannot read the Personal workspace, and vice versa.

### config.json structure (git-ignored)
```
config.json
  └── notion
        ├── social_media
        │     ├── token               ← secret_... (Vista integration)
        │     ├── task_database_id    ← 32-char ID from Notion URL
        │     ├── meeting_database_id ← optional, if separate DB
        │     └── notion_user_id      ← Youssef's Notion user ID (for "me" filter)
        └── personal
              ├── token               ← secret_... (personal integration)
              └── task_database_id    ← 32-char ID from personal Notion DB
```

### Notion data flow (Social Media Control Center)
```
Dashboard opens
  → POST localhost:8080/notion/personal/data_sources/{id}/query
      proxy injects Authorization header → forwards to api.notion.com
      → returns all tasks with pagination (Notion API version 2025-09-03)
  → mergeNewTasksIntoIndex() registers any new Media/Posts tasks as unscanned

User opens a task
  → openDetail(id)
    → GET localhost:8080/notion/personal/blocks/{page_id}/children
        → returns top-level page blocks
    → For each block with has_children in NESTED_CONTAINER_TYPES:
        → GET localhost:8080/notion/personal/blocks/{block_id}/children
            → returns one level of nested blocks (images inside lists, callouts, etc.)
    → renderMediaBlocks(topBlocks, allBlocks) classifies content:
        images, videos, files, PDFs, bookmarks, embeds, links, tables
    → GET localhost:8080/notion/personal/comments?block_id={page_id}
        → returns page comments if integration has "Read comments" permission
        → currently returns permission error (Phase 2)

User clicks a media item in the detail panel
  → fresh signed URL fetched on demand when task is opened (not cached)
  → Notion signed URLs expire after ~1 hour — never store them locally
  → opens in new browser tab

User clicks "Refresh Media Index"
  → runMediaIndexScan() iterates all 89 Media/Posts tasks (including Done)
  → For each task: fetches blocks + one level of nested blocks
  → Stores in localStorage (key: vista_media_index_v1):
      task ID, name, category, status, due date, mediaTypes[], hasMedia, scannedAt
      ← NO signed URLs stored — index is metadata only
  → Progress bar updates every 5 tasks; completes in ~30 seconds

User opens Media Library tab
  → renderMediaLibrary() reads index from localStorage (no API calls)
  → Shows only tasks where hasMedia = true (27 of 89 in current scan)
  → Clicking a card calls openDetail(id) → fetches fresh URLs live

User clicks "Mark Reviewed" (Phase 2 — local, no Notion write)
  → Planned: stored in localStorage only — no Notion API call
  → See decisions.md for full design

User clicks "Mark Reviewed" (Phase 3 — Notion write-back)
  → PATCH localhost:8080/notion/pages/{id}
      → sets "Youssef Reviewed" checkbox to true in Notion
```

### Media Index — localStorage schema
```
localStorage key: vista_media_index_v1
{
  "tasks": {
    "{taskId}": {
      "id":         "...",           // Notion page ID
      "name":       "...",           // task title
      "category":   "Content",       // Content | Social | Ads & Testing
      "status":     "Done",          // Notion status value
      "dueDate":    "2026-06-04",    // ISO date or ""
      "mediaTypes": ["image","link"],// types detected (no URLs)
      "hasMedia":   true,            // false = excluded from library
      "scannedAt":  1748000000000    // ms timestamp; null = not yet scanned
    }
  },
  "lastFullScan": 1748000000000,     // ms timestamp of last complete scan
  "totalScanned": 89                 // number of tasks in last scan
}
```

**Rules:**
- Never store Notion signed URLs in this index — they expire after ~1 hour.
- The index records media *type* (image/video/file/pdf/embed/link), not the URL.
- Actual URLs are fetched live when `openDetail(id)` is called.
- Tasks in the index with `scannedAt: null` are registered but unscanned; they do not appear in the Media Library until the next Refresh.
- The index covers ALL Media/Posts tasks regardless of Done status. The chip filter
  hides Done tasks (showDone=false) but the library must show media from completed work.

### Notion file URL expiry
All file/image URLs returned by the Notion API are temporary signed links that **expire after 1 hour**. The Media Library stores only metadata. Signed URLs are always fetched fresh when the user opens a task detail. Never cache or embed Notion file URLs in localStorage or any persistent store.

### Notion file URL expiry
All file/image URLs returned by the Notion API are temporary signed links that **expire after 1 hour**. The Media Library stores file metadata (name, type, source task) permanently in the page; the live URL is fetched fresh only when the user clicks "Open." Never cache or embed Notion file URLs as permanent links.

---

## Daftra Integration — Two Coexisting Patterns

Two modules use Daftra. They use different connection patterns intentionally. **Do not consolidate them.**

| Module | Route | API key location | Notes |
|---|---|---|---|
| Document Generator | Browser → `daftra.com/api2/...` directly | Hardcoded in HTML source | Pre-proxy legacy; stable — do not change |
| Financial Dashboard | Browser → `/daftra/...` → proxy → `daftra.com/api2/...` | `config.json → daftra.api_key` (injected by proxy, never in HTML) | Read-only GET only; added commit `8deefc4` |

### Proxy Daftra route
```
Browser → GET /daftra/{path+querystring}
proxy.py strips /daftra prefix
  → forwards to https://{subdomain}.daftra.com/api2/{path+querystring}
  → injects header: APIKEY: {api_key from config.json}
  → returns JSON response verbatim
POST / PUT / PATCH / DELETE → 405 (read-only enforced)
```

### config.json structure
```
config.json
  ├── notion
  │     ├── social_media
  │     │     ├── token, task_database_id, meeting_database_id, notion_user_id
  │     └── personal
  │           └── token, task_database_id
  ├── daftra
  │     ├── subdomain    ← your Daftra subdomain (e.g. "vistaunited")
  │     └── api_key      ← your Daftra API key  (git-ignored — never commit)
  └── proxy
        ├── port         ← default 8080
        └── bind         ← must stay 127.0.0.1
```

---

## Document Generator — Daftra Data Flow

```
User clicks "Fetch Documents"
  → fetchDocs()
    → GET /invoices.json + GET /estimates.json
    → Renders document list (renderList())

User clicks a document
  → openDoc(index)
    → GET /invoices/{id}.json  OR  GET /estimates/{id}.json
      → Extracts InvoiceItem[] (same key for both)
      → Extracts QR payload from qr_code_url → d64 → atob()  [invoices only]
      → Extracts client_bn1 / client_bn2 (VAT / CR)
      → Fallback: GET /clients/{id}.json if VAT/CR missing
    → renderPreview()
      → Builds #pdfPage HTML from template
      → Calls renderQR() for invoices only
        → QRCode.js draws canvas → setTimeout(0) → canvas.toDataURL() → <img>

User clicks "Download PDF"
  → downloadPDF()
    → html2pdf().set({...}).from(#pdfPage).save()
```

---

## Financial Dashboard — Daftra Data Flow

```
User clicks "Fetch Data"
  → Three paginated fetches (via /daftra/... proxy):
    GET /daftra/invoices.json?limit=100&page=N          (sales)
    GET /daftra/purchase_invoices.json?limit=100&page=N (purchases)
    GET /daftra/expenses.json?limit=100&page=N          (expenses)
  → Each endpoint fetched page by page until result count < limit

renderContent()
  → Splits purchase records:
      personalRecords = purRecords.filter(isPersonalTransfer)
      bizPurRecords   = purRecords.filter(!isPersonalTransfer)

  → Yellow card (always YTD, period-independent):
      ytdBounds = getPeriodBounds('ytd')
      profit    = salesYTD.salesExVAT − purchYTD.purchExVAT − expYTD.expExVAT
      taxReserve = Math.max(profit, 0) × 0.20

  → Red card (always current Gregorian quarter, period-independent):
      qtrBounds  = getCurrentQuarterBounds()
      vatBalance = salesQtr.outputVAT − purchQtr.inputVAT − expQtr.inputVAT

  → Period panels (obey sidebar selector — currentPeriod):
      bounds = getPeriodBounds(currentPeriod)
      sales / purchases (bizPurRecords only) / expenses calculated for period

  → Monthly chart + table: uses bizPurRecords (personal transfers excluded)
  → Personal Transfers panel: uses personalRecords only
```

**VAT derivation:** `summary_total − summary_subtotal` for all three record types.
`summary_tax1` is always null — never used.

**Personal transfer identification:** `r.supplier_business_name.trim().toLowerCase() === 'personal transfer'`

**Purchase invoice reference field:** `r.no` (e.g. `000048`). Fallback chain: `r.no || r.number || r.id || '—'`

---

## API Endpoints — Document Generator

| Endpoint | Purpose |
|---|---|
| `GET /invoices.json?limit=100&page=1` | Invoice list |
| `GET /estimates.json?limit=100&page=1` | Quotation list |
| `GET /invoices/{id}.json` | Full invoice + line items |
| `GET /estimates/{id}.json` | Full quotation + line items |
| `GET /clients/{id}.json` | VAT/CR fallback |

**Do not use** `/customers` or `/contacts` — both return 404.

---

## API Endpoints — Financial Dashboard (via `/daftra/...` proxy)

| Endpoint | Purpose |
|---|---|
| `GET /daftra/invoices.json?limit=100&page=N` | Sales invoices (paginated) |
| `GET /daftra/purchase_invoices.json?limit=100&page=N` | Purchase invoices (paginated) |
| `GET /daftra/expenses.json?limit=100&page=N` | Expenses (paginated) |

---

## Key Daftra Field Names

| Field | Location | Meaning |
|---|---|---|
| `data.Invoice.client_bn1` | Invoice | Client VAT |
| `data.Invoice.client_bn2` | Invoice | Client CR |
| `data.Client.bn1` | Client | Client VAT (fallback) |
| `data.Client.bn2` | Client | Client CR (fallback) |
| `data.Estimate.InvoiceItem[]` | Estimate | Line items (NOT `EstimateItem`) |
| `it.item` | Line item | Item name (quotations) |
| `it.unit_price` | Line item | Unit price (quotations) |
| `it.item_subtotal` | Line item | Total before tax (quotations) |
| `it.tax1_percent` | Line item | VAT rate |
| `it.tax1_value` | Line item | VAT amount |
| `it.subtotal` | Line item | Total with VAT |
| `it.price` | Line item | Unit price (invoices) |
| `doc.summary_subtotal` | Invoice/Estimate | Subtotal before VAT |
| `doc.total_amount` | Invoice | Total including VAT |
| `doc.summary_total` | Estimate | Total including VAT |

---

## PDF Page Layout

```
┌─────────────────────────────────────────────┐  210mm wide
│  [Logo]          [QR*]        [Title/No.]   │  padding: 28px 38px 24px 38px
│  ─────────────────────────────────────────  │  * invoices only
│  [Prepared For]        [Prepared By]        │
│  [Valid Until strip*]                       │  * quotations only
│  ─────────────────────────────────────────  │
│  [Line items table]                         │
│  [Totals block]                             │
│  [Terms & Conditions*]                      │  * quotations only
│  ─────────────────────────────────────────  │
│  [Footer*]                                  │  * invoices only
└─────────────────────────────────────────────┘
```

---

## Invoice vs Quotation Differences

| Feature | Invoice | Quotation |
|---|---|---|
| Document type label | Tax Invoice / فاتورة ضريبية | Quotation / عرض سعر |
| Header label | Issued To / Issued By | Prepared For / Prepared By |
| QR code | Yes (ZATCA compliant) | No |
| Table columns | 4 (Desc, Qty, Unit Price, Amount) | 8 (Item, Desc, Price, Qty, Total BT, VAT%, VAT Amt, Total+VAT) |
| Valid Until strip | No | Yes (date + 30 days) |
| Terms & Conditions | No | Yes (5 bilingual bullet points) |
| Footer | Yes | No |
| Number format | Pass-through from Daftra | Pass-through from Daftra |

---

## PDF Export Configuration

```js
html2pdf().set({
  margin: 0,
  filename: filename,
  image: { type: 'jpeg', quality: 0.98 },
  pagebreak: { mode: ['css', 'legacy'],
    avoid: ['.pp-footer', '.pp-summary-block', '.pp-info-grid', '.pp-totals'] },
  html2canvas: { scale: 2, useCORS: true, scrollY: 0, logging: false },
  jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
}).from(el).save()
```

**Rules:**
- `.set()` must come before `.from()` — never reorder
- No `width` or `windowWidth` on html2canvas — causes left-side clipping
- `scrollY: 0` prevents phantom blank page
- Logo must remain as base64 data URL — local file paths break html2canvas

---

## QR Code Implementation (Locked)

Invoices only. Source: `qr_code_url → ?d64=<base64>`.

```
qr_code_url
  → extract d64 param
  → atob(d64)               ← required; raw d64 produces wrong scan
  → QRCode.js (96×96, CorrectLevel.M)
  → canvas element in DOM
  → setTimeout(0): canvas.toDataURL('image/png')
  → replace canvas with <img src="data:image/png;...">
  → html2pdf captures <img> reliably
```

Never fetch `qr_code_url` directly — CORS blocks it. Never generate from invoice fields.

---

## Typography

| Font | Usage |
|---|---|
| Cormorant Garamond | Document headings, total labels |
| Jost | All body and data text |
| Cairo | All Arabic text |

Font weights in use: 300 (decorative labels), 400 (body text), 500 (section headings), 600 (totals), bold (company name in footer).
