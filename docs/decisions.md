# Design Decisions — Vista Platform

A record of why key implementation choices were made. Consult this before changing anything structural.

---

## Document Generator — Purchasing Invoice Manager

---

### PyMuPDF (fitz) for Server-Side Combined PDF

**Decision:** Use PyMuPDF (`fitz`) in `proxy.py` to merge PDFs and images into a single combined PDF, served via `POST /purchasing-invoices/combine`. The browser receives one PDF blob.

**Why:** The prior approach opened each file in a separate `window.open()` tab. Browsers allow only ~1–2 rapid programmatic `window.open()` calls before blocking further popups — so opening 5+ purchasing files silently failed. A server-side merge produces a single file that opens in one tab with no popup restrictions.

**Trade-off:** PyMuPDF must be installed (`pip install PyMuPDF`). The proxy returns `503` with clear install instructions if the module is absent. Combined PDFs are streamed from memory — no temp files are written to disk.

**Rule:** Do not revert to per-file `window.open()` for purchasing files.

---

### localStorage Manual Tag Override for File Classification

**Decision:** Manual classification tags are stored in `localStorage` key `vista_purchasing_file_tags_v1` (schema: `{ "folder/file.pdf": "invoice"|"payment"|"other" }`). Manual tag wins over auto-classification. Files are never renamed or moved on disk.

**Why:** Automatic keyword classification cannot be perfect for all filenames. The localStorage approach lets the user correct misclassifications permanently without touching the actual files or requiring a server-side metadata store. Tags survive browser refreshes. Tags are per-machine (localStorage), which is acceptable since the tool is single-user.

**Rule:** Never rename, move, or write metadata files to the purchasing invoices folder based on classification tags.

---

### Payment Slips Excluded from Print All by Default

**Decision:** `printAllPurchasingFiles()` prints only invoice-classified files. Payment slips and others are excluded. `printSelectedPurchasingFiles()` includes all checked files but shows a warning if non-invoice files are included in the selection.

**Why:** "Print All" in the context of purchasing invoices typically means "print everything I need to submit to the accountant" — that audience is invoices, not payment confirmations. Payment slips may be needed separately. The warning on Print Selected ensures the user is not surprised by payment slips appearing in a combined PDF they intended for invoices only.

---

### ThreadingHTTPServer for Concurrent Requests

**Decision:** `proxy.py` uses `ThreadingHTTPServer` instead of the default single-threaded `HTTPServer`.

**Why:** The PDF viewer modal and the list endpoint may fire concurrently (e.g. the user opens a PDF while the list is still loading). Single-threaded `HTTPServer` serialises all requests — an in-progress streaming response blocks all other routes including Notion API relays. `ThreadingHTTPServer` handles each connection in its own thread.

---

### Three-Phase Response for Purchasing File Serving

**Decision:** `_serve_purchasing_file()` is split into three phases: (1) validate path and extension, (2) send headers, (3) stream in 64KB chunks. No `_json_error()` call is made after Phase 2 has started.

**Why:** Calling `_json_error` (which calls `send_response` + `send_header` + `end_headers`) after headers are already sent triggers `ConnectionAbortedError` / `BrokenPipeError` because HTTP only allows one response per connection. The three-phase structure ensures that any error before headers go out returns a proper JSON error, and any error after headers go out is logged but does not attempt to send a second response.

---

### Cache-Control: no-store on Purchasing List Endpoint

**Decision:** `GET /purchasing-invoices/list` response includes `Cache-Control: no-store, no-cache, must-revalidate`. The frontend also fetches with `?t=${Date.now()}` and `{ cache: 'no-store' }`.

**Why:** After an upload, Edge and Chrome can return a cached list response that doesn't include the newly uploaded file — even on a fresh fetch. Double defence (both server header and client hint) is necessary because some browsers respect one but not the other.

---

## Financial Dashboard

---

### Daftra API Calls Routed Through Proxy (Financial Dashboard Only)

**Decision:** `financial-dashboard.html` calls `/daftra/...` only. `proxy.py` injects the `APIKEY` header from `config.json`. The API key never appears in the HTML source.

**Why:** The Document Generator predates the proxy's Daftra route and calls Daftra browser-direct (CORS allowed by Daftra). It has the API key hardcoded — acceptable for that legacy module. The Financial Dashboard was built after the proxy infrastructure existed and uses the correct pattern: credentials stay server-side, browser code never sees them.

