# Vista Platform — Full Project Handoff
**Date:** 2026-08-21  
**Branch:** `stable-reviewed-history`  
**HEAD:** `eb087ad` — "Improve social task notes and local requester filtering"  
**Remote sync:** HEAD = `origin/stable-reviewed-history` — fully pushed, no pending work.

---

## 1. Git State

```
Branch:  stable-reviewed-history
HEAD:    eb087ad  (local = remote — fully synced)
Remote:  origin → https://github.com/hellye-star/daftra-pdf-generator.git
```

### Last 10 commits
```
eb087ad  Improve social task notes and local requester filtering   ← HEAD (pushed)
25aba54  Add smart proposal generator for Vista and Sarawat
3d146a7  Add collapsible Hussam monthly performance view
8bd6f85  Add Daftra client lookup to Sarawat quote import
e78e0be  Fix purchasing invoice combine logging for Arabic filenames
6ad129f  Clarify Sarawat quotation number label
ab21783  Fix Sarawat form scroll jump while editing
4e9bdd0  Fix Sarawat fallback parser for Jazaaco numbered rows
5ba5eee  Update Claude handoff HEAD reference
09d7cd8  Add Claude handoff for current stable state
```

---

## 2. Tags — DO NOT MOVE

```
stable-reviewed-history-v1                → 2d0faec
stable-reviewed-history-v2-financial-dashboard → 01e288c
```

**Rule:** Never create, move, or delete these tags. Never `git tag -f`. Never push tags unless Youssef explicitly approves the exact tag name and target commit.

---

## 3. Dirty / On-Hold Files (working tree — NOT staged, NOT committed)

These files have uncommitted changes. **Do not stage or commit any of them without explicit approval.**

### Modified (tracked, but NOT staged)
| File | Contains |
|------|----------|
| `proxy.py` | WhatsApp routes (`/api/whatsapp/`), Catalogue routes (`/api/catalogue/`, `/catalogue-images/`) |
| `daftra-pdf-generator_1.html` | WhatsApp send panel CSS + logic (on hold) |
| `index.html` | SQI card added to home dashboard |
| `marketing-dashboard.html` | Instagram recent media + intelligence sections (in-progress) |
| `.claude/launch.json` | Local dev server config (never commit) |

### Untracked (on-hold or temporary)
**WhatsApp system (entire feature on hold — do not touch):**
- `whatsapp-business-center.html`
- `whatsapp_api_client.py`
- `whatsapp_audio_convert.py`
- `whatsapp_bot.py`
- `whatsapp_business_api.py`
- `whatsapp_db.py`
- `whatsapp_handler.py`
- `whatsapp_send_test.py`
- `whatsapp_status.py`
- `whatsapp_webhook_server.py`

**Catalogue system (on hold):**
- `catalogue.html`
- `catalogue_api.py`
- `catalogue_db.py`
- `vista_catalog_capture/` (directory)

**Handoff / temp files (do not commit):**
- `CHATGPT_HANDOFF.md`
- `FINANCIAL_DASHBOARD_HANDOFF.md`
- `VISTA_HANDOFF_GPT_CLAUDE_2026-07-20.md`
- `listener_watchdog.py`
- `mock_tests.py`
- `proposal_only.patch`
- `sqi_ai_proxy.log`
- `logs/` (directory)
- `__pycache__/` (directory)
- `C：WindowsTemptest_baseline.txt` (temp artifact, ignore)

---

## 4. On-Hold Work — Detailed

### WhatsApp Business Center
- Complete feature: send panel in Document Generator, webhook server, bot, DB, API client
- Status: All files untracked; `proxy.py` has WhatsApp routes in dirty diff; `daftra-pdf-generator_1.html` has WhatsApp send panel CSS/JS
- Reason paused: Not yet approved for integration
- Rule: Do NOT include in any `git add`, do NOT mention in commits

### Catalogue System
- `catalogue.html` — full catalogue browser UI
- `catalogue_api.py` / `catalogue_db.py` — SQLite-backed catalogue API
- `vista_catalog_capture/` — image/data capture directory
- `proxy.py` has catalogue routes in dirty diff
- Status: On hold, not approved for commit

### proxy.py On-Hold Routes
The dirty `proxy.py` diff contains:
- GET/POST `/api/catalogue/` → `catalogue_api`
- DELETE/PATCH `/api/catalogue/` → `catalogue_api`
- GET/POST/PATCH `/api/whatsapp/` → `whatsapp_business_api` / `whatsapp_handler`
- `/catalogue-images/` static image serving
- `_serve_catalogue_image()` method

When approving proxy.py changes, stage only the specific approved hunks using `git add -p proxy.py`.

---

## 5. Module Summary

