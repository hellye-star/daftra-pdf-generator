# Roadmap — Vista Platform

---

## Platform Status

| Module | File | Status |
|---|---|---|
| Vista Homepage | `index.html` | ✅ Live |
| Document Generator | `daftra-pdf-generator_1.html` | ✅ Live — stable |
| Social Media Control Center | `social-dashboard.html` | ✅ Live — Phase 2A complete + detail unification |
| Personal Task Center | `personal-dashboard.html` | ⏳ Planned |
| Local Proxy | `proxy.py` | ✅ Live |
| Config template | `config.example.json` | ✅ Done |
| Git safety | `.gitignore` | ✅ Done — config.json excluded |

---

## Notion Sources

Two separate Notion workspaces are supported. Each has its own integration token, database ID(s), and dashboard module. They are fully isolated from each other.

| Source | Dashboard | Purpose |
|---|---|---|
| **Hussam / Vista shared** | `social-dashboard.html` | Social media tasks, media/assets, meeting agendas, items needing approval or review |
| **Youssef personal** | `personal-dashboard.html` | Private task list, personal reminders, follow-ups, items tracked independently |

Both use **read-only** Notion integration tokens. Write-back (e.g. marking a task reviewed in Notion) will be added in Phase 2 only after read-only flow is validated.

---

## Social Media Control Center — Phase Progress

### ✅ Phase 1 (complete)
- Proxy, task fetching, Needs My Attention tab, Task Tracker tab, task detail panel, sidebar filters.

### ✅ Phase 1.5 + Search (complete)
- Media Library tab with manual index (`localStorage`, no signed URLs stored).
- Nested block detection (images/files inside list items, callouts, toggles).
- On-demand media loading with fresh signed URLs.
- Quick-filter chips (All, Attention, Content, Social, Media/Posts, Overdue, **Reviewed** — see Phase 2A note below).
- Four distinct empty-state messages for task detail media section.
- Video / File / PDF / Embed / Link type clarity.
- Font and readability improvements throughout.
- Media audit: 89 tasks scanned, 27 with confirmed media.
- Live sidebar search across all tabs — task name, description, status, assignee, category, due date, media type labels. Stacks with chips and sidebar filters. × clear button.
- Notion table rendering in task detail panel — inline HTML table with column/row header support, link-aware cells, per-table row fetch, graceful fallback.
- Contextual "Open This Task in Notion ↗" button in empty states only (empty page, text-only, unsupported content, fetch error). Hidden whenever supported content renders.
- Task detail loading unified across all entry points (Media Library, Task Tracker, Needs My Attention, search results) — single `openDetail(id)` function, no tab-specific paths.
- Race condition fix — `_detailTaskId` guard prevents stale fetch results from overwriting the currently open task. All 7 `el.innerHTML` write points in `loadDetailMedia` and `loadDetailComments` are guarded.
- Platform Home navigation link added to `daftra-pdf-generator_1.html` topbar.
- Programmatic validation passed 2026-06-05. Visual click-through testing pending.

### ✅ Phase 2A — Mark Reviewed (local, no Notion write) — complete 2026-06-04
See "Mark Reviewed — Local Tracking" section below.

**Reviewed quick-filter chip — added 2026-06-05, corrected 2026-06-05:**
- Chip placed beside Overdue in the quick-filter strip. No new main tab.
- Filters Task Tracker to tasks where `isReviewedByMe(task)` is true — review record exists regardless of staleness.
- **Permanent history:** a task stays in Reviewed until the user manually clicks Remove Review. Notion edits and Done status changes never remove it.
- Done tasks are always visible under Reviewed regardless of the Include Done toggle.
- Tasks edited in Notion after review show an amber **Updated Since Review** badge; they may also return to Needs My Attention but remain in Reviewed.
- Search, Category, and Assignee filters apply. Include Done does not hide reviewed tasks.
- Live count `· N` reflects all tasks with a review record (regardless of staleness).
- Empty state: "No reviewed tasks match the current filters."

### ✅ Phase 2A.5 — Related Supporting Tasks — COMPLETE (2026-06-05)

The Related Supporting Tasks section is live in the task detail panel. All fuzzy/description-based matching has been removed. The section is always visible when a task is open, with a "Link Supporting Task" button available at all times.

