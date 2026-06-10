# CLAUDE_CONTEXT — Vista Platform

---

## Platform Workflow Rules — PERMANENT (added 2026-06-07)

These rules apply to every session on every Vista Platform module. Read before making any change.

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
| Push only the active approved branch | Default push target is the approved branch for that module — do not push other branches without explicit instruction |
| `stable-reviewed-history` is the approved branch | This is the Social Media / Notion dashboard approved branch |
| `stable-reviewed-history-v1` is the restore tag | Points to commit `2d0faec` — the approved stable snapshot |
| `feature/financial-dashboard` must not be pushed | Do not push this branch unless the user explicitly approves |

### localStorage — reviewed task history

| Fact | Detail |
|---|---|
| Storage location | Normal browser localStorage only — key `vista_reviews_v1` |
| Incognito windows | Always show empty reviewed history — localStorage is isolated per window type |
| Browser F5 refresh | Does NOT delete reviewed tasks — localStorage persists across refreshes |
| Different browser or device | Will not see reviewed history — localStorage is per browser per machine |
| Clearing browser data | Will delete reviewed history if "Local storage" or "Site data" is included |

---

## Social Media Control Center (`social-dashboard.html`)

### Current Status — Session Save Point 2026-06-05 (Reviewed chip added)

This section is the permanent source of truth for the Social Media Control Center. Before starting any new session on this module, read this section first.

#### What is working
- `proxy.py` running on `localhost:8080` via `python proxy.py`. Relays Notion API calls with token injected from `config.json`. Never exposes tokens in HTML files.
- Dashboard loads all Notion tasks successfully (118 total as of 2026-06-05, paginated). Uses Notion API version `2025-09-03`.
- **Needs My Attention tab** — surfaces Youssef-assigned, Blocked, Pending Feedback, and overdue tasks, sorted by urgency.
- **Task Tracker tab** — all tasks grouped by due-date bucket, collapsible, with color-coded rows and status badges.
- **Media Library tab** — shows only tasks with confirmed media (27 of 89 in current index).
- **Task detail panel** — slide-in from right, shows summary strip, description, live-fetched media section, comments section. Notion tables render inline as styled HTML tables with column/row header support and link-aware cells.
- **Unified detail loading** — all entry points (Media Library, Task Tracker, Needs My Attention, search results) call the same `openDetail(id)` function. No entry-point-specific code paths exist. Verified programmatically 2026-06-05; visual click-through testing still pending during normal dashboard use.
- **Race condition fix** — `_detailTaskId` global tracks which task is currently open. Both `loadDetailMedia` and `loadDetailComments` check `_detailTaskId !== pageId` before every `el.innerHTML` write (7 write points guarded total). Stale fetch results from a previously opened task are silently discarded.
- **Contextual "Open This Task in Notion ↗" button** — appears in the Media & Files section only when the entire section cannot render content: empty page body, text-only tasks, unsupported block types, or fetch errors. Does NOT appear when images, links, tables, or any supported content renders successfully. The general topbar "Open in Notion ↗" link is always available and is the only Notion link shown when content renders.
- **Media index** — manual scan stored in `localStorage` key `vista_media_index_v1`. Scans all 89 Content/Social/Ads & Testing tasks including Done tasks. Index is metadata only — it does not gate or control live media loading in the detail panel.
- **Nested media detection** — fetches one level of child blocks for toggles, callouts, list items, columns. Confirmed: images nested inside `numbered_list_item` are detected.
- **Notion table rendering** — `table` blocks in top-level page content have their child rows fetched separately (one API call per table) and rendered as HTML tables in the detail panel. Column header row → `<thead>`. Row header column → `<th>`. Links in cells rendered as `<a target="_blank">`. Table-row fallback message shown without a Notion button (topbar link is sufficient). Tables nested inside containers (toggles, callouts) are not rendered — Notion does not expose nested table rows in a single children response.
- **On-demand URL fetching** — media URLs are always fetched live when a task is opened. Notion signed URLs expire in ~1 hour and are **never stored in localStorage**.
- **Quick-filter chips** — All, Needs My Attention, Content, Social, Media/Posts, Overdue, **Reviewed**, **Favorites**.
  - **Reviewed chip** — opens Task Tracker view filtered to tasks where `isReviewedByMe(task)` is true (review record exists regardless of staleness). A task stays in Reviewed until manually removed via "Remove Review" — Notion edits and Done status changes do not eject it. Done tasks are always visible in this view regardless of the Include Done toggle. Search, Category, and Assignee filters still apply. Live count `· N` reflects all tasks with a review record. Tasks edited in Notion after being reviewed show an amber `Updated Since Review` badge in the task row and a highlighted note in the detail panel review bar; they may also reappear in Needs My Attention (via `isReviewedAndFresh` → false) but remain in Reviewed. Empty state: "No reviewed tasks match the current filters."
  - **Favorites chip** — opens Task Tracker view filtered to tasks where `isFavorite(task)` is true. A task stays in Favorites until the user manually removes it — Notion edits, Done status changes, and refreshes do not eject it. Done tasks are always visible in this view regardless of the Include Done toggle. Search, Category, and Assignee filters still apply. Live count `· N` reflects all favorited tasks. Star toggle (`☆`/`★`) available on every task row (stops event propagation — does not open the detail panel) and inside the task detail panel as a bar/button. Empty state: "No favorited tasks match the current filters." Storage key: `vista_favorites_v1`.
- **Meeting Agendas / Notes sidebar panel** — collapsible sidebar section. On expand, fetches child pages from the Meetings Agendas or Meeting Notes parent page via `/notion/social/blocks/{id}/children`. Two tabs: Agendas (4 pages, newest first) and Notes (7 pages, newest first). Clicking a page title opens a full-width content viewer in the main area (chips, tabs, and legend are hidden; back button restores them). Block renderer handles: `heading_2`, `heading_3`, `paragraph`, `bulleted_list_item`, `numbered_list_item`, `divider`, `callout`. Rich text supports bold, italic, code, inline links, and auto-linked plain URLs. Child-page lists cached in `_meetingCache` for the session. Graceful fallback if page IDs are blank or a child page returns an error. Uses `MEETING_PROXY = 'social'` (not `PROXY_ROUTE = 'personal'`) — meeting pages are accessible via Hussam's social_media token.
- **Sidebar** — Refresh Tasks button, Search box with live filtering and × clear button, Category + Assignee filters, Include Done toggle, Refresh Media Index button with index status, Meeting Agendas/Notes collapsible panel.
- **Search** — live substring search across task name, description, status, assignee, category, due date, due date formula, and media type labels. Applies to all three tabs (Needs My Attention, Task Tracker, Media Library). Stacks additively with chips and sidebar filters.
- **Search — Completed Matches section** — when a search query is active and `showDone` is off, Done tasks matching the query appear below the active bucket groups under a "Completed Matches · N" header, muted at 65% opacity. Section disappears when the search box is cleared. Tracker badge counts and normal browsing behaviour are unchanged.
- **Related Supporting Tasks section** — always visible in the task detail panel between Media & Files and Comments. Each card shows status, category, assignee, confirmed media types from the index, tier label ("Linked by You", "Explicit Notion Link", "Exact Reference", "Strong Match", or "Possible Match"), an "Open Task →" button, and a "Remove link" button for Tier 5 (manual) cards only. A "+ Link Supporting Task" button is always available to add new manual links. See approved detection signals below.
- **Document Generator navigation** — "← Platform Home" link added to the topbar of `daftra-pdf-generator_1.html`. Links to `index.html`. Never appears in generated PDFs (outside `#pdfPage`).