**Two patterns coexist intentionally.** The Document Generator is stable and must not be migrated to the proxy route without a specific reason. New Daftra-connected tools must use the proxy route.

---

### Personal Transfer Purchase Invoices Excluded From All Business Calculations

**Decision:** Purchase invoices where `supplier_business_name.trim().toLowerCase() === 'personal transfer'` are excluded from business profit, VAT reconciliation, monthly chart, and all period panels. They appear only in a dedicated Personal Transfers section.

**Why:** These records represent owner withdrawals — money moved out of the business bank account to personal use — not business operating costs. Including them in profit or VAT calculations would understate profit and misrepresent the VAT position. They are shown separately (7 records, identified as of branch testing) so the user has full visibility without contaminating business figures.

**Implementation:** Pre-filtered at the top of `renderContent()` into `personalRecords` and `bizPurRecords` before any calculation. `bizPurRecords` is passed to all calculation functions. This is a locked behaviour — do not remove or weaken the filter.

---

## Social Media Control Center

---

### Media Index in localStorage, Not Fetched on Load

**Decision:** The Media Library does not fetch page blocks on every dashboard load. Instead a `localStorage` index (`vista_media_index_v1`) stores task metadata (types found, hasMedia flag, timestamp). It is populated only when the user explicitly clicks "Refresh Media Index."

**Why:** 89 tasks × 2–10 API calls each = up to 890 Notion API requests. Doing this silently on every load would be slow (~30 seconds), burn rate limits, and feel broken. The user controls when to rebuild the index. Once built, the library renders instantly from `localStorage` with zero API calls.

**Trade-off:** The index can go stale. New tasks added after the last scan are registered as unscanned and shown in the sidebar warning. The user must manually rescan to pick them up.

---

### No Signed URLs in the Media Index

**Decision:** The `localStorage` index stores only media *type* (`image`, `video`, `file`, `pdf`, `embed`, `link`), not the actual Notion signed URLs.

**Why:** Notion signed URLs expire after ~1 hour. Storing them would make the index immediately useless and give a false impression that URLs are retrievable. The correct pattern is: index records *what exists*; actual URLs are fetched fresh via `loadDetailMedia()` when the user opens the task.

**Rule:** Never persist a Notion file URL, signed image URL, or embed URL anywhere in `localStorage`, cookies, or any client-side store.

---

### Media Index Scans All Tasks Including Done

**Decision:** `getAllMediaTasks()` returns all Content/Social/Ads & Testing tasks regardless of `Status === 'Done'`. The media index and Media Library include Done tasks.

**Why:** The "Include Done" sidebar toggle controls the Task Tracker and Attention views, where Done tasks are noise. The Media Library is a *content archive* — you frequently want to find an image or file from a task that was completed weeks ago. Filtering by active status would silently hide the majority of available media (23 of 27 media-containing tasks were Done in the current scan).

**Implementation note:** The chip filter still obeys `showDone` (~53 tasks shown). The index scan uses `getAllMediaTasks()` (~89 tasks). These are different functions with different purposes.

---

### Nested Block Fetch (One Level Deep)

**Decision:** When loading task detail media, `loadDetailMedia()` fetches the top-level blocks, then fetches one additional level of children for any block with `has_children: true` whose type is in `NESTED_CONTAINER_TYPES` (toggle, column, callout, numbered/bulleted list item, etc.).

**Why:** The media audit confirmed that images are stored inside `numbered_list_item` containers (e.g. Vista United email signatures has 4 images nested this way). A flat top-level fetch misses these entirely. One additional level covers all observed patterns. Deeper recursion (2+ levels) is not implemented — it was not needed in any audited task and would multiply API calls.

**Limit:** Max 8 nested containers fetched per task to avoid unbounded API usage.

---

### Empty-State Distinctions in Task Detail Media Section

**Decision:** The media section shows three different messages depending on what the blocks contain, rather than a single generic "no media" message:

1. `blockCount === 0` → "This task has no page body — only title and properties exist in Notion."
2. Blocks exist, text only → "📝 This task has notes and text but no attached images, files, or links."
3. Blocks exist, unsupported types only → "No detectable content. The page may use unsupported block types."
4. Table block present → "📊 This task contains a table that cannot be rendered."