**Detection signals (final — no others permitted):**

| Signal | Type | How it works |
|---|---|---|
| Explicit Notion page link | Automatic | `notionLinksFromBlocks()` scans top-level block rich_text for `app.notion.com/p/` URLs, extracts the UUID prefix, and matches against `allTasks`. Zero false positives. |
| Linked by You | Manual (local) | User clicks "+ Link Supporting Task", searches all tasks, selects one. Stored in `localStorage` key `vista_task_relations_v1`. Bidirectional — opening either task shows the connection. |

**Explicitly removed:**
- `RELATED_STOP` constant — gone
- `sigTaskWords()` function — gone
- `findRelatedByDescription()` function — gone
- All word-overlap, keyword, bigram, exact-name, and description-based detection — none of it remains

**What each Related task card shows:**
- Full task name
- Status, category, and assignee badges
- Confirmed media types from the Media Index (Tables, Links, Images, etc.)
- Source label: "Explicit Notion link" or "Linked by You"
- "Open Task →" button (calls shared `openDetail(id)`)
- "Remove link" button — only for manually created links (Notion-link cards are read-only)

**localStorage schema — `vista_task_relations_v1`:**
```json
{
  "actionTaskId": [
    { "relatedTaskId": "supporting-task-id", "createdAt": 1748000000000 }
  ]
}
```
Both directions are stored explicitly. Opening either linked task surfaces the connection. No Notion API calls, no write permissions required.

**Validated (2026-06-05):**
- "Tag keywords as positive or negative" and "Ad keyword research (not final)" linked manually — confirmed Tables and Links badges appear, opening either task loads content correctly.
- "Research tote bag demand in KSA" does not appear unless manually linked.
- No fuzzy or description-word matching remains in the codebase.

**Functions added/changed:**
- `notionLinksFromBlocks(blocks)` — unchanged (kept from previous implementation)
- `RELATIONS_KEY` — `'vista_task_relations_v1'`
- `loadRelations()`, `saveRelations(data)`, `saveRelation(a, b)`, `removeRelation(a, b)`, `getManualRelatedIds(taskId)`
- `buildRelatedTasksHTML(related, currentTaskId)` — rewritten; source label + conditional Remove Link button
- `removeManualRelation(currentTaskId, relatedTaskId)` — removes relation and re-renders section in place
- `_refreshRelatedSectionFull(taskId)` — rebuilds the Related section from localStorage + DOM-preserved Notion-link cards
- `openTaskSelector(currentTaskId)`, `closeTaskSelector()`, `renderTaskSelectorList(query)`, `selectRelatedTask(relatedTaskId)` — task selector modal
- `loadDetailMedia` — related block updated: no `findRelatedByDescription` call; uses `getManualRelatedIds` + `notionLinksFromBlocks` only
- `#detail-related-wrapper` — always visible; "+ Link Supporting Task" button always present

### ⏳ Related Tasks — preferred long-term solution
A proper Notion `relation` property added by Hussam to his database is the correct permanent solution. It would:
- Be immune to task renames (links are by page ID, not text)
- Be bidirectional natively in Notion
- Require no threshold tuning or localStorage management
- Allow the dashboard to read the relation directly from the task's API response with zero detection logic

When Hussam adds a `Related Tasks` relation property, read it from the task properties in `loadAllTasks()` and surface it as a third signal alongside explicit Notion links. The card rendering, `openDetail` integration, and "Linked by You" manual links require no changes. The `vista_task_relations_v1` localStorage store can be retired once all existing manual links have Notion-side equivalents.