#### Related Supporting Tasks — APPROVED AND FINAL (2026-06-05)

**Five ranked tiers (strongest to weakest). Max 5 results. Sorted strongest first. `hasMedia` as tie-breaker within the same tier.**

| Tier | Label | Type | Logic |
|---|---|---|---|
| 5 | Linked by You | Manual | User-created link stored in `vista_task_relations_v1`. Bidirectional. |
| 4 | Explicit Notion Link | Automatic | `notionLinksFromBlocks()` — `app.notion.com/p/` URL in task body blocks. |
| 3 | Exact Reference | Automatic | Full task name (lowercase) is a substring of the other task's description property, or vice versa. |
| 2 | Strong Match | Automatic | Same category + shared consecutive-word bigram OR 3+ shared sig words; OR cross-category + shared bigram. |
| 1 | Possible Match | Automatic | Same category + 2+ shared significant words from task names. |

**Stop words excluded from automatic matching (`RELATED_STOP`):**
User-specified: `task, post, content, update, review, prepare, add, send, publish, final, new, vista, united` + standard short/structural words. Minimum word length: 4 chars.

**Card footer:** "Open Task →" for all cards. "Remove link" only on Tier 5 (manual) cards. No "Open in Notion" in the card footer — the topbar link is always available.

**localStorage schema — `vista_task_relations_v1`:**
```json
{ "taskAId": [{ "relatedTaskId": "taskBId", "createdAt": 1748000000000 }] }
```
Both directions stored explicitly. No Notion write permissions required.

**Key functions:**
- `RELATED_STOP` — stop-word set for automatic matching
- `sigWords(text)`, `sigBigrams(text)` — significant word/bigram extraction
- `findRelatedTasks(currentTaskId, topBlocks)` — all 5 tiers, sorted, capped at 5
- `_lastTopBlocks` — cached topBlocks for re-render without re-fetch
- `notionLinksFromBlocks(blocks)` — Tier 4 signal
- `loadRelations()`, `saveRelation(a,b)`, `removeRelation(a,b)`, `getManualRelatedIds(taskId)` — Tier 5 store
- `buildRelatedTasksHTML(related, currentTaskId)` — renders cards with tier label + Remove Link for Tier 5
- `_refreshRelatedSectionFull(taskId)` — re-renders section using `_lastTopBlocks` after add/remove
- `openTaskSelector(currentTaskId)`, `renderTaskSelectorList(query)`, `selectRelatedTask(id)`, `closeTaskSelector()` — manual-link modal

**Previously removed and must not be restored:**
`sigTaskWords()`, `findRelatedByDescription()` (the old fuzzy/description-only version — different from current `sigWords`/`sigBigrams`).

**Validation confirmed (2026-06-05):**
- False positive eliminated: "Research tote bag demand in KSA" scores 0 against "Ad keyword research (not final)" (different categories, only 1 shared word — below threshold).
- "Tag keywords as positive or negative" ↔ "Ad keyword research (not final)": requires manual link (plural "keywords" ≠ singular "keyword"; no shared bigrams — confirmed correct, not a defect).
- Same-category tasks sharing 3+ significant words correctly surface as Strong Match.

#### What is NOT implemented yet
| Feature | Status | Notes |
|---|---|---|
| Comments | ❌ Blocked | Live permission test performed 2026-06-05: all tasks return `403 restricted_resource`. Integration "Youssef" (bot, Saura Agency workspace) does not have Read Comments active. Hussam must enable it on the correct integration. Detail panel shows clear error message. |
| Mark Reviewed | ✅ Done (Phase 2A) | localStorage only. `vista_reviews_v1`. Staleness auto-reset via `last_edited_time` comparison. |
| Reviewed quick-filter chip | ✅ Done | Chip beside Overdue. Filters Task Tracker to `isReviewedByMe` tasks (permanent — only removed by manual Remove Review). Done tasks always visible in this view. Stale tasks show amber `Updated Since Review` badge. Search, Category, Assignee apply; Include Done does not hide reviewed tasks. |
| Related Supporting Tasks | ✅ Done (Phase 2A.5) | 5-tier detection: Tiers 1–3 automatic (Possible/Strong Match, Exact Reference), Tier 4 Explicit Notion Link, Tier 5 manual "Linked by You". Max 5 results. `RELATED_STOP` + `sigWords`/`sigBigrams` threshold prevents false positives. See "APPROVED AND FINAL" section above. |
| Favorites | ✅ Done (Phase 2A.6) | localStorage only. `vista_favorites_v1`. `isFavorite(t)`, `addFavorite`, `removeFavorite`, `toggleFavoriteAction`. Star toggle (☆/★) on every task row + in detail panel. Favorites chip beside Reviewed. Done tasks always visible. No staleness concept. |
| Meeting Agendas sidebar | ✅ Done (full) | Collapsible sidebar panel. Two tabs: Agendas (4 pages) and Notes (7 pages), newest first. Click page → full-width viewer replaces main content. Block renderer: heading_2/3, paragraph, ul/ol, divider, callout. Rich text: bold, italic, code, links, auto-URL. `MEETING_PROXY = 'social'`. Page IDs in `config.json` as `meeting_agenda_page_id` / `meeting_notes_page_id`. Graceful fallback for inaccessible pages. |
| Create new task | ✅ Done (2026-06-10) | `+ New Task` sidebar button → modal form → `POST /notion/social/pages`. See "Create New Task" section below. |
| Archive (delete) task | ✅ Done (2026-06-10) | "Archive this task…" button in detail panel → confirmation → `PATCH /notion/social/pages/{id}` with `{archived:true}`. Notion archive only — no hard delete. localStorage records not auto-cleaned. See "Archive Task" section below. |
| Notion write-back | ❌ Not started | Phase 2D — requires write permission + Hussam adding a `Youssef Reviewed` checkbox to his database. |
| Google Drive saving | ❌ Not started | Phase 4 — scheduled after Personal Task Center. |
| Personal Task Center | ✅ Done (2026-06-10) | Phase 3 — `personal-dashboard.html`. Standalone dashboard, `/notion/personal/` route. See "Personal Task Center" section in CLAUDE_CONTEXT. |

#### Create New Task — LIVE ✅ (2026-06-10)

Hussam granted write permission on the Vista social_media integration. New tasks can be created in the shared database directly from the dashboard.

**Proxy route:** `POST /notion/social/pages` — forwarded by the existing `_proxy_notion()` handler. No proxy.py changes were required.

**Writable properties (all others are read-only or computed):**
| Property | Type | Required |
|---|---|---|
| `Task name` | title | ✅ Yes |
| `Status` | status | Optional (defaults to "Not started") |
| `Category` | select | Optional |
| `Assignee` | select | Optional |
| `Due date` | date | Optional |
| `Description` | rich_text | Optional |

