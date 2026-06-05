# Changelog — Vista Platform

---

## [2026-06-05] — Phase 2A.5: Related Supporting Tasks — Complete

### social-dashboard.html — Related Supporting Tasks redesigned

**All fuzzy/description-based matching removed.** Two signals only:

| Signal | Type | Detail |
|---|---|---|
| Explicit Notion page link | Automatic | `notionLinksFromBlocks()` scans top-level block rich_text for `app.notion.com/p/` URLs, extracts UUID prefix, matches against `allTasks`. Unchanged from previous implementation. |
| Linked by You | Manual (local) | "+ Link Supporting Task" button opens a searchable modal. User picks any task (active or Done). Stored bidirectionally in `localStorage` key `vista_task_relations_v1`. |

**Functions removed:**
- `RELATED_STOP` constant
- `sigTaskWords(name)`
- `findRelatedByDescription(taskId)`

**Functions added:**
- `loadRelations()`, `saveRelations(data)`, `saveRelation(a,b)`, `removeRelation(a,b)`, `getManualRelatedIds(taskId)` — localStorage relations store
- `removeManualRelation(currentTaskId, relatedTaskId)` — removes relation and re-renders section in place
- `_refreshRelatedSectionFull(taskId)` — rebuilds Related section from localStorage + DOM-preserved Notion-link cards
- `openTaskSelector(currentTaskId)`, `closeTaskSelector()`, `renderTaskSelectorList(query)`, `selectRelatedTask(id)` — task selector modal

**Functions changed:**
- `buildRelatedTasksHTML(related, currentTaskId)` — new second param; source label "Linked by You" or "Explicit Notion link"; Remove link button on manual cards only
- `loadDetailMedia` related block — removed `findRelatedByDescription` call; uses `getManualRelatedIds` + `notionLinksFromBlocks` only

**HTML changes:**
- `#detail-related-wrapper` — `display:none` removed; always visible; "+ Link Supporting Task" button added inside
- `#ts-overlay` modal — added before `</body>`; searchable task list, Escape to close, click-outside to close

**CSS added:** `.related-task-footer`, `.related-task-remove`, `.link-task-btn`, `.ts-overlay`, `.ts-modal`, `.ts-header`, `.ts-title`, `.ts-close`, `.ts-search-wrap`, `.ts-search`, `.ts-list`, `.ts-item`, `.ts-item-name`, `.ts-item-meta`, `.ts-item-badge`, `.ts-item-type`, `.ts-empty`

**localStorage schema — `vista_task_relations_v1`:**
```json
{ "taskAId": [{ "relatedTaskId": "taskBId", "createdAt": 1748000000000 }] }
```
Both directions stored. No Notion write permissions required.

**Validated (programmatic, 32/32 checks):**
- All fuzzy/description symbols absent from source
- All new relation store + modal symbols present
- `#detail-related-wrapper` no longer has `display:none`
- `descMap` absent from `loadDetailMedia`
- Task selector modal HTML and CSS present

**Live validation steps (to perform during normal dashboard use):**
1. Open "Tag keywords as positive or negative" → click "+ Link Supporting Task" → search "Ad keyword research" → select it → confirm card appears with Tables and Links badges → click "Open Task →" → confirms content loads
2. Open "Ad keyword research (not final)" → confirm "Tag keywords" card now appears (bidirectional)
3. Confirm "Research tote bag demand in KSA" does NOT appear on either task
4. Click "Remove link" on one task → confirm card disappears from both tasks on next open

---

## [2026-06-05] — Session 2 Save Point (correction pending)

### Search: Completed Matches section

When a search query is active and the "Include Done" toggle is off, Done tasks that match the query now appear below the active bucket groups in a "Completed Matches · N" section with a muted divider header and 65% opacity rows. The section is absent when no search query is active, preserving normal browsing behaviour. Tracker badge count reflects active tasks only — Done matches do not affect it.

**Functions changed:** `applyFilters()` — computes `doneMatches` array when `q && !showDone`; `renderTracker(tasks, doneMatches=[])` — appends `.search-done-section` HTML when `doneMatches.length > 0`.

**CSS added:** `.search-done-section`, `.search-done-header`, `.search-done-label`, `.search-done-rule`, opacity rules on `.task-row` within section.

---

