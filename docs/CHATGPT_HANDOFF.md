# Vista Platform — ChatGPT Handoff

A compact reference for understanding the project, evaluating Claude responses, and continuing development. Read this before suggesting any changes.

---

## 1. Project Overview

Vista United Co. internal tooling — a suite of single-file HTML tools served by a local Python proxy. No build step. No cloud hosting. Everything opens from one folder at `http://localhost:8080`. Two active tools: a Notion-connected social media dashboard and a Daftra ERP PDF generator.

---

## 2. Platform Modules

| Module | File | Status |
|---|---|---|
| Homepage | `index.html` | ✅ Live |
| Social Media Control Center | `social-dashboard.html` | ✅ Live — Phase 2A + 2A.5 complete |
| Document Generator | `daftra-pdf-generator_1.html` | ✅ Live — stable, no open tasks |
| Financial Dashboard | `financial-dashboard.html` | 🌿 Feature branch ready — live on `feature/financial-dashboard`, not yet merged to `stable-reviewed-history` |
| Personal Task Center | `personal-dashboard.html` | ⏳ Planned (Phase 3) |
| Local Proxy | `proxy.py` | ✅ Live |

---

## 3. Current Approved Stable Version

| Item | Value |
|---|---|
| Active stable branch | `stable-reviewed-history` |
| Stable commit | `2d0faec` |
| Stable tag | `stable-reviewed-history-v1` |
| Tag message | "Approved stable Vista dashboard with permanent Reviewed history" |

**Note:** The Financial Dashboard (`feature/financial-dashboard`, latest commit `0bc03db`) is **not** included in `stable-reviewed-history`. That branch and tag reflect only the Social Media Control Center and Document Generator. The merge target for the Financial Dashboard branch will be decided after final review.

**To restore stable at any time:**
```bash
# Inspect (detached HEAD):
git checkout stable-reviewed-history-v1

# Restore as a working branch:
git checkout -b restored-stable stable-reviewed-history-v1
```

---

## 4. How to Run

```bash
python proxy.py
```

| URL | Purpose |
|---|---|
| `http://localhost:8080` | Vista Platform homepage |
| `http://localhost:8080/social-dashboard.html` | Social Media Control Center |
| `http://localhost:8080/daftra-pdf-generator_1.html` | Document Generator |
| `http://localhost:8080/financial-dashboard.html` | Financial Dashboard |

**Requirements:** `config.json` must exist in the project root (git-ignored). It holds the Notion API token and database IDs. See `config.example.json` for the structure. Never commit `config.json`.

---

## 5. Architecture and Safety Rules

- **Single-file HTML** — all logic, CSS, and JS in one `.html` file per tool. No bundler, no framework.
- **Local proxy required for Notion** — `proxy.py` relays Notion API calls and injects the auth token. Direct browser fetch to `api.notion.com` is blocked by CORS. Proxy binds to `127.0.0.1` only.
- **Daftra API — Document Generator** (`daftra-pdf-generator_1.html`): browser-direct. CORS is allowed by Daftra; the API key is hardcoded in the source. This module predates the proxy-based Daftra route and is stable — do not change its connection pattern.
- **Daftra API — Financial Dashboard** (`financial-dashboard.html`): all browser calls go to `/daftra/...` only. The proxy injects the `APIKEY` header from `config.json → daftra.api_key`. The API key never appears in the HTML source. Direct browser calls to `daftra.com` are not made from this module. The proxy route is read-only GET only — POST, PUT, PATCH, DELETE are blocked with 405.
- **No auto-fetch in Financial Dashboard** — zero `DOMContentLoaded` / `setInterval` / `setTimeout` data-fetch triggers. Manual Fetch Data button only.
- **Financial values are management estimates** — not official tax filings. Labels in the dashboard reflect this.
- **Personal transfer exclusion** — purchase invoices where `supplier_business_name.trim().toLowerCase() === 'personal transfer'` are excluded from all business calculations in the Financial Dashboard and shown in a separate panel.
- **Never store Notion signed URLs** — they expire in ~1 hour. The media index stores metadata only; live URLs are fetched on demand when a task is opened.
- **Logo must stay as base64** — `html2canvas` cannot load local file paths. `LOGO_DATA_URL` in the document generator must remain a base64 data URL, never a file path.
- **QR implementation is locked** — the full pipeline (`d64 → atob() → QRCode.js → canvas → PNG img`) must not be changed. Verified by scan. See `CLAUDE_CONTEXT.md`.
- **`html2pdf` chain order is locked** — `.set()` must always come before `.from()`. Reversing this silently corrupts PDF export.
- **`config.json` is git-ignored** — never commit it, never echo it, never expose its contents.