**Omit on create (read-only):** `Due Date 2` (formula), `Bucket` (formula), `Updated at` (last_edited_time).

**Functions added:**
- `openNewTaskModal()` — resets form fields, shows `#nt-overlay`, focuses task name
- `closeNewTaskModal()` — hides `#nt-overlay`
- `submitNewTask()` — validates name, builds Notion body (omits blank optional fields), POSTs, handles success/error inline

**Submit flow:**
1. Button disables + shows "Creating…" on first click (duplicate-submit guard)
2. Success → green bar, "Created" label, modal closes after 900 ms, `loadAllTasks()` fires
3. Notion error → error surfaced inline, button re-enables for retry
4. Network error → same inline path

**Escape key (side effect fix):** The global `keydown` Escape handler was previously unconditional (`closeDetail()` always). Replaced with a prioritised chain: new task modal → task-selector modal → detail panel. Each fires only if its layer is the topmost visible one.

**Locked rules (do not change):**
- Never send `Due Date 2`, `Bucket`, or `Updated at` in the create body — they are computed/system fields.
- Optional properties (`Category`, `Assignee`, `Due date`, `Description`) must be omitted entirely (not sent as null/empty) when blank — Notion returns 400 for properties sent with empty values when the field doesn't accept them.
- The submit button must remain disabled after a successful create (re-enable only on error).
- On success, always call `loadAllTasks()` so the new task appears in the Task Tracker without a manual refresh.

#### Archive Task — LIVE ✅ (2026-06-10)

**Proxy route:** `PATCH /notion/social/pages/{page_id}` with body `{"archived": true}`. Forwarded by `_proxy_notion()`. `do_PATCH` updated in `proxy.py` — Notion routes pass through, `/daftra/...` stays blocked (GET-only), unknown routes return 405.

**UI:** "Archive this task…" button at the bottom of the detail panel. Clicking reveals an inline confirmation box with the task name and warning text. Two buttons: "Yes, archive it" (red, disables on click) and "Cancel" (restores button). Error message shown inline if Notion returns an error.

**Behaviour:**
- Archive = Notion `archived: true` only. No hard delete. Page is recoverable from Notion Trash.
- On success: `closeDetail()` fires, then `loadAllTasks()` refreshes — archived task disappears from all views.
- On error: Notion's error message shown inline; button re-enables for retry.
- No bulk archive. Only the currently-open task can be archived.

**localStorage:** Records in `vista_reviews_v1`, `vista_favorites_v1`, `vista_task_relations_v1` for the archived task are **not** cleaned automatically. Orphaned records are inert (task no longer appears in `allTasks`). No visible UI issues observed from orphaned records — task simply stops loading.

**proxy.py change — `do_PATCH` (approved minimal change):**
```python
def do_PATCH(self):
    if self.path.startswith('/daftra/'):
        self._block_daftra_write()      # Daftra stays GET-only
    else:
        route = self._notion_route()
        if route:
            self._proxy_notion(*route)  # /notion/social/ and /notion/personal/ allowed
        else:
            self._block_daftra_write()  # unknown routes blocked
```

**proxy.py — `_json_error` fix:** Added `Content-Length` header to `_json_error()`. Without it, Python's HTTP/1.0 response handling caused PATCH error responses to close the connection before the client could read the status code. This is a correctness fix that applies to all error responses, not just PATCH.

**Functions added:**
- `showArchiveConfirm(id)` — hides button, shows confirmation box
- `cancelArchive()` — hides confirmation box, restores button
- `confirmArchive(id)` — sends `PATCH /notion/social/pages/{id}`, handles success/error

**Locked rules:**
- Never send `archived: false` to unarchive from this dashboard — no unarchive UI exists.
- Never attempt hard delete — Notion public API does not expose permanent deletion.
- Do not auto-clean localStorage records on archive without explicit approval.

#### Personal Task Center — LIVE ✅ (2026-06-10)

**File:** `personal-dashboard.html` (standalone — no shared logic with `social-dashboard.html`)
**Proxy route:** `/notion/personal/` only. Uses `notion.personal` token from `config.json`.
**Data source ID:** `3624a590-47e4-80de-85ca-000bf4745dcd` ("To Do List DB")

**Schema (3 properties only):**
| Property | Type | Notes |
|---|---|---|
| `Name` | `title` | Required — task name |
| `Done` | `checkbox` | true/false |
| `Due Date` | `date` | Optional. Supports datetime+timezone (syncs to Google Calendar) or date-only |

**Operations:**
- List: `POST /notion/personal/data_sources/{DATA_SOURCE_ID}/query` with `{ page_size: 100 }`
- Create: `POST /notion/personal/pages` — `parent: { type: 'data_source_id', data_source_id: '...' }`
- Toggle Done: `PATCH /notion/personal/pages/{id}` — `{ properties: { Done: { checkbox: bool } } }`
- Archive: `PATCH /notion/personal/pages/{id}` — `{ archived: true }`

**UI:**
- Topbar: "Personal Tasks" tool name, links to social-dashboard.html and index.html (same tab).
- Filter tabs: All / Active / Done.
- Task rows: checkbox (inline toggle), name, due date with time if present, archive button per row.
- Overdue tasks (date in past, not Done): red date + "Overdue" tag.
- Done tasks: name struck through, date greyed out.
- Sort: active first (overdue → by date → undated), done last.
- "New Task" button → inline form (name + datetime-local picker). Enter key submits, Escape closes.
- Archive confirmation: expands inline below the row (no modal). Soft-delete only.
- No detail panel — schema is too simple to need one.

**Navigation added:**
- `index.html`: Card 4 linking to `personal-dashboard.html` (same tab, `<a href>`).
- `social-dashboard.html` topbar: "Personal Tasks" link beside "← Platform Home".

**Locked rules:**
- Never mix personal tasks with Vista/social tasks.
- Never call `/notion/social/` from `personal-dashboard.html`.
- No Google Calendar API or OAuth. Calendar sync happens automatically through Notion's integration.
- No hard delete. Archive only.

#### Validation status (2026-06-05)
- Programmatic validation passed: all 3 `openDetail` call-sites confirmed, all 7 `el.innerHTML` write points confirmed guarded, block fetch returning correct data for test task (372a2557), `renderMediaBlocks` returning `html` path (no fallback button) for task with confirmed content.
- **Visual click-through testing still pending.** Should be performed during normal dashboard use: open same task from Media Library, Task Tracker, search results, and (if applicable) Needs My Attention; confirm identical media rendering and no stale overwrites when switching tasks quickly.

#### Proxy and data source
```
Command to run:   python proxy.py
URL:              http://localhost:8080/social-dashboard.html
Tasks route:      /notion/personal/...  → api.notion.com/v1/... (Youssef's integration)
Meetings route:   /notion/social/...    → api.notion.com/v1/... (Hussam's integration)
Data source ID:   35aa2557-c7f8-8140-81f5-000b067a0139
Meeting Agendas:  35ba2557-c7f8-81e9-8f53-ed3da0df85c3  (parent page ID)
Meeting Notes:    363a2557-c7f8-81a1-bf70-e59d23f14b87  (parent page ID)
API version:      2025-09-03
Token source:     config.json → notion.personal.token + notion.social_media.token (git-ignored)
```