### Debug investigation: two-path detail panel test

Comprehensive runtime logging was added to `openDetail` and `loadDetailMedia` (source tags on all entry points, console groups at every decision point) to test whether Media Library and Search/Tracker paths produced different media results. Finding: the paths opened two **different tasks** — "Ad keyword research (not final)" (`372a2557…`) from the Media Library and "Tag keywords as positive or negative" (`373a2557…`) from the Needs My Attention list. The second task genuinely has no page body (0 blocks). No code bug exists in the media loading architecture. All temporary logging removed after validation.

---

### Related Supporting Tasks section — IMPLEMENTED BUT CORRECTION PENDING ⚠

**What was built:**
- New section "Related Supporting Tasks" added to the task detail panel, between Media & Files and Comments.
- Hidden by default (`display:none`); shown only when ≥1 related task is detected.
- Each card: status/category/assignee badges, relationship reason label, confirmed media types from the Media Index, "Open Task →" button calling `openDetail(id)`.
- Related task detail loads normally — media, tables, and content via the shared flow.

**Detection signals implemented:**
1. Signal 1 (correct, keep): explicit Notion page links in top-level block rich_text — UUID prefix extracted from `app.notion.com/p/` URLs and matched against `allTasks`.
2. Signal 2 (REJECTED): ≥2 significant words (≥5 chars, not stop-words) from one task's name appear in the other task's description property, checked bidirectionally.

**Confirmed false positive from Signal 2:**
"Ad keyword research (not final)" shows "Research tote bag demand in KSA" as related. The tote-bag task's description contains "keyword" and "research", which are two significant words from "Ad keyword research" — triggering a match between two entirely unrelated tasks.

**Validation finding (live data, exact matching, 118 tasks):**
With strict exact full-name substring matching (case-insensitive), only 1 pair is detected across the entire database: "Add lanyards + bottles → Apply Youssef feedback - Diverse". The "Tag keywords / Ad keyword research" pair is NOT detectable by exact matching — the description says "keyword research" not the full task name "Ad keyword research (not final)". This pair requires Hussam to add an explicit Notion page link in the task body.

**Required next-session correction:**
Remove `RELATED_STOP` constant, `sigTaskWords()`, and the word-count logic from `findRelatedByDescription()`. Replace with exact case-insensitive full-name substring search of the description property. No other detection signals permitted until a proper Notion `relation` property exists.

**CSS added:** `.related-tasks-list`, `.related-task-card`, `.related-task-meta`, `.related-task-reason-badge`, `.related-task-name`, `.related-task-types`, `.related-types-label`, `.related-type-badge`, `.related-task-open`.

**Functions added:** `RELATED_STOP`, `sigTaskWords(name)`, `notionLinksFromBlocks(blocks)`, `findRelatedByDescription(taskId)`, `buildRelatedTasksHTML(related)`.

**`buildDetailHTML` change:** added hidden `#detail-related-wrapper` / `#detail-related-content` between Media and Comments sections.

**`loadDetailMedia` change:** after the first stale guard, computes Signal 1 + Signal 2, merges (Signal 1 takes priority), and shows/hides the wrapper based on results.

---

## [2026-06-05] — Session Save Point

### Programmatic validation — task detail unified loading
All entry points (Media Library, Task Tracker, Needs My Attention, search results) confirmed to use the same `openDetail(id)` function with identical task ID format. Block fetch verified live for test task `372a2557` (11 blocks, 1 link, 2 tables). Stale-guard coverage confirmed: all 7 `el.innerHTML` write points in `loadDetailMedia` and `loadDetailComments` are preceded by `_detailTaskId !== pageId` guards. Visual click-through testing still pending during normal dashboard use.

### Comments permission test
Live test via proxy confirmed all tasks return `403 restricted_resource` on the comments endpoint. The active integration is the "Youssef" bot in the Saura Agency workspace (`374a2557-c7f8-81eb-a106-002798bada1a`). Read Comments capability must be enabled on this specific integration in Notion settings. No code changes needed — the existing error handler will surface comments automatically once the permission is active.

---

## [2026-06-05] — Task Detail Unification, Race Condition Fix, and Contextual Notion Buttons

### social-dashboard.html — Unified detail loading