### Document Generator (`daftra-pdf-generator_1.html`)
- Purchasing invoice PDF generation from Daftra API
- Supplier quotation card (manual input + Daftra lookup)
- Sarawat Quote Import: paste supplier PDF text → structured quote card
- Smart proposal generator for Vista and Sarawat quotes
- QR code + html2pdf export
- **Do not touch:** QR/html2pdf logic, purchasing invoice routes in proxy.py

### Sarawat Quote Import
- Sub-feature of Document Generator
- Multi-supplier paste parser with fallback (Jazaaco numbered rows, standard format)
- Daftra client lookup integration
- Smart proposal generator (collapsible, generates Arabic/English scope text)
- Committed and stable at HEAD

### Supplier Quotation Intelligence (`supplier-quotation-intelligence.html`)
- Local-first: compare scope, pricing, risks, historical supplier performance
- SQI card added to `index.html` (dirty — not yet committed)
- Backend: `sqi_storage_api.py` (committed), `/api/sqi/` routes in proxy.py (committed)
- AI proxy: `sqi_ai_proxy.log` (temp, ignore)

### Financial Dashboard (`financial-dashboard.html`)
- Committed and stable at tag `stable-reviewed-history-v2-financial-dashboard`
- **Do not modify** — protected file

### Social Dashboard (`social-dashboard.html`) ← Most Recent Work
- Committed and stable at HEAD (`eb087ad`)
- See Section 6 for full feature list

### Marketing Dashboard (`marketing-dashboard.html`)
- Dirty: Instagram recent media section + intelligence section partially added
- Status: In-progress, not yet approved for commit
- Do not stage without explicit approval

### Personal Dashboard (`index.html`)
- Dirty: SQI card added
- Status: Pending approval

### WhatsApp Business Center (`whatsapp-business-center.html`)
- Complete UI exists as untracked file
- Status: On hold — do not commit

### Catalogue (`catalogue.html`)
- Complete UI exists as untracked file
- Status: On hold — do not commit

---

## 6. Latest Social Dashboard Changes (HEAD — eb087ad)

All changes in `social-dashboard.html` only.

1. **Collapsible Hussam Monthly Performance view** (commit `3d146a7`)  
   — Sidebar panel showing Hussam's task completion by month, collapsible toggle

2. **Notes & Media rendering from Notion page-body blocks** (commit `eb087ad`)  
   — New `renderNotionTextBlocks()` function renders paragraph, h1/h2/h3, bullets, numbered, callout, quote, to-do from Notion page body  
   — Section renamed "Notes & Media", moved above Details in the task detail panel  
   — Scrollable container with `max-height: 300px`  
   — Lazy `📝 Notes` badge on task cards (session-only Set `_notesTaskIds`, no upfront scan)  
   — `data-id` attribute added to `.task-row` for DOM targeting

3. **Meeting Notes drill-down** (commit `eb087ad`)  
   — Fixed blank viewer when clicking a month (e.g. "2026-08 — August")  
   — `openMeetingPage()` now detects all-`child_page` responses and renders a clickable sub-list  
   — Click a meeting → opens its actual content blocks  
   — Meeting Agendas behavior unchanged