**Why:** A single "no media" message gives no signal. Distinguishing empty-page from text-only-page tells the user whether to look in Notion for content or whether the page simply has none. The table note prevents confusion when a task clearly has content but nothing renders.

---

### Media Library Shows Confirmed Media Only

**Decision:** The Media Library tab displays only tasks where `hasMedia: true` in the index. Tasks that are unscanned (`scannedAt: null`) or have `hasMedia: false` are excluded.

**Why:** Showing 62 empty tasks alongside 27 with media would make the library useless as a browsing surface. The point of the library is instant visual access to tasks that have something to see or download.

**Unscanned tasks** are surfaced only in the sidebar warning ("⚠ N new tasks not yet scanned") so the user knows to rescan — they do not pollute the library grid.

---

### Video Rendered as "Video", Not "Embed"

**Decision:** Notion `video` blocks are labelled "Video" with a ▶ icon in both the detail panel and the media index. Notion `embed` and `bookmark` blocks are labelled "Embed" or "Bookmark" respectively.

**Why:** Grouping video with generic embeds ("Embed") hides its nature. A video deserves a distinct label so users know what they're clicking. The media audit found 5 video blocks across tasks — enough to warrant the distinction.

---

---

## Single-File Architecture

**Decision:** Everything in one `.html` file — no build system, no bundler, no framework.

**Why:** This tool is used internally by one person. A single file can be opened directly in a browser, emailed, or dropped into any folder. There is no deployment step to break.

**Trade-off:** The file will grow large. Acceptable for this scope.

---

## Logo as Base64 Data URL

**Decision:** The logo is embedded as a `const LOGO_DATA_URL = 'data:image/png;base64,...'` at the top of the script block.

**Why:** `html2canvas` (used internally by html2pdf.js) cannot load local file paths (`./logo.png`) when the page is opened from `file://`. It silently renders a broken image in the PDF. Base64 embedding is the only reliable approach.

**Rule:** Never replace `${LOGO_DATA_URL}` with a file path in the template.

**To regenerate:** `base64 -w 0 logo.png` then prefix with `data:image/png;base64,`.

---

## html2pdf Chain Order: `.set()` Before `.from()`

**Decision:** Options are always set before the element is captured.

**Why:** html2pdf.js applies options at the time `.from()` captures the DOM. If `.set()` is called after `.from()`, options are silently ignored and the export is corrupted.

**Rule:** Never reorder the chain. The `downloadPDF()` function body is locked.

---

## No Page Height Constraint

**Decision:** No `min-height: 297mm` on `.pdf-page`.

**Why:** A fixed height forces a second page even when content is short. Page height is determined entirely by content flow. `scrollY: 0` in html2canvas prevents the blank-page-from-scroll-offset issue that previously required the height constraint.

---

## No Flexbox on the PDF Page Root

**Decision:** `.pdf-page` does not use `display: flex`.

**Why:** Flex was previously used to position the footer at the bottom of the page. This caused the footer to overlap content on long invoices. Removing flex allows the footer to flow naturally after content, and `page-break-inside: avoid` keeps it together across page breaks.

---

## QR from d64 Param, Not Image Fetch

**Decision:** Extract the `?d64=` parameter from `qr_code_url`, decode with `atob()`, and render locally via QRCode.js rather than fetching the QR image from Daftra.

**Why:** `fetch(qr_code_url)` is blocked by CORS on both `file://` and `localhost`. Even if it worked, the fetched image would not survive html2canvas export reliably. The `d64` parameter contains the full ZATCA TLV payload — rendering it locally produces an identical, scannable QR.

**Verification:** All three QRs (Daftra web UI, browser preview, exported PDF) must scan to the same value.

---

## Canvas → PNG Data URL Before PDF Export

**Decision:** After QRCode.js draws its canvas, immediately convert it to a PNG data URL and replace the canvas with an `<img>` element.

**Why:** html2canvas has unreliable behavior when capturing live `<canvas>` elements — it sometimes renders them blank. An `<img src="data:image/png;...">` is captured correctly every time.

**Timing:** Done inside `setTimeout(0)` to allow QRCode.js to finish drawing before the conversion runs.