- **All entry points now confirmed to use a single shared `openDetail(id)` function.** No entry-point-specific code paths. Media Library, Task Tracker, Needs My Attention, and search results all pass the same task ID format (`t.id` from `allTasks`, or `entry.id` from the media index which stores the same value).
- **`_detailTaskId` race condition fix** — a new global `let _detailTaskId = null` is set in `openDetail` before the async loaders are spawned. Both `loadDetailMedia` and `loadDetailComments` check `_detailTaskId !== pageId` before every `el.innerHTML` write. If the user opens a different task before a fetch completes, the stale result is silently discarded and the spinner for the new task remains in place until its own fetch resolves. 7 write points guarded total (3 in media, 4 in comments).

### social-dashboard.html — Contextual "Open This Task in Notion ↗" button

- **Button appears only when the entire Media & Files section cannot render content:**
  - Page body is empty (no blocks)
  - Task has text/notes only, no renderable media
  - Page uses unsupported or unrecognised block types
  - Block fetch fails (network/proxy error) — button added to error state this session
- **Button does NOT appear** when images, links, tables, or any supported content renders successfully. The `openBtn` string is built in `renderMediaBlocks` and used only in the `emptyMsg` return path, never in the `html` return path.
- **Table fallback** (rows could not be loaded) no longer shows the button — it appears alongside other rendered content where the topbar link is sufficient. `renderNotionTable` signature simplified to remove `notionUrl` parameter.
- **The general topbar "Open in Notion ↗" link remains on every task** and is the only Notion link visible when content renders successfully. No duplicate links.

### Functions added
- `let _detailTaskId = null` — state variable tracking the currently open task

### Functions modified
- `openDetail(id)` — sets `_detailTaskId = id` before spawning async loaders
- `loadDetailMedia(pageId)` — 5 stale guards added (`_detailTaskId !== pageId`); error catch now includes the contextual Notion button
- `loadDetailComments(pageId)` — 4 stale guards added across all response paths
- `renderMediaBlocks(...)` — `openBtn` now used only in `emptyMsg` paths
- `renderNotionTable(block, rows)` — `notionUrl` parameter removed; fallback message no longer includes the Notion button

---

## [2026-06-05] — Platform Home Navigation Link in Document Generator

### daftra-pdf-generator_1.html

- **"← Platform Home" link** added to the topbar, right-aligned via `margin-left: auto`. Links to `index.html`. Opens in the same tab.
- Styled identically to the equivalent link in `social-dashboard.html` — same font size, weight, letter-spacing, colour token (`var(--light)`), and hover state (`#fff`).
- Placed in the topbar `<div>` outside `#pdfPage`. Cannot appear in generated PDFs — `html2pdf` captures only the `#pdfPage` element.
- No invoice, quotation, QR, or PDF export logic was modified.

---

## [2026-06-05] — Task Detail Empty States — Contextual Notion Button

### social-dashboard.html — "Open This Task in Notion ↗" in empty states

- Added `.notion-open-btn` CSS class — dark button (`background: var(--dark)`), `display: inline-block`, `padding: 11px 20px`, consistent Jost font styling.
- Button uses the task's own Notion URL (`t.url` looked up from `allTasks` by `pageId` inside `loadDetailMedia`) — always links to the specific task, never to a workspace root.
- Added to three empty-state messages in the Media & Files section:
  - Empty page body (`topBlocks.length === 0`)
  - Text-only task (no renderable media, `hasText = true`)
  - Unsupported block types (no text, no media)
- Later refined (same session): removed from `renderNotionTable` fallback and from `renderMediaBlocks` html path — button only appears in full-section empty states.

---

## [2026-06-04] — Notion Table Rendering in Task Detail

### social-dashboard.html — Inline table rendering

- **Tables now render directly in the task detail panel** instead of showing the "cannot be rendered" fallback message.
- `loadDetailMedia` detects top-level `table` blocks, fetches their child rows via one additional Notion API call per table, and passes a `tableRowsMap` to `renderMediaBlocks`.
- `renderNotionTable(block, rows)` builds a proper HTML table with `<thead>`/`<tbody>` split when Notion marks a column header row (`has_column_header`), and `<th>` cells for row-header columns (`has_row_header`).
- `richTextToHTML(arr)` converts each `rich_text` segment to escaped HTML, preserving `href` links as `<a target="_blank">` elements.
- Multiple tables in a single task are each rendered separately with a spacer between them.
- A muted row · column count note is shown below each rendered table.
- **Fallback preserved** — if rows cannot be fetched, the graceful fallback with "Open in Notion" is shown for that individual table only; other media in the same task is unaffected.