4. **Dashboard-local Requested By system** (commit `eb087ad`)  
   — `localStorage` key `vista_requested_by_v1` — `{ [taskId]: 'Youssef' | 'Hussam' }`  
   — Never written to Notion (Hussam's schema cannot be modified)  
   — Sidebar filter: All / Youssef / Hussam / Blank  
   — Task detail dropdown to set/change value  
   — Task row badges: `Req: Youssef` (blue) / `Req: Hussam` (grey)  
   — New task modal defaults to Youssef; saves after successful creation

5. **Bulk blank-task requester tagging** (commit `eb087ad`)  
   — "Tag Blank Tasks" collapsible panel in sidebar  
   — Lists up to 30 active (non-Done) untagged tasks alphabetically  
   — Y / H quick-tag buttons per task  
   — Updates badge immediately without re-render

---

## 7. Workflow Rules

1. **Inspect first.** Before editing any file, read the relevant section. Report findings before writing code.
2. **No edits without approval.** Propose changes, wait for explicit approval, then implement.
3. **Visual review before commit.** Test in browser (proxy.py on localhost:8080). Show screenshots or test results.
4. **Stage only approved hunks/files.**  
   - Use `git add <specific-file>` or `git add -p` for partial staging  
   - Never `git add .` or `git add -A`  
   - Always run `git diff --cached --name-only` before committing to confirm scope
5. **Commit and push only after approval.** Show `git diff --cached` first. Get explicit "proceed" from Youssef.
6. **Never commit `config.json`.** It contains API tokens. Never print, stage, or expose it.
7. **Never accidentally stage on-hold work.** WhatsApp files, catalogue files, and the on-hold proxy.py hunks must never appear in `git diff --cached`.
8. **Never force push.** Never use `--force` or `--force-with-lease` without explicit instruction.
9. **Never move tags.** Tags `stable-reviewed-history-v1` and `stable-reviewed-history-v2-financial-dashboard` are permanent anchors.
10. **Scope of all current work:** Modify only the file(s) Youssef approves per task. Do not touch `financial-dashboard.html`, `daftra-pdf-generator_1.html` QR/html2pdf logic, or `proxy.py` purchasing invoice routes without approval.

---

## 8. Starting a New Claude Session

At the start of a new Claude Code session, run these commands and report state before doing anything else:

```bash
git branch --show-current
git status --short
git log --oneline --decorate -5
git tag --list
```

Then read this file:
```
Read: docs/CLAUDE_HANDOFF.md
```

Report:
- Current branch and HEAD commit
- Any unexpected staged files (should be none)
- Any new untracked files not listed in Section 3
- Confirm tags are intact
- State what you are ready to work on

**Do not edit any file until Youssef gives an explicit task.**

---

## 9. ChatGPT Session Prompt

Paste the following into a new ChatGPT session to orient it fully:

---

```
You are helping me (Youssef) manage and develop the Vista Platform — a set of internal web tools 
built as single-file HTML applications served by a Python proxy (proxy.py) on localhost:8080.

== Project Overview ==
Repository: https://github.com/hellye-star/daftra-pdf-generator.git
Branch: stable-reviewed-history
HEAD: eb087ad — "Improve social task notes and local requester filtering" (pushed, synced)
Working directory: C:\claude

== Tools / Modules ==
1. Document Generator (daftra-pdf-generator_1.html)
   - Purchasing invoice PDF from Daftra API
   - Sarawat Quote Import: paste supplier text → structured quote card
   - Smart proposal generator (Arabic/English)
   - Supplier Quotation card with Daftra client lookup
   - QR + html2pdf export

2. Supplier Quotation Intelligence (supplier-quotation-intelligence.html)
   - Compare scope, pricing, risks, supplier history
   - Local-first with SQLite backend (sqi_storage_api.py)

3. Financial Dashboard (financial-dashboard.html) — stable, do not modify

4. Social Dashboard (social-dashboard.html) — most recent work, fully committed
   - Notion task board for Hussam's Vista team
   - Collapsible Hussam Monthly Performance view
   - Notes & Media: renders Notion page-body blocks (paragraphs, bullets, callouts, etc.)
   - Meeting Notes drill-down: month pages now show clickable sub-list of meetings
   - Requested By system: localStorage-based (Youssef/Hussam/Blank), never written to Notion
   - Bulk blank-task tagger in sidebar
   - Lazy "📝 Notes" badge on task cards

5. Marketing Dashboard (marketing-dashboard.html) — in progress, not yet committed

6. WhatsApp Business Center — on hold, do not commit

7. Catalogue system — on hold, do not commit

== On-Hold / Dirty Files ==
These exist in the working tree but must NOT be committed without explicit approval:
- proxy.py: has WhatsApp + Catalogue routes in dirty diff
- daftra-pdf-generator_1.html: WhatsApp send panel on hold
- index.html: SQI card added (pending approval)
- marketing-dashboard.html: Instagram sections (in progress)
- All whatsapp_*.py files (untracked, on hold)
- catalogue.html, catalogue_api.py, catalogue_db.py (untracked, on hold)

== Important Tags — Never Move ==
- stable-reviewed-history-v1 → 2d0faec
- stable-reviewed-history-v2-financial-dashboard → 01e288c

== My Workflow Rules ==
1. Inspect before editing — read the relevant section first, report findings
2. No edits without my explicit approval
3. Stage only approved files/hunks — never git add . or git add -A
4. Show git diff --cached before committing
5. Commit and push only after I say "proceed"
6. Never commit config.json (contains API tokens)
7. Never stage WhatsApp or catalogue on-hold files
8. Never force push
9. Never move tags
10. Scope per task: only modify the specific file(s) I approve

== Tech Stack ==
- Single-file HTML tools (vanilla JS, no build step)
- Python HTTP server: proxy.py (SimpleHTTPRequestHandler)
- Notion API: proxied through /notion/ routes
- Daftra API: proxied through /daftra/ routes
- Google Ads + Meta APIs: proxied read-only
- localStorage for client-side persistence (no user DB)
- Fonts: Jost (sans), Cormorant Garamond (serif) from Google Fonts

== What I Need From You ==
- Help plan and implement features for the above tools
- Always inspect current state before suggesting changes
- Give me focused diffs and test instructions
- Follow my workflow rules exactly
- When I say "inspect only", do not suggest or implement anything — just report

Current focus: Ready for new tasks. Ask me what to work on next.
```

---

*This handoff was generated 2026-08-21. Verify git state at the start of each session before acting.*