---

## VAT Calculation Fallback

**Decision:** `taxAmt = parseFloat(doc.summary_tax || ...) || Math.max(0, total - subtotal)`

**Why:** Daftra sometimes returns `summary_tax = 0` on estimates even when VAT is included in `total_amount`. The fallback ensures VAT is always displayed correctly by deriving it from the difference between total and subtotal.

---

## Separate Tables for Head Rows and Tail Rows (Invoice Tail)

**Decision:** The last 2 line items are split into a separate `pp-table-tail` element wrapped in a `pp-invoice-tail` div with `page-break-inside: avoid`.

**Why:** Without this, the totals block (subtotal / VAT / grand total) would orphan onto a new page, separated from the last rows. Keeping the last 2 rows and the totals block together prevents this.

**Rule:** The tail table must always have a `<colgroup>` with matching column widths so values align under the correct headers.

---

## nth-child Column Alignment (Not Class-Based)

**Decision:** Table column alignment uses `nth-child(2/3/4)` selectors rather than relying on per-cell classes.

**Why:** Class-based alignment (`pp-td-r`) was inconsistent — the class existed on value cells but the tail table's td elements weren't always receiving it correctly. Position-based selectors are deterministic.

**Invoice:** cols 2–4 right. **Quotation:** cols 3–8 right (cols 1–2 are Item and Description, both left).

---

## Separate Table Classes for Invoice vs Quotation

**Decision:** Invoice uses `pp-table` / `pp-table-tail`. Quotation uses `pp-table pp-table-quo` / `pp-table-tail pp-table-tail-quo`.

**Why:** Invoice has 4 columns; quotation has 8. They need different `nth-child` alignment rules and different `<colgroup>` widths. Separate classes allow independent targeting without interference.

---

## Quotation QR Suppression

**Decision:** QR is suppressed for quotations via `isInv && qrPayload` in both the HTML template and the `renderQR()` call.

**Why:** Quotations are commercial proposals, not tax documents. The ZATCA QR is mandatory only for tax invoices. Quotations also have a `qr_code_url` field in the Daftra response — the condition must be explicit, not reliant on the field being absent.

---

## Valid Until Calculated from Date + 30 Days

**Decision:** `doc.expiry_date || (doc.date + 30 days)`.

**Why:** `expiry_date` is always blank on Daftra estimates. The 30-day validity is a business rule, so the date is calculated at render time from the quotation issue date.

---

## Quotation Header: 2-Column Grid with Title Inset

**Decision:** Quotation header uses `pp-header-quo` class — `1fr 1fr` grid (no centre QR column), `pp-qr-wrap { display: none }`, and `pp-title-block { margin-right: 20px }`.

**Why:** Without the QR, the original `1fr auto 1fr` grid has an empty centre column that creates visual dead space. A `1fr 1fr` grid balances the two sides. The 20px title inset compensates for the logo's `translateY(-48px)` upward float — without it the title sits hard against the right margin while the logo's visual weight is concentrated upper-left.

**Rule:** The logo's `translateY(-48px)` is retained for quotations. Do not override it.

---

## Footer: Invoices Only

**Decision:** The footer (company name, contact, address, website) is rendered only for invoices.

**Why:** Quotations include a Terms & Conditions block at the bottom instead. The footer information is embedded within the Prepared By section, making a footer redundant on quotations.

---

## Terms & Conditions: Flex Two-Column, No ul/li

**Decision:** Terms use a `display: flex` two-column layout — English left, Arabic right. Plain `<div>` elements with `·` prefix/suffix rather than `<ul>/<li>`.

**Why:** `<ul>` with `direction: rtl` on the container produces stray bullet dots on the left side of the page — the list markers render in the wrong position. Plain divs with an explicit `·` character are predictable across all browsers and PDF renderers.

---

## Prepared By: 2-Line Contact Block (Quotations)

**Decision:** Quotation Prepared By shows `address<br>website · phone` (2 lines). Invoice Issued By shows `address<br>website<br>phone` (3 lines).

**Why:** Quotation's Prepared For block typically has 2 lines of content. A 3-line Prepared By block creates a visible height mismatch between the two columns, which looks unbalanced on the quotation. Invoices retain 3 lines because the height difference is less noticeable in the Issued To / Issued By context.