### Supported
- Plain text cells
- Links inside cells (rendered as clickable anchors)
- Column header row (`has_column_header` → `<thead>` with `<th>`)
- Row header column (`has_row_header` → first `<td>` becomes `<th>`)
- Multiple tables per task
- Horizontal scroll on wide tables (`.notion-table-wrap { overflow-x: auto }`)

### Known limitations
- Tables nested inside toggles, callouts, or other containers are not rendered (Notion API does not expose nested table rows in the same children response).
- Cell formatting (bold, italic, colour) is not applied — plain text and links only.

### Functions added
- `richTextToHTML(arr)` — converts Notion rich_text array to safe HTML with link support
- `renderNotionTable(block, rows)` — renders a Notion table block as a styled HTML table

### Functions modified
- `loadDetailMedia` — added table block detection, per-table row fetch, and `tableRowsMap` construction; passes map as third arg to `renderMediaBlocks`
- `renderMediaBlocks(topBlocks, allBlocks, tableRowsMap = {})` — added optional third param; `tables` array now stores full block objects (not just IDs); table section replaced with `renderNotionTable` calls

---

## [2026-06-04] — Search

### social-dashboard.html — Live search across all tabs

- **Search box** added to the sidebar between the Tasks and Filter sections. Styled consistently with existing sidebar controls.
- **Live filtering** — results update on every keystroke (`oninput`). No debounce needed at current dataset size.
- **× clear button** appears inline inside the search box when the query is non-empty. Clicking it resets the query and restores the full view.
- **Search fields per task:** task name, description, status, assignee, category, due date (ISO), Due Date 2 formula, bucket formula, and media type labels from the index (image, video, file, pdf, embed, link) where a scan record exists.
- **Media Library search** uses `searchMatchesEntry` — same field set minus task-specific fields, using the index entry's `name`, `category`, `status`, `dueDate`, and `mediaTypes[]`.
- **Additive with existing filters** — search is applied after chips and sidebar dropdowns in `applyFilters()`. A task must satisfy both the chip/filter selection and the search query to appear.
- **Applies to all three tabs:** Needs My Attention, Task Tracker, Media Library.
- **Badge counts** on tabs reflect the post-search filtered totals.
- **Mark Reviewed logic unchanged** — `isReviewedAndFresh` runs inside `attentionFilter` before search is applied; the two features compose correctly.

### Functions added
- `onSearchInput(val)` — updates `searchQuery`, toggles clear button, calls `applyFilters()` and `renderMediaLibrary()` if on the Media tab
- `clearSearch()` — resets state, clears input, re-renders
- `searchMatches(t, q)` — case-insensitive substring test across all task fields including media type labels from the index
- `searchMatchesEntry(entry, q)` — same for Media Library index entries

### State added
- `let searchQuery = ''`

---

## [2026-06-04] — Phase 2A: Mark Reviewed by Me

### social-dashboard.html — Mark Reviewed (localStorage only)

- **Mark Reviewed button** added to the task detail panel (between the summary strip and the Description section). Writes to `localStorage` key `vista_reviews_v1`.
- **Reviewed tasks are fully suppressed from Needs My Attention** regardless of status — Blocked, Pending Feedback, and Overdue no longer override the review. The task leaves the attention queue until Hussam edits it in Notion.
- **Staleness auto-reset** — `isReviewedAndFresh(t)` compares the stored `last_edited_time` snapshot to the task's current Notion `last_edited_time`. If Hussam has edited the task after it was reviewed, the function returns `false` and the task reappears in Needs My Attention automatically.
- **"✓ Reviewed" badge** appears in the Task Tracker below the status badge for any reviewed, non-stale task. All other row indicators (overdue warning, Blocked/Pending Feedback border colour) remain intact.
- **"Reviewed by You · {date}" bar** appears in the detail panel for reviewed tasks, with a "Remove review" link that immediately reverses the review and restores the task to Needs My Attention if it qualifies.
- **10-second undo toast** appears at the bottom of the screen after marking reviewed. Clicking Undo reverses the action without requiring the detail panel to be open.
- **No Notion API calls added** — zero new proxy routes, zero new permissions, zero write requests. Pure `localStorage`.