### ⏳ Phase 2B — Comments & @mentions  ← next
- Prerequisite: "Read comments" capability enabled on the Notion integration. **Live test performed 2026-06-05 — currently blocked.** All tasks return `403 restricted_resource`. Integration is "Youssef" bot in Saura Agency workspace. Hussam must enable Read Comments on this specific integration in Notion settings. No code changes needed once enabled.
- Live comment fetch in task detail panel with author, timestamp, and highlighted @mentions.
- Mentions Index — manual scan (like Media Index). Stores per-task: mention detected, latest comment timestamp, short preview, last scanned timestamp. Key `vista_mentions_index_v1`.
- "Mentioned Me" quick-filter chip and "Mentioned You" badge on task rows.
- Mentioned tasks surfaced in Needs My Attention at high priority (above Blocked).
- Staleness interaction with Mark Reviewed: a reviewed task that receives a new comment mentioning Youssef is treated as requiring attention again.
- Mention matching uses Youssef's personal Notion user ID from `config.json → notion.social_media.notion_user_id`. The proxy exposes this value via a new `/notion/personal/config/user_id` route so it never appears in HTML source.

### ⏳ Phase 2C — Review workflow refinement (post-comments)
- Reassess Mark Reviewed staleness rules now that comment timestamps are available.
- Consider whether a new comment (even without @mention) should trigger attention for Youssef-assigned tasks.
- Only refine after Phase 2B has been validated in daily use.

### ⏳ Phase 2D — Mark Reviewed (Notion write-back)
- Add "Youssef Reviewed" checkbox property to Hussam's database.
- `PATCH /notion/pages/{id}` via proxy sets the checkbox.
- Requires write permission added to the integration.
- Only build after Phase 2A is validated in daily use.

### ⏳ Phase 3 — Personal Task Center
- `personal-dashboard.html` for Youssef's private Notion workspace.
- Separate integration token, separate database ID.
- Homepage card flip from "Coming soon" to "Live" on completion.

### ⏳ Phase 4 — Google Drive Media Archive
- Browse and link Google Drive assets from within the dashboard.
- Previously deferred indefinitely; now scheduled after Personal Task Center.

### ⏳ Not planned
- Real-time sync (pull-on-demand is sufficient)
- Multi-user or cloud hosting
- Notion write-back before Phase 2B and 2C are validated

---

## Mark Reviewed — Local Tracking (Phase 2A design)

### Approach
Local `localStorage` only. No Notion write permission required. No changes to Hussam's database. No new proxy routes.

### Storage — `localStorage` key `vista_reviews_v1`
```json
{
  "taskId_abc123": {
    "reviewedAt":      1748000000000,
    "taskName":        "Share team member images and descriptions",
    "lastEditedTime":  "2026-06-01T10:30:00.000Z",
    "note":            ""
  }
}
```

**`lastEditedTime` is the key field.** When a task is marked reviewed, the current `last_edited_time` from Notion (`t.properties['Updated at'].last_edited_time`) is stored alongside the review record. On every load, the stored value is compared to Notion's current value. If the task has been edited in Notion after it was reviewed, it is automatically treated as unreviewed and reappears in Needs My Attention — no manual action needed.

### Behaviour rules

| Rule | Detail |
|---|---|
| Mark Reviewed button | Small outline button in the task detail panel. Writes to `vista_reviews_v1` with current `last_edited_time`. |
| Staleness auto-reset | If `Notion.last_edited_time > stored lastEditedTime` → treat as unreviewed. Task returns to Needs My Attention automatically. |
| Needs My Attention removal | A reviewed, non-stale task is **fully suppressed from Needs My Attention regardless of status.** Blocked, overdue, and Pending Feedback do not override the review — the task leaves the attention queue until Hussam edits it. |
| Task Tracker | Reviewed tasks stay fully visible in the tracker with all normal indicators (overdue, Blocked, Pending Feedback) intact, plus a muted "Reviewed by You" badge. |
| Detail panel badge | Task detail shows "Reviewed by You · {date}" when reviewed and not stale. |
| Undo | A 10-second undo link appears immediately after marking reviewed. |
| Remove review | "Remove review" link visible in the detail panel for any reviewed task. Removes the record instantly; task reappears in Needs My Attention if applicable. |
| No Notion calls | Zero new API calls, zero new proxy routes, zero new permissions required. |

### Upgrade path to Phase 2C (Notion write-back)
1. Hussam adds `Youssef Reviewed` (checkbox) to his database.
2. `loadAllTasks()` reads this property and treats it as reviewed if true.
3. Mark Reviewed fires `PATCH /notion/pages/{id}` **and** writes to `localStorage` as fallback.
4. `localStorage` store deprecated once Notion write-back is confirmed stable.