#### localStorage keys in use
| Key | Purpose |
|---|---|
| `vista_media_index_v1` | Media index metadata. Never stores signed URLs. |
| `vista_reviews_v1` | Mark Reviewed records (Phase 2A). Stores reviewedAt, taskName, lastEditedTime. |
| `vista_task_relations_v1` | Manual Related Supporting Task links (Phase 2A.5). Bidirectional. |
| `vista_favorites_v1` | Favorites records (Phase 2A.6). Stores favoritedAt, taskName. No staleness concept. |

#### Media index counts (2026-06-04 scan)
- 89 tasks scanned (Content + Social + Ads & Testing, all statuses)
- 27 tasks with confirmed media
- Media types: 14 image · 8 link · 5 video · 4 embed · 3 file
- 23 of 27 are Done tasks — confirmed that scanning includes Done

---

### Phase 2A: Mark Reviewed by Me — IMPLEMENTED ✅ (2026-06-04)

**Approach:** Local `localStorage` only. No Notion write permission required. No changes to Hussam's database.

#### Storage schema — `localStorage` key `vista_reviews_v1`
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

**Critical field: `lastEditedTime`**
When a task is marked reviewed, store its current `last_edited_time` from the Notion task properties (`t.properties['Updated at'].last_edited_time`). On every task load, compare the stored `lastEditedTime` to the current value from Notion. If the task has been edited after it was reviewed, automatically treat it as unreviewed and return it to Needs My Attention.

This means: if Hussam updates a task after you've reviewed it, it reappears automatically — you don't have to remember to re-check it.

#### Behaviour rules
1. **Mark Reviewed button** — shown in task detail panel (small outline button). Writes to `vista_reviews_v1` with current `last_edited_time`.
2. **Needs My Attention removal** — a reviewed-and-fresh task is **fully suppressed from Needs My Attention regardless of its status.** Blocked, overdue, and Pending Feedback indicators do NOT keep it in the attention queue. The review means "I have seen this and I am done with it until Hussam changes it."
3. **Staleness check (`isReviewedStale`)** — if `Notion.last_edited_time > stored lastEditedTime`, the task shows an amber **Updated Since Review** badge and reappears in Needs My Attention. It does NOT leave the Reviewed chip view — only Remove Review removes it.
4. **Reviewed chip (permanent history)** — uses `isReviewedByMe(t)` (review record exists). Done tasks and stale tasks remain visible. A task leaves Reviewed only when the user manually clicks Remove Review.
5. **Task Tracker visibility** — reviewed tasks stay fully visible in the tracker with all their normal indicators (overdue, Blocked, Pending Feedback) intact. Green `✓ Reviewed` badge shown for all reviewed tasks. Amber `Updated Since Review` badge shown additionally when stale.
6. **Detail panel** — shows "Reviewed by You · {date}" bar for all reviewed tasks. When stale, bar turns amber with "Updated since review" note. Remove Review always available.
7. **Undo** — a 10-second undo link appears after marking reviewed. Clicking it removes the localStorage record immediately.
8. **Remove review** — a "Remove review" link in the detail panel for any reviewed task. Removes the record instantly; task reappears in Needs My Attention if applicable and leaves the Reviewed chip view.
9. **No Notion API calls** — zero new proxy routes, zero new permissions.

#### Functions implemented
- `REVIEWS_KEY = 'vista_reviews_v1'`
- `loadReviews()`, `saveReview(id, name, lastEditedTime)`, `removeReview(id)`
- `isReviewedAndFresh(t)` — true if reviewed AND `updated(t) <= rec.lastEditedTime`. Used only by `attentionFilter`.
- `isReviewedByMe(t)` — true if any review record exists. Used by Reviewed chip filter, chip count, task row badge, `reviewSectionHTML`.
- `isReviewedStale(t)` — true if reviewed AND `updated(t) > rec.lastEditedTime`. Used by "Updated Since Review" badge in task row and detail panel.
- `attentionFilter()`: `isReviewedAndFresh(t)` suppresses the task from the entire attention queue
- `taskRow()`: green `✓ Reviewed` badge for all reviewed tasks; amber `Updated Since Review` badge when stale
- `buildDetailHTML()`: Mark Reviewed button or review bar (amber when stale)

#### Favorites functions (Phase 2A.6)
- `FAVORITES_KEY = 'vista_favorites_v1'`
- `loadFavorites()`, `saveFavorites(data)`
- `isFavorite(t)` — true if task ID exists in `vista_favorites_v1`. No staleness concept.
- `addFavorite(id, name)`, `removeFavorite(id)` — write/delete localStorage records
- `toggleFavoriteAction(id)` — toggles favorite state; re-renders `detail-favorite-section` if task is open; calls `applyFilters()` to update row badges and chip count
- `favoriteSectionHTML(t)` — returns gold `★ Favorited · date` bar with "Remove" link, or `☆ Add to Favorites` button
- `taskRow()`: gold `★ Favorite` badge when favorited; `☆`/`★` inline toggle (`stopPropagation` — does not open detail panel)
- Done gate in `getFilteredTasks()`: exempted for `activeChip === 'favorites'` (same as Reviewed)

#### Meeting Agendas / Notes functions (2026-06-08)
- `MEETING_AGENDA_PAGE_ID` = `'35ba2557-c7f8-81e9-8f53-ed3da0df85c3'` (hardcoded constant, same pattern as `DATA_SOURCE_ID`)
- `MEETING_NOTES_PAGE_ID`  = `'363a2557-c7f8-81a1-bf70-e59d23f14b87'`
- `MEETING_PROXY = 'social'` — meeting pages are in Hussam's workspace; use the social_media token, NOT the personal (task) token
- `_meetingTab` state: `'agendas'` | `'notes'`
- `_meetingCache` — `{ agendas: null, notes: null }` — caches fetched page lists for the session (re-fetched after proxy restart)
- `toggleMeetingAgendasPanel()` — open/close sidebar panel; triggers `loadMeetingList` on first expand
- `switchMeetingTab(tab)` — switches Agendas ↔ Notes tab, loads list
- `loadMeetingList(tab)` — fetches `/notion/social/blocks/{parentId}/children`, filters `child_page` blocks, reverses to newest-first, caches, calls `_renderMeetingItems`
- `_setMeetingListHTML(html)` / `_renderMeetingItems(pages)` — sidebar list render helpers
- `openMeetingPage(id, title)` — hides chips/tabs/legend/task panels; shows `panelMeeting`; fetches block content via `/notion/social/blocks/{id}/children?page_size=100`
- `closeMeetingViewer()` — hides `panelMeeting`, restores chips/tabs, calls `switchTab(currentTab)` to restore legend and active panel
- `renderRichText(rtArr)` — converts Notion rich_text array to HTML: bold, italic, code, href links, auto-linked plain URLs (regex)
- `renderNotionBlocks(blocks)` — converts Notion blocks array to HTML. Supported: `heading_2` → `<h2 class="nb-h2">`, `heading_3` → `<h3 class="nb-h3">`, `paragraph` → `<p class="nb-p">`, `bulleted_list_item` → `<ul class="nb-ul">` (consecutive grouped), `numbered_list_item` → `<ol class="nb-ol">` (consecutive grouped), `divider` → `<hr class="nb-divider">`, `callout` → `<div class="nb-callout">` with emoji icon. Unknown block types silently skipped.
- **Legend restore bug note:** `openMeetingPage` uses `legendStrip.classList.add('legend-hidden')`. `closeMeetingViewer` calls `switchTab(currentTab)` which re-applies `legend-hidden` toggle correctly for media tab. Do NOT use `legendStrip.style.display` for meeting viewer — will prevent switchTab from restoring it.