### localStorage schema — `vista_reviews_v1`
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

### Functions added
- `loadReviews()`, `saveReview(id, name, lastEditedTime)`, `removeReview(id)`
- `isReviewedAndFresh(t)` — true if reviewed AND `updated(t) <= rec.lastEditedTime`
- `reviewSectionHTML(t)` — renders either the Mark Reviewed button or the Reviewed bar
- `refreshDetailReviewSection(t)` — updates `#detail-review-section` in place without re-fetching media or comments
- `markReviewed(id)`, `removeReviewAction(id)`, `undoReview()`
- `showUndoToast(id)`, `hideUndoToast()`

### Functions modified
- `attentionFilter()` — added `if (isReviewedAndFresh(t)) return false` before all qualifying conditions
- `taskRow()` — added `b-reviewed` badge when `isReviewedAndFresh(t)`
- `buildDetailHTML()` — added `<div id="detail-review-section">` between the summary strip and Description

---

## [2026-06-04] — Session save point

### Platform state at close of session

| Module | State |
|---|---|
| `proxy.py` | ✅ Live — `python proxy.py` on `localhost:8080` |
| `social-dashboard.html` | ✅ Live — Phase 1.5 complete |
| `daftra-pdf-generator_1.html` | ✅ Live — stable, no changes this session |
| `index.html` | ✅ Live — no changes this session |

### Social Media Control Center — confirmed working
- Notion task fetch: 114 tasks loaded, paginated, Notion API `2025-09-03`.
- Needs My Attention: 11 tasks surfaced.
- Task Tracker: 72 active tasks, bucket-grouped.
- Media Library: 27 confirmed-media tasks from 89 scanned.
- Media index in `localStorage` (`vista_media_index_v1`): no signed URLs stored.
- Task detail: live block fetch on open, one level of nested media detection.
- Comments: not active — "Read comments" permission not enabled on the Notion integration.

### What is NOT started
- Mark Reviewed (Phase 2A) — designed and documented, not implemented.
- Comments & @mentions (Phase 2B) — blocked on enabling "Read comments" in Notion integration settings.
- Notion write-back (Phase 2C) — not started.
- Google Drive saving — deferred indefinitely.
- Personal Task Center — not started.

### Next planned feature
**Mark Reviewed by Me — Phase 2A (local `localStorage`, no Notion write).**
Full design spec in `docs/roadmap.md` and `CLAUDE_CONTEXT.md`.
Key implementation detail: store `last_edited_time` with each review record. If the task is updated in Notion after being reviewed, it automatically reappears in Needs My Attention without any manual action.

---

## [2026-06-04] — Phase 1.5: Media Library + Media Index + Detail Panel Improvements

### social-dashboard.html — Media Library

- **Media Library tab** added as a third tab alongside Needs My Attention and Task Tracker.
  - Shows only tasks with confirmed media (27 of 89 in current scan).
  - Card grid: task name, media type badges (🖼 Images, ▶ Video, 📎 File, 📄 PDF, 🔗 Embed/Link), category · status · due date, "View →" opens the task detail panel.
  - Done tasks included and shown with reduced opacity — media from completed work is fully accessible.
  - Legend strip hidden on the Media Library tab (not relevant there).
  - Empty state when index not yet built: clear instructions to click Refresh Media Index.

- **Manual media index** (`localStorage` key `vista_media_index_v1`).
  - Triggered by new "Refresh Media Index" sidebar button (separate from Refresh Tasks, styled as outline button).
  - Scans ALL Content/Social/Ads & Testing tasks regardless of Done status (89 tasks total).
  - Stores per-task metadata: ID, name, category, status, due date, mediaTypes[], hasMedia, scannedAt timestamp.
  - **No signed Notion URLs stored** — URLs expire after ~1 hour and are always fetched live.
  - Progress bar updates every 5 tasks; full scan takes ~30 seconds.
  - After scan: sidebar shows "27 tasks with media · 89 tasks scanned · {date}, {time}".
  - On `loadAllTasks()`: new tasks not yet in index are registered as unscanned. Sidebar shows "⚠ N new tasks not yet scanned" warning.
  - Media Library badge shows confirmed-media count ("27"); shows "—" when index not built.