### Functions to add (do not implement until approved)
- `REVIEWS_KEY = 'vista_reviews_v1'`
- `loadReviews()`, `saveReview(id, name, lastEditedTime)`, `removeReview(id)`
- `isReviewed(id)` — true if record exists and `lastEditedTime` matches current Notion value
- `isStale(id, currentLastEdited)` — true if task was updated in Notion after it was reviewed
- Update `attentionFilter()`: wrap every qualifying condition (Youssef-assigned, Blocked, Pending Feedback, overdue) with `&& !isReviewedAndFresh(t)` — review suppresses the task from the entire queue, not just the Youssef-assigned path
- Update `taskRow()`: add muted ✓ badge when reviewed and not stale
- Update `buildDetailHTML()`: add Mark Reviewed button and "Reviewed by you · {date}" badge

---

## Document Generator Status

Invoice and quotation PDF generation is complete and stable. The sections below describe known limitations, potential improvements, and ideas for future work — none of which are required for the current use case.

---

## Known Limitations

### Pagination (100 documents)
List endpoints use `?limit=100&page=1`. Accounts with more than 100 invoices or quotations will see a truncated list. Pagination is not implemented.

**Potential fix:** Fetch additional pages when the returned count equals the limit, appending results until an empty page is returned.

---

### Multi-page PDF Pagination
`page-break-inside: avoid` is set on key blocks (footer, summary, info grid, totals) but multi-page behavior on invoices with many line items has not been fully stress-tested. Rendering may vary by browser.

**Potential fix:** Test with a 20+ item invoice and adjust page-break rules if needed.

---

### API Key Hardcoded in Source
The Daftra API key is hardcoded in the HTML file. This is acceptable for single-user internal tooling but is not suitable for sharing or hosting publicly.

**Potential fix:** Move to a `.env`-style config file or a local settings sidebar that stores the key in `localStorage`.

---

### No Offline Fallback
Google Fonts and the html2pdf CDN are required at runtime. PDFs generated without internet access may have missing fonts or fail to export.

**Potential fix:** Bundle fonts and libraries as base64 or host locally.

---

### CORS Dependency
All Daftra API calls are made client-side. If Daftra changes its CORS policy, the tool stops working.

**Potential fix:** Add a lightweight proxy (e.g. a local Node.js or Python server) to relay API calls.

---

## Possible Future Features

### Purchase Orders
If Daftra exposes a purchase order endpoint, the same architecture could be extended. A new `renderPORow` function and `pp-table-po` class would follow the same pattern as quotations.

### Delivery Notes / Receipts
Similar single-page documents that could reuse the header, info grid, and footer components.

### Client Logo / Letterhead Customisation
Allow the user to upload a different logo via the sidebar without editing the HTML. Would require replacing the base64 constant at runtime.

### Email Integration
A "Send PDF" button that triggers the browser's mail client with the PDF attached, or calls a mail API.

### Dark Mode / Alternate Brand Theme
A CSS variable swap could produce an alternate colour scheme without changing layout.

### Pagination in Document List
Fetch all pages from Daftra rather than stopping at 100 documents.

---

## Near-Term Technical Cleanup

- **Move hardcoded API key to a local config/env file.** The key is currently embedded in the HTML source. Move it to a separate `config.js` or `settings.json` that is loaded at runtime and excluded from version control.
- **Add simple backup/versioning workflow.** Before any change to `daftra-pdf-generator_1.html`, copy the current file to a dated backup (e.g. `backups/daftra-pdf-generator_2025-01-01.html`). Keeps a recoverable history without requiring a full Git workflow.
- **Add multi-page PDF stress testing.** Open an invoice with 20+ line items and verify that page breaks fall cleanly — no orphaned totals, no footer overlap, no clipped content on the second page.
- **Keep invoice and quotation modules frozen unless a real bug appears.** Both document types are stable. Do not refactor, rename, or restructure any invoice or quotation code without a confirmed bug report as the trigger.

---

## Not Planned

- Multi-user / authentication — tool is single-user by design
- Cloud hosting — open locally or on a LAN; no hosting required
- Real-time sync — documents are fetched on demand
- Database or backend — no server, by design