---

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

**Estimate item key:** Daftra returns estimate line items under `data.Estimate.InvoiceItem` (NOT `EstimateItem`). The `itemsKey` must be `'InvoiceItem'` for both invoices and estimates.

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

## QR Implementation (LOCKED)

### Status — STABLE ✅
Verified by scanning multiple invoices. Preview QR, PDF QR, and Daftra QR all return identical payload.

### Source
```
Invoice.qr_code_url  →  ?d64=<base64>
```

### Process (do not change order or any step)
```js
// 1. Extract d64 parameter from Daftra URL
const d64 = new URL(invoice.qr_code_url).searchParams.get('d64') || '';

// 2. Decode d64 — required; raw d64 produces wrong scan result
const qrPayload = d64 ? atob(d64) : '';
doc._qrPayload = qrPayload;

// 3. Render decoded payload with QRCode.js (96×96, CorrectLevel.M)
//    QRCode.js draws a <canvas> inside #pp-qr-target

// 4. Convert canvas → PNG data URL (inside setTimeout(0) after QRCode.js finishes)
const img = document.createElement('img');
img.src = canvas.toDataURL('image/png');

// 5. Replace canvas with <img src="data:image/png;...">
//    html2canvas reliably captures data URL <img>; live canvas is unreliable in PDF export

// 6. PDF export uses the <img> — QR appears correctly in downloaded PDF
```

### Library
`qrcodejs` v1.0.0 — `https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js`

### Placement — 3-column CSS grid
```css
.pp-header      { display: grid; grid-template-columns: 1fr auto 1fr; align-items: start; column-gap: 28px; }
.pp-logo-wrap   { justify-self: start;  align-self: start; margin-top: 0; }
.pp-qr-wrap     { justify-self: center; align-self: start; }
.pp-title-block { justify-self: end;    align-self: start; text-align: right; }
#pp-qr-target   { width: 96px; height: 96px; background: #fff; }
.pp-qr-img      { width: 96px; height: 96px; display: block; object-fit: contain; }
```

### Rules — NEVER change
- **Never fetch `qr_code_url` image** — CORS blocks it on `file://` and `localhost`
- **Never use `imageUrlToDataUrl()`** for QR — retired, CORS-blocked
- **Never pass raw `d64` to QRCode.js** — must `atob()` decode first
- **Never generate QR from invoice fields**
- **Never construct ZATCA TLV manually**
- **Always convert canvas → PNG data URL → `<img>`** before PDF export

### Regression Test
For any future QR-related change:
1. Open invoice
2. Scan Daftra QR (from Daftra web UI)
3. Scan preview QR (in the browser tool)
4. Scan PDF QR (from downloaded PDF)

**All three values must be identical. If not, reject the change before committing.**

---

## Current Final Polish Tasks

### 1. Invoice Number Formatting
**Status: DONE ✅**

Pass-through only — display the Daftra invoice number exactly as received, no reformatting.

```js
const prefix = String(no).trim(); // pass-through for both invoices and quotations
```

**Previous broken logic (do not restore):**
```js
// WRONG — strips and reformats, adding INV# prefix
const cleanNo = isInv ? String(no).replace(/^INV[#-]?/i, '') : String(no);
const prefix  = isInv ? `INV#${cleanNo}` : `QUO-${no}`;
```

Validated:
```
Daftra invoice number: INV000021
PDF invoice number:    INV000021
Status: PASS
```

Quotation numbers are also pass-through — `String(no).trim()` for both document types. Do not prepend `QUO-`.

---

### 2. Numeric Column Alignment
**Status: DONE ✅**

Root cause: two separate tables (`pp-table` with `<thead>` and `pp-table-tail` without one). The tail table had no column widths set, so its columns floated to auto-width and values appeared under wrong headers.

Fixes applied:
- Added `<colgroup>` to the tail table template (52% / 9% / 20% / 19%) to match header table widths.
- Replaced class-based alignment with `nth-child(2/3/4)` selectors on both `.pp-table` and `.pp-table-tail` — targeting cells by position, not class name.
- Removed `padding-left: 8px` from `.pp-td-r` (was asymmetric; not needed for alignment).

### 3. Totals Clipping
**Status: DONE ✅**

Added `padding-right: 8px; box-sizing: border-box` to `.pp-totals`. Total Due amount now has 14px gap from the right edge — no longer clipping.

### 4. Readability Improvement at 100% PDF Zoom
**Status: DONE ✅**

Conservative font-size pass — no structural changes, layout unchanged.

| Element | Before | After |
|---|---|---|
| `pp-table th` (header) | 6px | 7px |
| `.th-ar` (Arabic header) | 6px | 7px |
| `pp-item-name` | 7.5px | 8.5px |
| `pp-item-desc` | 6.5px | 7.5px |
| `pp-td-r` (cell values) | 7.5px | 8.5px |
| `pp-info-body` | 7px | 8px |
| `pp-heading-en` | 7px | 7.5px |
| `pp-reg-lbl-en/ar` | 6px | 7px |
| `pp-reg-val` | 7.5px | 8.5px |
| `pp-doc-meta` | 6.5px | 7.5px |
| `pp-t-row-en` | 7px | 8px |
| `pp-t-row-ar` | 6px | 7px |
| `pp-t-row-amt` | 7.5px | 8.5px |
| `pp-t-final .val` | 10px | 11px |
| `pp-terms` | 6.5px | 7.5px |

Validated: all column alignments, QR, logo, VAT/CR, totals visibility, invoice number — no regressions.

### 5. Secondary Text Contrast Refinement
**Status: DONE ✅**

Issue: description text and secondary info felt pale at 100% PDF zoom due to Jost Light (300) weight combined with muted gray `#6B6B67` color.

Fix: bumped `font-weight: 300 → 400` (Jost Regular) on readable body elements only. No color or size changes — the color palette is fully preserved.

| Element | Role | Change |
|---|---|---|
| `pp-item-desc` | Item description lines | 300 → 400 |
| `pp-info-body` | Address/contact in Issued To/By | 300 → 400 |
| `pp-td-r` | Numeric cell values | 300 → 400 |
| `pp-t-row-en` | Subtotal/VAT labels in totals | 300 → 400 |

Left at 300 (intentionally decorative): `pp-t-row-ar`, `pp-doc-meta`, `pp-terms`, `th-ar`.