- **Scan results from first run (2026-06-04):**
  - 89 tasks scanned (Content 40, Social ?, Ads & Testing ?)
  - 27 tasks with confirmed media
  - 62 tasks with no detectable media (47 empty pages, ~10 text-only, ~5 other)
  - Media type breakdown: 14 image · 8 link · 5 video · 4 embed · 3 file
  - By status: 23 Done · 2 In progress · 2 Not started

### social-dashboard.html — Nested media detection

- `loadDetailMedia()` now fetches **one level of nested children** for container blocks with `has_children: true`.
  - Supported container types: `toggle`, `column_list`, `column`, `callout`, `synced_block`, `quote`, `bulleted_list_item`, `numbered_list_item`, `to_do`.
  - Max 8 nested containers per task (prevents unbounded API usage).
  - Nested images show a "nested" badge in the bottom-right corner of the thumbnail.
  - Nested file/link items show the parent block type in the type label (e.g. "Image · inside numbered list item").
  - Confirmed fix: "Vista United email signatures" — 4 images previously missed, now detected and displayed.

### social-dashboard.html — Empty-state improvements

Four distinct messages in the Media & Files section of the task detail panel:
- `blockCount === 0` → "This task has no page body — only title and properties exist in Notion."
- Blocks present, text only → "📝 This task has notes and text but no attached images, files, or links."
- Table block present → "📊 This task contains a table that cannot be rendered in the dashboard. Open in Notion."
- Unrecognised blocks only → "No detectable content. The page may use unsupported block types."

### social-dashboard.html — Media type clarity

- `video` blocks labelled **Video** with ▶ icon — no longer grouped with generic embeds.
- `bookmark` → Bookmark, `embed` → Embed, `link_preview` → Link Preview, paragraph links → Link.
- `file` → File (📎), `pdf` → PDF (📄), `image` → Image (🖼).
- `table` blocks render a non-clickable note instead of being silently ignored.

### social-dashboard.html — UI refinements (Phase 1.5 polish)

- Quick filter chips added: All, Needs My Attention, Content, Social, Media / Posts, Overdue.
  - "Media / Posts" combines Content + Social + Ads & Testing in a single click.
  - "Overdue" shows only past-due, non-Done tasks.
  - Sidebar dropdown changes reset the active chip to "All."
- Font sizes increased across: task rows (14px), sidebar (10–13px), legend (11px), detail panel labels/values (9–14px), media section (10–13px).
- Task row overdue indicator: "⚠" prefix + red text when past due date.
- Detail panel widened from 560px → 660px for media content.
- Quick-summary strip at top of detail panel: Status · Due date · Assignee · Category — always visible without scrolling.
- Legend strip explains left-border colours and due-date emoji (🔴🟠🟡🟢).
- Legend strip hidden on Media Library tab.

### social-dashboard.html — Architecture note (53 vs 89 count)

The chip filter applies `showDone = false` and shows ~53 active Media/Posts tasks. The media index always scans all 89 (including Done). This is intentional — the library is a content archive, not a task filter. Documented in code comment and architecture.md.

---

## [2026-06-04] — Phase 1: Social Media Control Center + Proxy

### proxy.py
- Built local Python proxy (stdlib only) serving all HTML files on `localhost:8080`.
- Routes `/notion/personal/...` → `api.notion.com/v1/...` with personal token injected from `config.json`.
- Routes `/notion/social/...` → `api.notion.com/v1/...` with social_media token (ready for when Vista token is configured).
- Binds to `127.0.0.1` only — not reachable from other machines on the network.
- Logs only Notion proxy calls (suppresses static-file noise).
- Startup validates `config.json` — exits with a clear message if file missing or invalid JSON.
- `launch.json` updated to run `proxy.py` directly (replaces bare `http.server`).
- Confirmed working: `GET /social-dashboard.html` → 200, `POST /notion/personal/data_sources/.../query` → 200, 243KB.

