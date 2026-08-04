# Vista Platform — Claude Handoff

**Last updated:** 2026-08-04
**Branch:** `stable-reviewed-history`
**HEAD:** `a90e3d3` — "Add paused modules section" (matches `origin/stable-reviewed-history`, 0 ahead/0 behind)

Read this file first, every session, before touching anything.

---

## 1. Git state — verify, don't assume

Run before any work:
```bash
git branch --show-current
git status --short
git log --oneline --decorate -5
```

- The only active branch is `stable-reviewed-history`. **Never assume `main` or `master` is the merge target** — this repo's `master` branch is a separate, unrelated line of history. All Vista Platform work happens on `stable-reviewed-history` only, unless the user explicitly names a different branch.
- The working tree is very likely NOT clean. As of this writing there is real, deliberate in-progress work sitting uncommitted:
  - `daftra-pdf-generator_1.html` — 5 unstaged WhatsApp-integration hunks (on hold)
  - `index.html` — unstaged "Supplier Quotation Intelligence" card
  - `.claude/launch.json`, `marketing-dashboard.html`, `proxy.py` — uncommitted catalogue/WhatsApp/Meta work
  - Untracked: `whatsapp_*.py` / `whatsapp-business-center.html`, `catalogue*.py` / `catalogue.html`, `listener_watchdog.py`, `mock_tests.py`, `logs/`, `sqi_ai_proxy.log`, `__pycache__/`, loose root handoff docs (`CHATGPT_HANDOFF.md`, `FINANCIAL_DASHBOARD_HANDOFF.md`, `VISTA_HANDOFF_GPT_CLAUDE_2026-07-20.md`)
  - **Do not discard, stash, or "clean up" any of this without asking the user first.** It is intentional in-progress work, not clutter. Verified against a session memory checkpoint from 2026-07-29 — the working tree has been in this exact state, unchanged, for days.

## 2. Tags — historical markers only

| Tag | Type | Points at | Meaning |
|---|---|---|---|
| `stable-reviewed-history-v1` | annotated | `2d0faec` | Original Social Dashboard stable snapshot — restore point |
| `stable-reviewed-history-v2-financial-dashboard` | lightweight | `01e288c` | Financial Dashboard merge milestone — historical only, 46 commits behind current HEAD |

`stable-reviewed-history-v1` is an annotated tag — `git rev-parse stable-reviewed-history-v1` returns the tag object, not the commit. Use `stable-reviewed-history-v1^{commit}` (or `git log -1 stable-reviewed-history-v1`) to resolve it to `2d0faec`.

**Rule: existing tags must never be moved, deleted, or recreated.** Do not run `git tag -f`, do not push tags, do not create a new tag — not even a "helpful" checkpoint tag — without the user explicitly asking for it by name in that session.

## 3. Current stable modules (all live on `stable-reviewed-history`)

| Module | File | Notes |
|---|---|---|
| Homepage | `index.html` | Now includes a "Paused Modules" nav drawer |
| Social Media Control Center | `social-dashboard.html` | Phase 2A/2A.5/2A.6 complete |
| Personal Task Center | `personal-dashboard.html` | Phase 3 complete |
| Financial Dashboard | `financial-dashboard.html` | Merged, stable |
| Document Generator | `daftra-pdf-generator_1.html` | Invoice/Quotation/Delivery Note/Receipt Voucher/Purchasing Invoice manager + Sarawat quote import + persistent SQI storage |
| Marketing Intelligence Dashboard | `marketing-dashboard.html` | GA4 live; Google Ads/Meta connections paused; moved to Paused Modules nav |
| Local Proxy | `proxy.py` | Serves all HTML, relays Notion/Daftra/Marketing APIs |

## 4. Shipped since the Financial Dashboard milestone (`01e288c`)

Not yet reflected in `docs/*.md` or `CLAUDE_CONTEXT.md` — read these commits' diffs directly if you need implementation detail, don't trust the older docs for this range:

- Meeting/task/UI polish: personal task notes field, Instagram live profile/insights connection + metric fixes
- Bilingual ZATCA VAT PDF export, later simplified
- **Sarawat quote import workflow** (3 commits — initial workflow, parsing/pricing fixes, multi-quantity support) in the Document Generator
- **Persistent SQI storage + improved AI assessment** (`d089eef`)
- **Purchasing invoice date + document-type filters** (`67352db`)
- **Paused Modules section** on the homepage — Marketing Intelligence card moved out of the active grid into a collapsible paused-modules list alongside WhatsApp Business Center and Catalogue Manager (`a90e3d3`, current HEAD)

Plus the large uncommitted body of work described in §1 (WhatsApp Business Center, Catalogue Manager, Supplier Quotation Intelligence card) — none of it is committed/pushed into the stable history yet.

## 5. Startup checklist for a new Claude session

1. `git branch --show-current` — confirm `stable-reviewed-history`
2. `git status --short` — do NOT assume clean; expect the files listed in §1
3. `git log --oneline --decorate -10` — confirm HEAD matches this doc's header; if not, this doc is stale — say so before proceeding
4. Read this file (`docs/CLAUDE_HANDOFF.md`) in full
5. For anything touching Document Generator internals, also read `CLAUDE_CONTEXT.md` (locked QR/PDF/invoice-number rules still apply)
6. Ask the user what they want worked on before touching any file — do not resume prior uncommitted work unprompted

## 6. Protected files / modules — never touch without explicit instruction

- `config.json` — see §7
- `~/.vista-platform/keys/*` — secret credential files, outside repo
- WhatsApp: tokens, listener, tunnel, watchdog, scheduled tasks, LIVE bot mode, `whatsapp_*.py`
- Catalog/catalogue tokens and IDs
- Locked implementation details in `CLAUDE_CONTEXT.md` (QR pipeline, html2pdf chain order, invoice number pass-through, Receipt Voucher signature approach, Financial Dashboard no-auto-fetch rule, personal-transfer exclusion)

## 7. `config.json` safety rule

- `config.json` is git-ignored. Never commit it, never `git add -f` it, never print/echo/log its contents, never paste it into a response.
- Never enter, request, or handle secrets (tokens, API keys, PINs, App Secrets) in plain conversation. Credential setup goes through the proxy's setup-center endpoints only.

## 8. Git workflow — required order for every approved change

1. **Validate locally** — run `python proxy.py` and test in a real browser before declaring anything done
2. **Document** — update `docs/CLAUDE_HANDOFF.md` / `docs/changelog.md` / relevant docs for any functional or architectural change
3. **Commit** — stage only the relevant files; clear commit message
4. **Push only after explicit user approval** of that specific commit — approval does not carry over to future commits

## 9. Stop-and-report rule

Before any of the following, stop and report to the user instead of proceeding:
- Any destructive git operation (`reset --hard`, `checkout --`, `clean -f`, force-push, branch deletion)
- Touching any file listed in §6
- Committing or pushing anything not explicitly reviewed and approved in this session
- Creating or moving a tag
- Any action whose blast radius is unclear from the request

## 10. How Claude should work with this user

- **Inspect first.** Run `git status`/`git log`/`git diff` and read relevant source before proposing anything — never assume the state described in an old handoff doc is current.
- **Explain, then wait.** Lay out what you found and what you propose to do. Do not edit, commit, or push until the user says to.
- **Visual review before commit.** For any UI-facing change, the user checks it in the browser and explicitly approves before it gets committed.
- **Scope discipline.** Work only on the files relevant to the current request. Flag — don't silently touch — unrelated dirty files encountered along the way.