---

### 6. Quotation Section
**Status: DONE ✅**

#### Bug fixed — items not loading
Daftra returns estimate line items under `data.Estimate.InvoiceItem` (same key as invoices), not `EstimateItem`. Changed `itemsKey` to `'InvoiceItem'` unconditionally.

#### QR removed from quotations
`isInv && qrPayload` condition added in both the HTML template (`#pp-qr-target` div) and the `renderQR()` call. Quotation PDFs never render a QR. Invoice QR unchanged.

#### Quotation 8-column table
Quotation uses a separate `renderQuoRow` function and a `pp-table-quo` / `pp-table-tail-quo` CSS class for independent column alignment.

**Column → Daftra field mapping (confirmed from live API):**
| Column | EN Label | AR Label | Field |
|---|---|---|---|
| 1 | Item | البند | `it.item` |
| 2 | Description | الوصف | `it.description` |
| 3 | Price | السعر | `it.unit_price` |
| 4 | Qty | الكمية | `it.quantity` |
| 5 | Total Before Tax | قبل الضريبة | `it.item_subtotal` |
| 6 | VAT % | نسبة الضريبة | `it.tax1_percent` |
| 7 | VAT Amount | قيمة الضريبة | `it.tax1_value` |
| 8 | Total With VAT | مع الضريبة | `it.subtotal` |

**Alignment rule:** cols 1–2 left, cols 3–8 right (nth-child selectors on `pp-table-quo`).

#### Quotation number rule
Pass-through only — same as invoices. Daftra returns bare numbers (e.g. `000021`). Do not prepend `QUO-`. `prefix = String(no).trim()` for both document types.

#### Header alignment
Quotation header uses `pp-header-quo` class:
- `grid-template-columns: 1fr 1fr; column-gap: 0` — two equal columns, no empty QR centre
- `.pp-qr-wrap { display: none }` — QR hidden for quotations
- Logo retains `translateY(-48px)` — same as invoices; do not override this for quotations
- `.pp-header-quo .pp-title-block { margin-right: 20px }` — title inset 20px from right content edge; compensates visually for the logo's upward-floating weight so the header feels balanced rather than the title sitting hard against the right margin

Invoice header unchanged (`1fr auto 1fr`, QR in centre, `translateY(-48px)`).

#### Valid Until date
`doc.expiry_date` is always blank on Daftra estimates. Calculated as `doc.date + 30 days` using DD/MM/YYYY parsing. Do not rely on `expiry_date`.

```js
const validUntil = (() => {
  if (isInv) return '';
  if (doc.expiry_date) return doc.expiry_date;
  const parts = (date || '').split('/');
  const d = new Date(+parts[2], +parts[1]-1, +parts[0]);
  d.setDate(d.getDate() + 30);
  return `${String(d.getDate()).padStart(2,'0')}/${String(d.getMonth()+1).padStart(2,'0')}/${d.getFullYear()}`;
})();
```

#### Terms & Conditions layout
Two-column flex layout — EN left, AR right, separated by a `0.5px` rule. No `<ul>/<li>` — uses `<div>` with `&middot;` prefix to avoid stray RTL bullet rendering. Arabic column uses `direction:rtl; text-align:right` with `·` appended at line end.

#### Footer
Quotation PDFs have no footer. `${isInv ? '<div class="pp-footer">...</div>' : ''}`. Invoice footer unchanged.

#### Valid Until strip
Replaces the old `thirdCol` third grid column. Rendered as a standalone `display:flex` bar between the info grid and the line items table. Keeps the info grid strictly 2-column (Prepared For / Prepared By) for clean alignment.

#### Terms & Conditions (quotation only)
Five bullet points — EN + AR:
1. Quotation valid for 30 days.
2. 50% advance payment required upon approval.
3. Remaining balance due before delivery.
4. Production starts only after payment and final written approval or PO.
5. All prices include VAT at 15% unless otherwise stated.

**Quotation validation checklist:**
- [ ] Quotation number matches Daftra exactly (no `QUO-` prefix)
- [ ] QR absent from quotation PDF
- [ ] Invoice QR still present
- [ ] All 8 columns populated from correct fields
- [ ] Cols 3–8 right-aligned, cols 1–2 left-aligned
- [ ] Info grid strictly 2-column (Prepared For / Prepared By)
- [ ] Valid Until strip visible above table
- [ ] Footer absent from quotation
- [ ] Terms & Conditions with 5 bullet points visible
- [ ] Totals (subtotal, VAT, total) visible
- [ ] VAT/CR present
- [ ] No regressions on invoice PDF

---

### 3. Locked — Do Not Touch
These items are stable and must not be modified during polish work:

| Item | Reason |
|------|--------|
| QR implementation | Locked — verified by scan. See QR section. |
| QR payload logic | `d64` → `atob()` → QRCode.js → canvas → PNG img |
| VAT/CR mapping | `client_bn1`/`bn1`, `client_bn2`/`bn2` |
| Invoice calculations | subtotal, VAT fallback, total |
| Footer behavior | Single instance, natural flow, no VAT/CR |
| Logo alignment | `translateY(-48px)` optical correction |
| Pagination behavior | `pp-invoice-tail` keeps last 2 rows + totals together |
| `downloadPDF()` logic | html2pdf options, chain order, `waitForImages` |

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
| QR not appearing in PDF | `fetch(qr_code_url)` blocked by CORS on both `file://` and `localhost`. Fix: extract `d64` param from URL, decode with `atob()`, render as inline `<canvas>` via QRCode.js. html2canvas captures inline canvas natively. Scan-verified identical to Daftra QR. |

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

---

## Invoice System Regression Prevention Update

### 1. MD File Usage Rule

This MD file is the permanent source of truth for the invoice automation project.

Before making any code change, Claude must:
1. Read this MD file.
2. Follow all existing rules.
3. Preserve all previously fixed behavior.
4. Avoid quick local patches that may break other invoice sections.

After fixing any bug, Claude must update this MD file with:
1. The issue discovered.
2. The root cause.
3. The permanent prevention rule.
4. A validation checklist item.

A fix is not considered complete unless:
- Code is updated.
- MD file is updated.
- A fresh invoice PDF is generated.
- The full validation checklist passes.

---

### 2. Regression Prevention Rule

Never fix one invoice issue by breaking another already-fixed issue.
Any invoice layout or data change must preserve:

- Logo alignment.
- Header spacing.
- Invoice number accuracy.
- QR code visibility and readability.
- Item table alignment.
- Quantity alignment.
- Unit price alignment.
- Line total alignment.
- Totals section visibility.
- VAT calculation visibility.
- Grand total visibility.
- Page margin safety.
- Arabic and English text rendering.
- No overlapping text.
- No hidden or clipped totals.

Before delivery, Claude must perform a full visual and data validation pass.

---

### 3. Invoice Number Rule

The invoice number displayed in the generated PDF must exactly match the invoice number received from Daftra.

Example — Daftra value:
```
INV000021
```

PDF output must be:
```
INV000021
```

Do not add `INV#`. Do not output `INV#000021`, `INVINV000021`, `INV #000021`, or `INV-000021`.