### social-dashboard.html
- Full Phase 1 Social Media Control Center built and live.
- Fetches all tasks from Hussam's Notion database (Tasks Tracker data source) with automatic pagination.
- Uses Notion API version `2025-09-03` (required for multi-data-source databases).
- **Needs My Attention tab:** surfaces tasks where Assignee = Youssef, Status = Pending Feedback, Status = Blocked, or Due date is overdue. Sorted by urgency (overdue → Blocked → Pending Feedback → Youssef-assigned). Urgent tasks shown with a red left border.
- **Task Tracker tab:** all tasks grouped by Bucket formula (Due this week / next week / beyond / last week / etc.). Collapsible groups. Blue left accent for Youssef-assigned rows, amber for attention rows.
- **Task Detail panel:** slide-in from right on click. Shows all properties, description, and a placeholder for Phase 2 media/file scanning. "Open in Notion ↗" link on every task.
- Sidebar filters: Category, Assignee, Include Done toggle.
- Escape key closes detail panel.
- Verified live: 11 attention tasks surfaced from first 100 tasks; bucket grouping correct.

### index.html
- Social Media Control Center card updated from "Coming soon" to "Live" and linked to `social-dashboard.html`.

---

## [2026-06-03] — Multi-source Notion config structure

- Added `config.example.json` — safe template with placeholder values, safe to commit.
- Added `.gitignore` — excludes `config.json` (tokens), Windows and macOS OS artifacts.
- `config.json` structure supports two isolated Notion sources:
  - `notion.social_media` — Hussam/Vista shared workspace (Social Media Control Center)
  - `notion.personal` — Youssef's private workspace (Personal Task Center)
- Each source has its own integration token, task database ID, and optional secondary database IDs.
- `proxy.bind` set to `127.0.0.1` in config template — prevents proxy from being reachable on LAN.
- Updated `docs/architecture.md` — reflects multi-module platform, added Notion integration section, data flow, file URL expiry rule, and config structure.
- Updated `docs/roadmap.md` — added Notion sources table, revised next steps to match two-database architecture.

---

## [2026-06-03] — Vista Platform Homepage

- Added `index.html` as the Vista United internal platform homepage.
- Three-card layout: Document Generator (live), Social Media Dashboard (coming soon), Future Tools (planned).
- Matches document generator design language — same color tokens (`--dark`, `--cream`, `--rule`), Cormorant Garamond / Jost / Cairo font stack, topbar, and spacing system.
- Document Generator card links directly to `daftra-pdf-generator_1.html` (filename unchanged).
- Social Media Dashboard and Future Tools cards are non-interactive placeholders with status badges.
- Updated `.claude/launch.json`: switched to port 8080 (port 8000 was in use on this machine).

---

## [Previous] — Invoice & Quotation Polish Complete

### Invoice

**Data**
- Invoice number is now a strict pass-through from Daftra (`String(no).trim()`). No prefix, no symbol, no reformatting.
- VAT/CR read from `client_bn1` / `client_bn2` on the invoice object. Falls back to `bn1` / `bn2` from `/clients/{id}.json` if missing.
- VAT amount uses `summary_tax` if non-zero; otherwise falls back to `total − subtotal`.
- Arabic-Indic digits in VAT/CR converted to Western digits at display time only (`toEnDigits()`).

**QR**
- Source: `qr_code_url → ?d64=` param → `atob()` → QRCode.js → canvas → PNG `<img>`.
- CORS-safe: QR URL is never fetched. Payload is extracted and rendered locally.
- Canvas converted to PNG data URL before PDF export for reliable html2canvas capture.
- Verified: preview QR, PDF QR, and Daftra web UI QR all scan to the same value.

**Table**
- Fixed: two-table split (`pp-table` + `pp-table-tail`) — tail table now has `<colgroup>` matching header table column widths (52% / 9% / 20% / 19%).
- Column alignment changed from class-based to `nth-child(2/3/4)` selectors — stable across both tables.
- Removed asymmetric `padding-left: 8px` from `.pp-td-r`.

**Totals**
- Added `padding-right: 8px; box-sizing: border-box` to `.pp-totals` — Total Due amount no longer clips at the right edge.

**Typography**
- Font sizes increased across the board for readability at 100% PDF zoom (range: +1 to +1.5px per element).
- Secondary body text bumped from `font-weight: 300` to `font-weight: 400` (Jost Regular) — improves contrast without changing the colour palette.