---

## 6. Social Media Control Center — Current Features

### Tabs
- **Needs My Attention** — Youssef-assigned, Blocked, Pending Feedback, and overdue tasks. Sorted by urgency. Reviewed-and-fresh tasks fully suppressed.
- **Task Tracker** — all tasks grouped by due-date bucket, collapsible, color-coded rows and status badges.
- **Media Library** — tasks with confirmed media from the manual scan index (27 of 89 in last scan). Done tasks included — this is a content archive, not a task filter.

### Quick-filter chips
`All` · `Needs My Attention` · `Content` · `Social` · `Media/Posts` · `Overdue` · **`Reviewed`**

### Reviewed chip (approved stable behaviour)
- Shows tasks where `isReviewedByMe(t)` is true — review record exists regardless of staleness.
- **Permanent history**: a task stays in Reviewed until the user manually clicks Remove Review. Notion edits and Done status changes never remove it.
- Done tasks are always visible under Reviewed regardless of the Include Done toggle.
- Tasks edited in Notion after review show an amber **Updated Since Review** badge in the task row and a note in the detail panel. They may also return to Needs My Attention but remain in Reviewed.
- Category, Assignee, and search filters apply. Include Done does not hide reviewed tasks.
- `isReviewedAndFresh` is used only by `attentionFilter` — this is intentional and must not be changed.

### Task detail panel
- Slide-in from right. Summary strip, description, media blocks, inline Notion table rendering.
- Contextual "Open in Notion ↗" button appears in the Media section **only** when content cannot render (empty page, text-only, unsupported blocks, fetch error). It does **not** appear when supported content renders — the topbar link covers that.
- Race condition guarded: `_detailTaskId` global prevents stale async fetches from overwriting the currently open task. All 7 `el.innerHTML` write points are guarded.

### Related Supporting Tasks
- Always visible in the detail panel. **5-tier detection system**, max 5 results, strongest first:

| Tier | Label | Type |
|---|---|---|
| 5 | Linked by You | Manual (modal, stored in `vista_task_relations_v1`) |
| 4 | Explicit Notion Link | Automatic (`app.notion.com/p/` URLs in task body) |
| 3 | Exact Reference | Automatic (full task name in other task's description) |
| 2 | Strong Match | Automatic (same cat + bigram or 3+ words; cross-cat + bigram) |
| 1 | Possible Match | Automatic (same cat + 2+ shared significant words) |

- `RELATED_STOP` stop-words + minimum word length (≥4 chars) prevent false positives at Tiers 1–3.
- "Remove link" button only on Tier 5 (manual) cards.
- **Previously removed (must not be restored):** `sigTaskWords()` and `findRelatedByDescription()` — the original single-pass functions that produced false positives. The current Tier 1–3 system is different: it uses `sigWords()`, `sigBigrams()`, and `findRelatedTasks()` with threshold controls that were validated against the known false positive.

### Sidebar
Search (live, across all tabs), Category filter, Assignee filter, Include Done toggle, Refresh Tasks, Refresh Media Index.

### Search
Live substring across task name, description, status, assignee, category, due date, and media type labels. Stacks additively with chips and sidebar filters. When a search is active and Include Done is off, matching Done tasks appear in a muted "Completed Matches" section below active results.

### localStorage keys
| Key | Purpose |
|---|---|
| `vista_media_index_v1` | Media scan metadata — no signed URLs |
| `vista_reviews_v1` | Mark Reviewed records — stores `reviewedAt`, `taskName`, `lastEditedTime` |
| `vista_task_relations_v1` | Manual Related Supporting Task links — bidirectional |

---

## 7. Document Generator Status

Stable. No open tasks. Both invoice and quotation PDF generation are complete and verified.

Key facts:
- Invoice and quotation numbers are strict pass-throughs from Daftra (`String(no).trim()`). No prefix added.
- QR on invoices only. Source: `qr_code_url → ?d64= → atob() → QRCode.js → canvas → PNG img`. Locked. Verified by scan.
- Daftra returns estimate line items under `data.Estimate.InvoiceItem` (not `EstimateItem`).
- VAT fallback: `summary_tax || (total - subtotal)` — Daftra sometimes returns `summary_tax = 0` even when VAT is present.
- Footer on invoices only. Terms & Conditions on quotations only. Valid Until calculated as issue date + 30 days (`expiry_date` is always blank on Daftra estimates).

---

## 8. Financial Dashboard — Feature Branch Summary

**Branch:** `feature/financial-dashboard` — not yet merged to `stable-reviewed-history`.
**Latest commit:** `0bc03db`

**What it does:** Fetches sales invoices, purchase invoices, and expenses from Daftra via the `/daftra/...` proxy. Shows period-based revenue, costs, VAT, profit, and an estimated profit tax reserve.

**Two period-independent top cards:**
- Yellow: Estimated Profit Tax Payable End of Year — always YTD, always 20% of positive profit. Subtitle: `20% of estimated business profit · management estimate`.
- Red: VAT Reconciliation — always current Gregorian quarter (Q1=Jan–Mar, Q2=Apr–Jun, Q3=Jul–Sep, Q4=Oct–Dec).

**Period selector:** Year to Date · This Month · Last Month · Q1 · Q2 · Q3 · Q4 · All Time. Does not affect the two top cards.

**Personal Transfers panel:** 7 purchase invoices excluded from all business figures, shown separately. Identified by `supplier_business_name.trim().toLowerCase() === 'personal transfer'`.

**Key locked rules:**
- Browser calls `/daftra/...` only — never `daftra.com` directly.
- No auto-fetch. Manual button only.
- VAT derived as `summary_total − summary_subtotal` (not `summary_tax1` — always null).
- Purchase invoice number: `r.no` (e.g. `000048`), not `r.number`.
- No localStorage / sessionStorage.

---

## 9. Major Completed Decisions and Why

| Decision | Why |
|---|---|
| Media index in localStorage, not fetched on load | 89 tasks × up to 10 API calls each would take ~30 seconds silently on every load |
| No signed Notion URLs in the index | They expire in ~1 hour — storing them gives a false impression of persistence |
| Media index scans Done tasks too | 23 of 27 media-containing tasks are Done — filtering by active status hides most content |
| Single `openDetail(id)` for all entry points | Previously separate code paths caused different media results from different tabs |
| `isReviewedByMe` gates Reviewed chip, not `isReviewedAndFresh` | Setting a task to Done in Notion updates `last_edited_time`, which would silently eject it from Reviewed — this is the wrong behaviour for a permanent review history |
| Old `sigTaskWords`/`findRelatedByDescription` replaced with 5-tier system | Old single-pass functions produced false positives. New `sigWords`/`sigBigrams`/`findRelatedTasks` with `RELATED_STOP` + category + threshold controls eliminated them. |
| QR from d64 param, not image fetch | `fetch(qr_code_url)` is blocked by CORS on both `file://` and `localhost` |
| Canvas → PNG data URL before PDF export | html2canvas captures `<img src="data:...">` reliably; live `<canvas>` sometimes renders blank |
| No `min-height: 297mm` on PDF page | Fixed height forced a blank second page on short invoices |
| No flexbox on PDF page root | Previously caused footer to overlap content on long invoices |
| Terms & Conditions use `<div>` + `·`, not `<ul>/<li>` | `<ul>` with `direction: rtl` on the container renders bullet dots on the wrong side |
| Daftra API calls routed through proxy in Financial Dashboard | Keeps the API key out of browser source. Document Generator predates this route and is stable — two patterns coexist intentionally. |
| Personal transfers excluded from business calculations | Owner withdrawal records distort profit and VAT if included. Separated pre-calculation into `personalRecords` / `bizPurRecords`. |

---

## 10. Blocked and Deferred Features

| Feature | Status | Blocker |
|---|---|---|
| Comments & @mentions (Phase 2B) | ❌ Blocked | All tasks return `403 restricted_resource`. Hussam must enable "Read Comments" on the "Youssef" integration in Notion (Saura Agency workspace). No code changes needed once enabled. |
| Notion write-back / Mark Reviewed in Notion (Phase 2D) | ⏳ Not started | Requires Hussam to add a `Youssef Reviewed` checkbox to his database + write permission on the integration |
| Notion `relation` property for Related Tasks | ⏳ Preferred future | Hussam adding a native Notion relation property would replace localStorage detection. `vista_task_relations_v1` can be retired once all existing links have Notion-side equivalents. |
| Personal Task Center (Phase 3) | ⏳ Not started | `personal-dashboard.html`, Youssef's private Notion workspace |
| Google Drive Media Archive (Phase 4) | ⏳ Deferred | Scheduled after Personal Task Center |

---

## 11. Known Limitations

- **Comments blocked** — `403 restricted_resource` on all tasks. Integration permission not yet enabled.
- **100-document cap** — document generator fetches `?limit=100&page=1` only. Pagination not implemented.
- **Tables nested in containers not rendered** — Notion does not expose nested table rows in a single children API response.
- **Media index goes stale** — must be manually refreshed. New tasks added after the last scan appear as unscanned.
- **Multi-page PDF not stress-tested** — `page-break-inside: avoid` is set, but behavior on invoices with 20+ line items has not been fully verified.
- **Visual click-through testing pending** — programmatic validation of unified detail loading passed, but live click-through (same task from all entry points, quick tab switching) has not been confirmed during normal use.
- **API key hardcoded** — Daftra API key is in the HTML source. Acceptable for single-user internal use; not for sharing or hosting.

---

## 12. Latest Approved Behaviour (Reviewed chip — corrected 2026-06-05)

The Reviewed chip was corrected from a "freshness filter" to a "permanent history" model.

**Before (wrong):** `isReviewedAndFresh` gated the chip — tasks disappeared from Reviewed when Notion status changed to Done (updating `last_edited_time`) or any Notion edit occurred after review.

**After (correct):**
- `isReviewedByMe(t)` — review record exists → task stays in Reviewed. Only Remove Review removes it.
- `isReviewedStale(t)` — review exists but Notion was edited after → shows amber **Updated Since Review** badge. Task may also return to Needs My Attention.
- `isReviewedAndFresh(t)` — unchanged, used only by `attentionFilter` to suppress tasks from Needs My Attention while fresh.
- Done tasks always visible in Reviewed regardless of Include Done toggle.

---

## 13. Recommended Next Priorities

1. **Enable Comments (Phase 2B)** — ask Hussam to enable "Read Comments" on the Notion integration. No code changes needed; the error handler is already in place.
2. **Live click-through validation** — open the same task from Media Library, Task Tracker, Needs My Attention, and search results; switch tasks quickly to confirm no stale overwrites.
3. **Personal Task Center (Phase 3)** — `personal-dashboard.html` using `notion.personal` config.
4. **Notion relation property** — ask Hussam to add a `Related Tasks` relation to his database; replaces localStorage-based manual links.
5. **Phase 2D (Notion write-back)** — only after Phase 2B is validated in daily use.

---

## 14. Approaches That Must Not Be Repeated

- **Do not restore `sigTaskWords()` or `findRelatedByDescription()`** — the old functions that produced false positives. The current 5-tier system (`sigWords`, `sigBigrams`, `findRelatedTasks`) is the correct replacement and is already live. Do not collapse it back to "two signals only."
- **Do not gate the Reviewed chip with `isReviewedAndFresh`**. This was the bug. Use `isReviewedByMe` for the Reviewed view; keep `isReviewedAndFresh` only in `attentionFilter`.
- **Do not add entry-point-specific code paths in `openDetail`**. All tabs must use the same function with the same task ID format.
- **Do not store Notion signed URLs** anywhere persistent (localStorage, cookies, etc.). They expire in ~1 hour.
- **Do not reorder `.set()` after `.from()` in the html2pdf chain**. It silently corrupts the export.
- **Do not switch the QR from the `d64 → atob()` pipeline** to fetching `qr_code_url` directly. CORS blocks it.
- **Do not add `min-height: 297mm` or `display: flex` to the PDF page root**. Both were tried and caused layout regressions.
- **Do not prepend `INV#` or `QUO-` to document numbers**. Pass through from Daftra exactly.
- **Do not commit `config.json`**. It contains live API tokens.
- **Do not add direct `daftra.com` browser calls to `financial-dashboard.html`**. All Daftra fetches must go through `/daftra/...`.
- **Do not add auto-refresh, `setInterval`, `setTimeout`, or `DOMContentLoaded` data-fetching to `financial-dashboard.html`**. Manual fetch only.
- **Do not include personal transfer records in business profit, VAT, or purchase totals**. Pre-filter at `renderContent()` before any calculation.
- **Do not migrate the Document Generator to the proxy Daftra route**. It is stable and its pattern is intentional.

---

## 14. Platform Workflow Rules (permanent — added 2026-06-07)

### Git workflow — required order for every approved change

1. **Validate locally** — run `python proxy.py` and test in the normal browser before declaring anything done.
2. **Document if needed** — update `CLAUDE_CONTEXT.md`, `docs/changelog.md`, and relevant docs for any functional or architectural change.
3. **Commit to Git** — stage only the relevant files; write a clear commit message.
4. **Push to GitHub after approval** — do not push without the user explicitly confirming the commit is approved.

### Push safety rules

| Rule | Detail |
|---|---|
| Never push `config.json` or secrets | `config.json` is git-ignored; never force-add, never echo its contents |
| Never force-push | Do not use `--force` or `--force-with-lease` unless the user explicitly approves with those words |
| Never push broken experimental work | Only push to a backup/feature branch if the user asks for it by name |
| Push only the active approved branch | Default push target is the approved branch for that module only |
| `stable-reviewed-history` is the approved branch | Social Media / Notion dashboard approved branch |
| `stable-reviewed-history-v1` is the restore tag | Points to `2d0faec` — the approved stable snapshot |
| `feature/financial-dashboard` must not be pushed | Do not push unless the user explicitly approves |

### localStorage — reviewed task history

| Fact | Detail |
|---|---|
| Storage location | Normal browser localStorage only — key `vista_reviews_v1` |
| Incognito windows | Always show empty reviewed history — localStorage is isolated per window type |
| Browser F5 refresh | Does NOT delete reviewed tasks — localStorage persists across refreshes |
| Different browser or device | Will not see reviewed history — localStorage is per browser per machine |
| Clearing browser data | Will delete reviewed history if "Local storage" or "Site data" is included |

---

## How to Resume

Before recommending or making any change, ChatGPT must:

1. **Read `CLAUDE_CONTEXT.md`** — the permanent source of truth for both the Social Media Control Center and the Document Generator. Contains all approved behaviour rules, locked implementations, and validation checklists.
2. **Read `docs/changelog.md`** — shows what changed and when. The most recent entries reflect the current approved state.
3. **Read `docs/roadmap.md`** — shows phase status, what is complete, what is blocked, and what is next.
4. **Read `docs/decisions.md`** — explains the reasoning behind structural choices. Consult before proposing any architectural change.
5. **Check `git log --oneline -10`** — confirm which branch you are on. Stable branch is `stable-reviewed-history` (commit `2d0faec`). Financial Dashboard feature branch is `feature/financial-dashboard` (latest commit `0bc03db`). Do not assume the two are merged.
6. **Check `git status`** — confirm working tree is clean before any work begins.
7. **Read the relevant section of `social-dashboard.html`** before changing any JS function. Do not rely on summaries alone — the function signatures, guard conditions, and localStorage schemas matter exactly.
8. **Do not suggest changes to locked items** (QR pipeline, html2pdf chain, `attentionFilter` + `isReviewedAndFresh` interaction, `openDetail` unification) without first confirming the lock is documented in `CLAUDE_CONTEXT.md` and has a clear reason to revisit.