The PDF must show the same invoice number as Daftra, with no added prefix, symbol, spacing, or formatting change.

---

### 4. Invoice Number Implementation Rule

Claude must inspect the current code and remove any logic that manually adds an invoice prefix.

Invalid logic examples:
```js
"INV" + invoice_number
"INV#" + invoice_number
`INV${invoice_number}`
`INV#${invoice_number}`
```

Correct logic — keep Daftra invoice number exactly as received:
```js
function formatInvoiceNumber(daftraInvoiceNumber) {
  if (!daftraInvoiceNumber) return '';
  return String(daftraInvoiceNumber).trim();
}
```

The invoice template must display only `formatInvoiceNumber(invoice_number)`.
It must not add `INV`, `INV#`, `#`, spaces, or any other formatting in the HTML/template layer.

---

### 5. Invoice Number Validation Checklist

Before delivering any generated invoice PDF, Claude must verify:

- [ ] Raw Daftra invoice number is identified.
- [ ] Rendered PDF invoice number is checked.
- [ ] PDF invoice number exactly equals Daftra invoice number.
- [ ] No duplicated `INV`.
- [ ] No added `#`.
- [ ] No added spaces.
- [ ] No missing digits.
- [ ] No formatting transformation applied.

Example validation:
```
Raw Daftra invoice number: INV000021
Rendered PDF invoice number: INV000021
Status: PASS
```

---

### 6. Table Alignment Rule

The invoice item table must keep stable column alignment across all invoices.

Required alignment:
- Item description: left-aligned.
- Quantity: right-aligned (consistent inside its column).
- Unit price: right-aligned.
- Line total: right-aligned.

Quantity, unit price, and total must never visually shift into neighboring columns.
Changing logo, header, totals, QR, or invoice number logic must not affect table alignment.

---

### 7. Totals Section Visibility Rule

The totals section must always be fully visible inside the page boundaries.
The following must never be hidden, clipped, or pushed outside the PDF page:

- Subtotal.
- Discount, if present.
- VAT amount.
- Grand total.
- Payment amount, if present.
- Balance due, if present.

Before delivery, Claude must visually confirm that the totals section appears fully and clearly.

---

### 8. QR Code Rule

The QR code must remain:
- Visible.
- Not stretched.
- Not cropped.
- Not overlapping other elements.
- Readable after PDF generation.

Any layout change must preserve QR code readability.

---

### 9. Final PDF Validation Checklist

Before delivering any invoice PDF, Claude must validate the final rendered PDF — not only the HTML or code.

```
[ ] Logo aligned correctly
[ ] Header layout stable
[ ] Invoice number exactly matches Daftra
[ ] No duplicated invoice prefix
[ ] No added # symbol
[ ] QR code visible and readable
[ ] Item description aligned
[ ] Quantity aligned
[ ] Unit price aligned
[ ] Line total aligned
[ ] Subtotal visible
[ ] VAT visible
[ ] Grand total visible
[ ] Nothing clipped
[ ] Nothing overlapping
[ ] Page margins safe
[ ] Arabic text renders correctly
[ ] English text renders correctly
```

Claude must not mark the task complete until all items pass.

---

### 10. Root Cause Discipline

Claude must not only patch the visible symptom.
For every bug, Claude must identify:

```
Issue:
Root cause:
Code area affected:
Fix applied:
MD rule added:
Validation performed:
```

This prevents repeated fixes of the same issue across different sessions.

---

### 11. Current Known Issue — Invoice Number Formatting

**Issue:** Invoice number was displayed incorrectly because the template was adding extra formatting (`INV#` prefix + stripping the existing prefix).

**Root cause:** `renderPreview()` applied `String(no).replace(/^INV[#-]?/i, '')` then prepended `INV#`, transforming `INV000021` → `INV#000021` instead of leaving it as-is.

**Correct behavior:** The PDF invoice number must exactly match Daftra.
```
Daftra: INV000021
PDF:    INV000021
```

**Permanent rule:** Never add `INV`, `INV#`, `#`, spaces, or symbols to the invoice number. Daftra already provides the full invoice number — pass it through unchanged.

**Resolution:** Reverted. `renderPreview()` now uses pass-through logic. See Section 4 above and the Invoice Number Formatting entry in Current Final Polish Tasks.

---

---

## Purchasing Invoice Local File Manager — LIVE ✅ (commit `d0188c6`)

This section is the permanent source of truth for the Purchasing Invoice manager. Read before touching any purchasing-invoice code.

### Scope lock
All purchasing invoice code is strictly isolated. Never touch: `printAllInvoices()`, Daftra invoice/quotation/DN rendering, QR pipeline, html2pdf chain, `config.json`.

### Local folder
```
PURCHASE_INVOICE_DIR = C:\Users\YousefMokaled\Documents\Vista United Co\purchasing invoices
PURCHASE_ALLOWED_EXTS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp'}
```
Upload subfolders use `D-M-YYYY` date format. Do not change this format.

### localStorage key
```
vista_purchasing_file_tags_v1
Schema: { "folder/file.pdf": "invoice" | "payment" | "other" }
```
Manual tag wins over auto-classification. Tags persist across refreshes. Files are never renamed or moved.

### Classification — `classifyPurchFile(name, relPath)`
Priority order:
1. Manual tag from `vista_purchasing_file_tags_v1` (if set)
2. Auto keyword match:
   - `payment` if filename contains: payment, report, receipt, transfer, bank
   - `other` if filename contains: quotation, quote, delivery, statement, contract, agreement — or matches `\bdn\b`
   - `invoice` (default)

### Proxy routes (proxy.py)
| Route | Method | Behaviour |
|---|---|---|
| `/purchasing-invoices/list` | GET | Directory walk; JSON array `{ folder, name, relativePath, size }`; `Cache-Control: no-store` |
| `/purchasing-invoices/file?path=…` | GET | Serves file inline; RFC 5987 Content-Disposition for Unicode filenames; three-phase (validate → headers → 64KB stream) |
| `/purchasing-invoices/upload` | POST | Multipart upload; saves into `D-M-YYYY` subfolder |
| `/purchasing-invoices/combine` | POST | PyMuPDF merge; accepts `{ "paths": [...] }`; streams combined PDF; `503` if fitz not installed |

### PyMuPDF dependency
Required for Print All and Print Selected. Install: `pip install PyMuPDF`. Proxy returns `503` with instructions if import fails. Tested with v1.27.2.3.

### Sort order for combined PDF — `_sortPurchFilesForPrint(files)`
Newest date folder first (`db − da`), alphabetical within folder. Both `printAllPurchasingFiles()` and `printSelectedPurchasingFiles()` call this before posting to `/purchasing-invoices/combine`.

### Print All behaviour
- Sends only invoice-classified files (payment slips and others excluded)
- `#purchPrintAllLog` panel shows progress; blob URL opened in new tab on success
- Fallback link shown if popup is blocked

### Print Selected behaviour
- Sends all checked files regardless of classification
- Warning shown in log if non-invoice files (payment / other) are included