**PDF export**
- Removed `min-height: 297mm` — eliminates blank second page on short invoices.
- Removed `width` / `windowWidth` constraints from html2canvas — was causing left-side content clipping.
- Added `scrollY: 0` — eliminates phantom blank page from scroll offset.

---

### Quotation

**Data**
- Fixed: estimate line items were being looked up under `EstimateItem` — Daftra actually stores them under `InvoiceItem` for both document types.
- Quotation number is a strict pass-through from Daftra (`String(no).trim()`). No `QUO-` prefix.
- Valid Until calculated as issue date + 30 days (DD/MM/YYYY). `expiry_date` is always blank on Daftra estimates.

**Layout**
- QR suppressed for quotations — `isInv && qrPayload` condition in both template and `renderQR()`.
- Footer removed from quotations — invoices only.
- 8-column table added with separate `pp-table-quo` / `pp-table-tail-quo` CSS classes.
- Columns 1–2 (Item, Description) left-aligned; columns 3–8 right-aligned via `nth-child(n+3)`.
- Info grid kept strictly 2-column (Prepared For / Prepared By). Valid Until moved to a separate strip above the table.
- Prepared By contact block: `address · website · phone` on 2 lines (not 3) — keeps column heights equal.

**Header**
- `pp-header-quo` class: `1fr 1fr` grid, no centre column, QR wrap hidden.
- Logo retains `translateY(-48px)`. Title block has `margin-right: 20px` inset for visual balance.

**Terms & Conditions**
- Two-column flex layout — English left, Arabic right, separated by a 0.5px rule.
- No `<ul>/<li>` — plain `<div>` elements with `·` to avoid stray RTL bullet rendering.
- Five bilingual bullet points covering validity, payment terms, production start, and VAT inclusion.
- `margin-top: 28px` to sit visually below the totals area.

---

## Quotation 8-Column Field Mapping

| Column | EN | AR | Daftra field |
|---|---|---|---|
| 1 | Item | البند | `it.item` |
| 2 | Description | الوصف | `it.description` |
| 3 | Price | السعر | `it.unit_price` |
| 4 | Qty | الكمية | `it.quantity` |
| 5 | Total Before Tax | قبل الضريبة | `it.item_subtotal` |
| 6 | VAT % | نسبة الضريبة | `it.tax1_percent` |
| 7 | VAT Amount | قيمة الضريبة | `it.tax1_value` |
| 8 | Total With VAT | مع الضريبة | `it.subtotal` |

---

## Solved Issues Log

| Issue | Root cause | Fix |
|---|---|---|
| Line items not showing on invoices | Used `unit_price` — Daftra uses `price` | Changed field lookup order |
| Client VAT/CR showing `—` | Fields are `client_bn1/bn2` on Invoice, `bn1/bn2` on Client | Updated field mapping + client fallback |
| VAT showing SAR 0.00 | `summary_tax` can be 0 even when VAT is in total | Added `total − subtotal` fallback |
| Footer overlapping content | `position: absolute` on footer | Removed; footer now in normal flow |
| Blank second page | `min-height: 297mm` + scroll offset | Removed height; added `scrollY: 0` |
| White right-side gap in PDF | `width: 595` / `windowWidth: 595` constraint | Removed px constraints; use `unit: 'mm'` |
| Content clipped on left in PDF | Same 595px constraint forced wrong viewport | Same fix as above |
| Arabic VAT/CR digits | Daftra returns Arabic-Indic numerals | `toEnDigits()` at display time only |
| Arabic section headings misaligned | `direction: rtl` on label container | Removed; Arabic now left-aligned |
| Summary block splitting across pages | No page-break rule | Added `page-break-inside: avoid` |
| QR not appearing in PDF | `fetch(qr_code_url)` blocked by CORS | Extract d64, decode locally, render via QRCode.js |
| Estimate line items empty | Looked up `EstimateItem` — doesn't exist | Changed to `InvoiceItem` for both types |
| Invoice number double-prefixed | Template was adding `INV#` after stripping prefix | Changed to pure pass-through |
| Totals amount clipping | No right padding on `.pp-totals` | Added `padding-right: 8px` |
| Quotation Valid Until blank | Relied on `expiry_date` (always null) | Calculated as issue date + 30 days |
| RTL bullet dots on wrong side | Used `<ul>` with `direction: rtl` container | Replaced with `<div>` + `·` character |