### Security
- `_safe_purchase_path(rel_path)` blocks `..`, absolute paths, and paths escaping `PURCHASE_INVOICE_DIR`
- Allowed extensions only: `.pdf .png .jpg .jpeg .webp`

### ThreadingHTTPServer
`proxy.py` uses `ThreadingHTTPServer` (not `HTTPServer`) to handle concurrent requests (e.g. PDF viewer + list endpoint firing at the same time).

### Cache-busting on list fetch
Frontend: `fetch('/purchasing-invoices/list?t=${Date.now()}', { cache: 'no-store' })`
Server: `Cache-Control: no-store, no-cache, must-revalidate`
Both are required — some browsers respect only one.

### Upload flow
After upload, frontend clears `purchasingSearch` and the search input value, then calls `fetchPurchasingList()`. This ensures newly uploaded files are visible even if a search filter was active.

### Locked — do not touch
- `printAllInvoices()` — Daftra invoice Print All logic
- Invoice/quotation/DN rendering, QR pipeline, html2pdf chain
- `config.json`, `social-dashboard.html`, `financial-dashboard.html`
- Local folder path (`PURCHASE_INVOICE_DIR`)
- Upload folder date format (`D-M-YYYY`)

---

# CLAUDE_CONTEXT — Financial Dashboard

## Branch and Status

Branch: `feature/financial-dashboard` — not yet merged to `stable-reviewed-history`.
Latest commit: `0bc03db` — Add Financial Dashboard card to homepage.

---

## What the Dashboard Does

Single-file HTML tool (`financial-dashboard.html`) connected to Daftra ERP via the `/daftra/...` proxy route. Fetches sales invoices, purchase invoices, and expenses. Calculates revenue, costs, VAT, and estimated profit tax.

**Two period-independent top cards (always recalculated regardless of sidebar selector):**

- **Yellow — Estimated Profit Tax Payable End of Year**
  Always uses YTD bounds (`getPeriodBounds('ytd')`).
  `taxReserve = Math.max(profit, 0) × 0.20`
  Subtitle hardcoded: `20% of estimated business profit · management estimate`
  Note is context-sensitive (data available / partial year / no data).

- **Red — VAT Reconciliation — {current quarter} So Far**
  Always uses `getCurrentQuarterBounds()` — Gregorian Q1=Jan–Mar, Q2=Apr–Jun, Q3=Jul–Sep, Q4=Oct–Dec.
  `vatBalance = outputVAT − inputVAT(purchases) − inputVAT(expenses)`

**Period selector (sidebar):** Year to Date · This Month · Last Month · Q1 · Q2 · Q3 · Q4 · All Time.
Changing the period affects the panels, monthly chart, and monthly table. It never affects the two top cards.

---

## Locked Behaviours — Do Not Change Without Explicit Approval

### 1. Daftra API key stays server-side only
`financial-dashboard.html` calls `/daftra/...` only. The proxy injects the `APIKEY` header from `config.json → daftra.api_key`. Never add a direct `fetch()` to `daftra.com` from this file.

### 2. Daftra proxy is read-only GET only
`proxy.py` blocks POST, PUT, PATCH, DELETE on `/daftra/...` with 405. Never add write calls through this proxy.

### 3. No auto-fetch
Zero `DOMContentLoaded`, `setInterval`, `setTimeout`, or `window.onload` that triggers data fetching. Manual Fetch Data button only. This is intentional — no timers of any kind.

### 4. Financial values are management estimates only
Labels must reflect this. The yellow card subtitle is hardcoded: `20% of estimated business profit · management estimate`. These figures are not official tax filings. Do not present them as such.

### 5. Personal transfer exclusion — locked
Records where `r.supplier_business_name.trim().toLowerCase() === 'personal transfer'` are excluded from **all** business calculations (profit, VAT, monthly chart, period panels). They appear **only** in the Personal Transfers section.
- Do not remove this filter.
- Do not include personal transfers in business profit, VAT, or purchase totals.
- Pre-filter happens at the top of `renderContent()` before any calculation.

### 6. VAT derivation: `summary_total − summary_subtotal`
`summary_tax1` is always null on both invoices and purchase invoices. Never use it as the primary VAT source for these endpoints. Always derive VAT as `summary_total − summary_subtotal`.

### 7. Purchase invoice reference field: `r.no`
Daftra's formatted purchase invoice number is in `r.no` (e.g. `000048`). `r.number` is undefined on purchase invoices. Always use the fallback chain: `r.no || r.number || r.id || '—'`.

### 8. No localStorage / sessionStorage
`financial-dashboard.html` uses zero client-side storage. Every Fetch Data call starts fresh from the API.

---

## Proxy Route (added commit `8deefc4`)

```
Browser → GET /daftra/{path+querystring}
Proxy strips /daftra prefix
→ forwards to https://{subdomain}.daftra.com/api2/{path+querystring}
→ injects APIKEY header from config.json → daftra.api_key
→ returns JSON response verbatim
POST / PUT / PATCH / DELETE → 405 (blocked)
```

---

## Daftra Endpoints Used

| Endpoint | Purpose |
|---|---|
| `GET /daftra/invoices.json?limit=100&page=N` | Sales invoices (paginated) |
| `GET /daftra/purchase_invoices.json?limit=100&page=N` | Purchase invoices (paginated) |
| `GET /daftra/expenses.json?limit=100&page=N` | Expenses (paginated) |

---

## Key Functions

```js
isPersonalTransfer(r)          // r.supplier_business_name.trim().toLowerCase() === 'personal transfer'
getPeriodBounds(key)           // returns {start, end} Date objects for ytd/this-month/last-month/q1–q4/all
getCurrentQuarterBounds()      // always current Gregorian quarter
getCurrentQuarterLabel()       // e.g. "Q2 2026 (Apr–Jun)"
calcSales(records, bounds)     // salesExVAT, outputVAT filtered to period
calcPurchases(records, bounds) // purchExVAT, inputVAT filtered to period
calcExpenses(records, bounds)  // expExVAT, inputVAT filtered to period
buildMonthlyTable(...)         // monthly breakdown, uses bizPurRecords only
renderPersonalTransfersSection(personalRecords)
```

---

## Sample Regression / Reference Figures

Captured from live Daftra data during branch testing, 2026-06-07.
These values will change as invoices are added. Use for regression checking only — not permanent business facts.

| Metric | Value |
|---|---|
| YTD sales ex-VAT | SAR 488,682.20 |
| YTD business purchases ex-VAT (personal excluded) | SAR 237,920.69 |
| YTD expenses ex-VAT | SAR 92.00 |
| YTD business profit | SAR 250,669.51 |
| Estimated Profit Tax Payable End of Year (20%) | SAR 50,133.90 |
| Q2 2026 VAT reconciliation so far | SAR 16,517.32 payable |
| Personal transfers excluded | 7 records · SAR 32,700.00 ex-VAT · SAR 0.00 derived VAT |

---

## config.json Dependency

Requires `config.json → daftra.subdomain` and `config.json → daftra.api_key` to be set.
Proxy returns 503 with a clear message if either is missing or still a placeholder value.
`config.json` is git-ignored. Never commit it. Never print its contents.
