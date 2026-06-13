# Changelog — Vista Platform

---

## [2026-06-14] — Marketing Intelligence: Meta / Instagram Setup Center

### proxy.py + marketing-dashboard.html

Added a local-only Meta / Instagram Setup Center for Instagram 1 so credentials can be configured through the browser UI without ever storing secrets client-side. All credential handling is server-side only.

**What was added:**

- **`GET /api/setup/meta/status`** — returns masked configuration state: `{configured, token_file_exists, ig_account_id_set, ig_account_id_masked (****1234), page_id_masked, message}`; never returns token value, file path, full account ID, or traceback
- **`POST /api/setup/meta/save`** — localhost-only; accepts `{access_token, instagram_business_account_id, page_id (optional)}`; validates token is non-empty, account ID is numeric, page ID is numeric if provided; saves token to `%USERPROFILE%\.vista-platform\keys\meta-access-token.json` (outside git repo); updates `config.json` atomically via `os.replace()` with `{token_path, instagram_business_account_id, page_id}` only — token never written to `config.json`; reloads in-memory vars without proxy restart; returns only `{ok, message}` with masked account ID; token field cleared from browser DOM immediately after successful save
- **`POST /api/setup/meta/test`** — localhost-only; reads token from keys file; calls `GET /{ig_business_account_id}?fields=username,followers_count` via plain `urllib.request` (no new Python package required); returns `{ok, message}` with username and follower count on success, or a safe categorised error string on failure — no token, path, or traceback returned; error codes 190/100/10/4/200 mapped to safe readable messages
- **`GET /api/meta/status`** — runtime status endpoint (same response shape as setup status); accessible for future report fetch flow
- **`POST /api/meta/*` → 405** — explicit block; all other existing routes unaffected
- **Meta Setup block in Data Source tab** — long-lived access token password input (full width), Instagram Business Account ID and Facebook Page ID (optional) in 2-column grid; Save Locally and Test Connection buttons; status badge cycling Not Configured → Needs Fix → Configured; `metaSetupCheckStatus()` runs at boot to populate badge silently; block is positioned above the existing Instagram 1 manual import block
- **Startup status line** — proxy now prints `Meta / Instagram API : OK - configured` or `NOT SET - use Meta Setup Center`; no token value or path printed
- **No secrets in tracked files** — token file at `%USERPROFILE%\.vista-platform\keys\meta-access-token.json` is outside the repo; `config.json` is git-ignored and was not printed, staged, committed, or pushed; no token value appears in any HTTP response or log line

**Not added (scope boundary):**
- `GET /api/meta/report` — no report fetch endpoint yet
- No "Fetch Instagram 1 from API" active button — informational note only that it is coming after test passes
- No Instagram 2, TikTok, or Google Ads report endpoint
- No write, publish, or ad actions
- Manual Instagram 1 JSON import (textarea + Load/Reset buttons) remains unchanged and fully working

---

## [2026-06-14] — Marketing Intelligence: Google Ads Setup Center

### proxy.py + marketing-dashboard.html

Added a local-only Google Ads Setup Center so credentials can be configured through the browser UI without ever storing secrets client-side. All credential handling is server-side only.

**What was added:**

- **`GET /api/setup/google-ads/status`** — returns masked configuration state: `{configured, customer_id_set, customer_id_masked (****1234), login_customer_id_masked, oauth_file_exists, gads_lib_installed, message}`; never returns developer token, client secret, refresh token, access token, file path, or traceback
- **`POST /api/setup/google-ads/save`** — accepts `{developer_token, client_id, client_secret, refresh_token, customer_id, login_customer_id (optional)}`; validates all required fields and that customer IDs are numeric (hyphens stripped automatically); saves OAuth secrets to `%USERPROFILE%\.vista-platform\keys\google-ads-oauth.json` (outside the git repo); updates `config.json` atomically via `os.replace()` with `{oauth_json_path, customer_id, login_customer_id}` only — no secrets written to `config.json`; reloads in-memory vars without proxy restart; returns only `{ok, message}` with masked customer ID; secret fields are cleared from the browser DOM immediately after successful save
- **`POST /api/setup/google-ads/test`** — lazy-imports `google.ads.googleads.client`; if missing returns safe 503 with `python -m pip install google-ads`; if installed, calls `CustomerService.list_accessible_customers()` (smallest possible read-only API call); returns `{ok, message}` with accessible account count or a safe categorised error string — no token, path, or traceback returned
- **`GET /api/google-ads/status`** — runtime status endpoint (same response shape as setup status); accessible without localhost restriction for future use by the report fetch flow
- **Setup save/test endpoints are localhost-only** — `_require_localhost()` check on each POST; proxy bind host remains `127.0.0.1` unchanged
- **Google Ads Setup block in Data Source tab** — 6-field 2-column form (Developer Token, OAuth Client ID, OAuth Client Secret, Refresh Token, Customer ID, Login Customer ID); all secret fields use `type="password"`; Save Locally and Test Connection buttons; status badge cycling Not Configured → Needs Fix → Configured; `gadsSetupCheckStatus()` runs at boot to populate badge silently; block is positioned above the existing manual Google Ads import block
- **Startup status line** — proxy now prints `Google Ads API : OK - configured` or `NOT SET - use Google Ads Setup Center`; no credential value or path printed
- **No secrets in tracked files** — key file at `%USERPROFILE%\.vista-platform\keys\google-ads-oauth.json` is outside the repo; `config.json` is git-ignored and was not printed, staged, committed, or pushed; no credential value appears in any HTTP response or log line

**Not added (scope boundary):**
- `GET /api/google-ads/report` — no report fetch endpoint yet
- No "Fetch Google Ads from API" active button — only informational note that it is coming after test passes
- No Google Ads write actions
- Manual Google Ads JSON import (textarea + Load/Reset buttons) remains unchanged and fully working
- Meta/Instagram, TikTok API setup — zero lines for these platforms

---

## [2026-06-13] — Marketing Intelligence: GA4 Setup Center

### proxy.py + marketing-dashboard.html

Added a local-only GA4 Setup Center so credentials can be configured through the browser UI without ever storing secrets client-side. All credential handling is server-side only.

**What was added:**

- **`GET /api/setup/ga4/status`** — returns masked configuration state: `{configured, property_id_set, property_id_masked (****1234), creds_file_exists, gauth_installed, message}`; never returns credential values, file paths, or tokens
- **`POST /api/setup/ga4/save`** — accepts `{property_id, service_account_json}` from the browser; validates the SA JSON (5 required fields, correct `type`), saves the key file atomically to `%USERPROFILE%\.vista-platform\keys\ga4-service-account.json` (outside the git repo), and updates `config.json` atomically via `os.replace()`; reloads in-memory config vars without proxy restart; returns only `{ok, message}` — no path, no secret echoed back; SA JSON textarea is cleared by the browser immediately after successful save
- **`POST /api/setup/ga4/test`** — authenticates with the saved service account and runs a minimal 1-row GA4 report to confirm property access; returns `{ok, message}` with safe error strings only
- **Setup endpoints are localhost-only** — each POST handler checks `client_address[0] == '127.0.0.1'` and returns 403 otherwise; proxy bind host remains `127.0.0.1` unchanged
- **GA4 Setup block in Data Source tab** — Property ID input, Service Account JSON textarea, Save Locally button, Test Connection button, status badge cycling Not Configured → Configured → (Connected after test); SA JSON textarea is always empty on load and cleared after save; `ga4SetupCheckStatus()` runs at boot to populate the badge silently if proxy is running
- **No secrets in tracked files** — key file at `%USERPROFILE%\.vista-platform\keys\` is outside the repo; `config.json` is git-ignored and was not printed, staged, committed, or pushed; no credential value appears in any HTTP response or log line

**Not added (scope boundary):**
- Google Ads, Meta/Instagram, or TikTok API setup — zero lines for these platforms
- Manual GA4 paste import and GA4 API fetch button remain fully working alongside the new setup block

---

## [2026-06-13] — Marketing Intelligence: GA4 API Pilot

### proxy.py + marketing-dashboard.html

Added a read-only GA4 API pilot through the local proxy. Credentials stay server-side only — the browser never receives a token, file path, or service account value. No APIs, OAuth browser flows, credentials in frontend, or write actions.

**What was added:**

- **`GET /api/ga4/status`** — new proxy endpoint; returns `{configured, property_id_set, creds_file_exists, gauth_installed, message}`; reports whether `google-auth` is installed, whether the property ID and credentials file are set in `config.json`; never returns credential values, file paths, or tracebacks
- **`GET /api/ga4/report?period=last_30_days`** — new proxy endpoint; authenticates via Service Account JSON (server-side only); calls GA4 Data API v1beta in three sub-requests (page-level sessions + engagement, custom events per page, audience by country/city/device); transforms and returns JSON shaped identically to the existing manual GA4 import schema — existing `renderGA4Imported()` and `aggregateGA4()` are reused with no changes
- **`POST /api/ga4/` → 405** — explicit block; purchasing POST routes remain first in `do_POST` and are unaffected
- **Lazy `google-auth` import** — `from google.oauth2 import service_account` and `import google.auth.transport.requests` are inside a `try/except ImportError` inside `_ga4_report()` only; if the package is missing, only the GA4 endpoints return `503` with a clear `pip install` message; proxy starts and all other routes work normally
- **GA4 startup status line** — proxy now prints `GA4 API : OK - configured` or `NOT SET - edit config.json (marketing_apis.google.ga4)` at startup; no credential path or value is printed
- **"Fetch GA4 from API" button** — added to the Data Source → GA4 import block in `marketing-dashboard.html`; calls `/api/ga4/status` first, then `/api/ga4/report`; on success sets `GA4_IMPORTED`, calls `updateDSModeIndicator()`, switches to the GA4 tab; shows **API Connected**, **API Error**, or **Needs Config** state clearly; manual paste import textarea and Load/Reset buttons are unchanged and remain fully functional alongside the button
- **`clearGA4Data()` updated** — also clears the API status span when reset is clicked

**Security:**
- No credential value, file path, `access_token`, service account email, or traceback is ever returned in an HTTP response
- `_ga4_property_id` is included in the `propertyName` field of the report response (public numeric ID, not a credential); all other config values remain server-side only
- `config.json` was not touched, printed, staged, committed, or pushed

**Not added (scope boundary):**
- Google Ads API, Meta/Instagram API, TikTok API — zero lines for these platforms
- Existing Daftra, Notion, and purchasing routes were not modified; all confirmed working after implementation

---

## [2026-06-13] — Marketing Intelligence: AI Coach & Instagram 2 Placeholders

### marketing-dashboard.html

Upgraded the AI Report tab into an AI Coach / Recovery Plan tab with rule-based beginner guidance derived entirely from imported metrics. No new files. No APIs, OAuth, credentials, fetch calls, AI API calls, or ad write actions.

**What was added / changed:**
- **AI Report tab renamed to AI Coach** — dynamic panel rendered by `renderAICoachPanel()`, re-renders automatically when any import is loaded or cleared
- **AI Coach sub-navigation** — six scroll buttons at the top of the panel: Overview, Instagram, TikTok, Google Ads, Action Plan, Campaign Setup Coach; each button scrolls to a stable anchor ID (`ai-section-*`) via `aiCoachScrollTo()`
- **Sections 1–7** — Account Health Score (0–100 with colour-coded label), Dead Account Diagnosis (Dormant / Engagement Problem / CTA Problem / Growth Problem), Follower-to-Reach Reality Check (three-case wording: no import / imported but `currentFollowers` missing / full ratio with bar), Content Type Recovery Advice (table from `byType` aggregation), Boosting Readiness Check (reads GAds CTR/conversions + GA4 bounce rates + IG1 engagement), 7-Day Recovery Plan (personalised when IG1 loaded), What to Post Next (derived from best Reel, most-saved, most-commented post; cross-references TikTok top video)
- **TikTok section in AI Coach** — always rendered with a stable `id="ai-section-tiktok"` anchor; shows real analysis when TikTok data is imported; shows a clear pending/schema-only placeholder with a link to Data Source when no data is loaded — TikTok sub-nav button always has a valid scroll target
- **Campaign Setup Coach section** — rule-based platform-specific ad setup guidance: objective, audience, creative, landing page/CTA, test budget, when to stop / change / scale, for Instagram Ads, Google Ads, GA4/Website readiness, and TikTok Ads; reads imported data to surface alerts (e.g. zero conversions vs spend, high-bounce landing pages, low engagement before boosting); TikTok Ads shown as pending if no TikTok data loaded
- **Follower-to-reach wording fixed** — three explicit cases: no IG1 import → clear prompt; IG1 loaded but `currentFollowers` missing → explicit message; full data → ratio bar + contextual advice
- **Instagram 2 panel clarified as schema-only / future account** — updated mock notice makes explicit that IG2 (@vista.branding) is a separate account with its own future import pipeline, not connected to IG1 data; added three placeholder sections (Best Content — by Metric, Weakest Content, Audience & Engagement Insight) labelled "Available after Instagram 2 import is wired" so the panel is clearly intentional, not broken

**Known working imports (not retested this change):**
- Instagram 1 manual import — working (no changes to `IG1_IMPORTED`, `aggregateIG1`, `loadIG1Data`, `clearIG1Data`)
- GA4 manual import + visitor segment analysis — working (no changes)
- Google Ads manual import — working (no changes)
- TikTok manual import — working (no changes to `TIKTOK_IMPORTED`, `aggregateTikTok`, `loadTikTokData`, `clearTikTokData`)

**Files NOT changed:** `proxy.py`, `config.json`, `index.html`, `social-dashboard.html`, `financial-dashboard.html`, `personal-dashboard.html`, `daftra-pdf-generator_1.html`

---

## [2026-06-13] — Marketing Intelligence: Instagram 1 Account Baseline & Validation

### marketing-dashboard.html

Extended the Instagram 1 manual import with account-level baseline fields and validation warnings. No new files. No APIs, OAuth, credentials, fetch calls, AI API calls, or ad write actions.

**What was added:**
- **Account baseline fields** now supported at the top level of the Instagram 1 JSON: `currentFollowers`, `followersAtStart`, `followersAtEnd`, `accountReach`, `accountImpressions`, `profileVisits`, `websiteClicks`, `whatsappClicks`, `totalContentPublished` — all optional but recommended
- When any baseline field is present, an **Account Baseline** section renders above post-level totals: current followers card, follower growth (start → end), account reach, account impressions, profile visits, website clicks, WhatsApp clicks, and total content published
- **Five validation warnings** appear at the top of the imported render when triggered:
  - Error — sum of followsGained across posts exceeds currentFollowers (implausible for small accounts)
  - Warning — follower count dropped start→end but no unfollows recorded on any post
  - Info — total post reach is more than 5× accountReach (possible double-counting; prompts clarification)
  - Info — imported handle does not match @vistaunited.co (wrong account check)
  - Info — currentFollowers missing (soft prompt to add it for validation)
- Warnings are colour-coded (red/amber/blue) with icons; no warnings = no block rendered
- **Imported-notice banner updated** to read "Imported manual data active. Numbers are based on the JSON you pasted. This is not live API data — use actual Instagram Insights numbers for accuracy."
- **DS schema example updated** to use realistic numbers for @vistaunited.co (9 followers, reach 70–210 per post, follows 0–2 per post) with explicit note: "Example format only — replace all values with your real Instagram Insights numbers"
- Post-level summary strip relabelled "Post-Level Totals" to distinguish it from the new account baseline strip

**Known working imports (previously verified, not retested this change):**
- Instagram 1 manual import — working (baseline/warnings added; core aggregation/load/clear unchanged)
- GA4 manual import + visitor segment analysis — working
- Google Ads manual import — working
- TikTok — schema-only, not wired

**Files NOT changed:** `proxy.py`, `config.json`, `index.html`, `social-dashboard.html`, `financial-dashboard.html`, `personal-dashboard.html`, `daftra-pdf-generator_1.html`

---

## [2026-06-13] — Marketing Intelligence: Google Ads Manual Import

### marketing-dashboard.html

Added real-data manual import for the Google Ads tab. No new files. No APIs, OAuth, credentials, fetch calls, AI API calls, or ad write actions.

**What was added:**
- **Google Ads JSON import** — textarea + Load / Reset to Mock buttons in the Data Source tab; badge updates to reflect active import state
- **`GADS_IMPORTED` in-memory state** — same pattern as IG1 and GA4; data clears on page refresh, no localStorage
- When Google Ads JSON is loaded, the **Google Ads tab re-renders entirely from imported data** instead of mock figures
- **`aggregateGAds(rows)`** — calculates total spend, impressions, clicks, conversions; overall CTR, CPC, cost per conversion; campaign rankings by conversions and by spend; zero-conversion (waste) campaigns; best and weak keywords; best and weak search terms; landing page traffic grouping with final URL mapping; high-click / zero-conversion rows for GA4 cross-reference
- **Six analysis questions answered in imported render:**
  - Q1 — Which campaigns are working? (best by conversions)
  - Q2 — Which campaigns are wasting spend? (high spend, zero conversions)
  - Q3 — Which keywords attract serious visitors? (best / weak keyword comparison)
  - Q4 — Which landing pages receive paid ad traffic?
  - Q5 — Which landing pages need GA4 comparison? (50+ clicks, zero conversions)
  - Q6 — What should be optimised next? (actionable recommendation cards)
- **Mock mode preserved** — `renderGAdsMock()` reproduces the exact previous mock panel (summary strip, weekly bar charts, key metrics) when no data is imported
- Data Source mode chips and import badge update dynamically on load and reset
- Instagram 1 import unchanged and fully working
- GA4 import and visitor segment analysis unchanged and fully working
- TikTok remains schema-only (no import wiring)

**Files NOT changed:** `proxy.py`, `config.json`, `index.html`, `social-dashboard.html`, `financial-dashboard.html`, `personal-dashboard.html`, `daftra-pdf-generator_1.html`

---

## [2026-06-13] — Marketing Intelligence: GA4 Visitor Segment Analysis

### marketing-dashboard.html

Extended the GA4 manual import with visitor segment analysis. No new files. No APIs, OAuth, credentials, fetch calls, AI API calls, or ad write actions.

**What was added:**
- **Visitor Segment Analysis section** in the Website / GA4 imported render — appears below the six page analysis tables when audience data is present in the imported JSON
- Summary strip: new visitors, returning visitors, sessions per user, number of locations tracked
- **Top countries** by user volume with average engagement rate and total conversion actions per country
- **Top cities** by user volume with session counts and conversion actions
- **Device category breakdown** (mobile / desktop / tablet) with user count and percentage share
- **Engagement insight cards**: strong engagement segments (≥ 45% engagement rate), weak engagement segments (< 30% engagement rate), cities producing WhatsApp / contact / form actions
- **Optional `audience` array** added to GA4 JSON schema — each entry is an aggregate visitor segment row by country, city, and device (no personal identifiers). Supported fields: `country`, `city`, `deviceCategory`, `users`, `newUsers`, `returningUsers`, `sessions`, `averageEngagementTime`, `engagementRate`, `keyEvents`, `whatsappClicks`, `contactClicks`, `formSubmits`
- **Page-level location fields** now also accepted on each `pages` row: `country`, `city`, `deviceCategory`, `newUsers`, `returningUsers` — used as fallback if no `audience` array is provided
- Wording throughout uses "visitor segments" and "user location analysis" — no implication of personal visitor identity
- Imported data remains memory-only (no localStorage, clears on page refresh)
- Instagram 1 import unchanged and fully working

**Files NOT changed:** `proxy.py`, `config.json`, `index.html`, `social-dashboard.html`, `financial-dashboard.html`, `personal-dashboard.html`, `daftra-pdf-generator_1.html`

---

## [2026-06-13] — Marketing Intelligence: Website / GA4 Tab + Expanded Data Source Schemas

### marketing-dashboard.html

Extended the Marketing Intelligence dashboard with a new Website Analytics tab and richer Data Source guidance. No new files. No APIs, OAuth, credentials, fetch calls, AI API calls, or ad write actions.

**What was added:**
- New **Website / GA4 tab** (between Google Ads and LinkedIn) — placeholder panel explaining the 6 analytical questions GA4 will answer once data is imported: which landing pages bring visitors, which pages keep people longest, which pages have weak engagement, which pages produce WhatsApp/contact/form actions, which source sends traffic that does not convert, which pages need a better CTA
- Each question card shows the specific JSON fields that drive the answer (`landingPage`, `sourceMedium`, `averageEngagementTime`, `whatsappClicks`, `keyEvents`, `formSubmits`, etc.)
- **Google Ads vs GA4 distinction panel** in the Data Source tab — clearly explains Google Ads = before-the-click (campaign, keyword, spend, CTR, CPC, conversions) vs GA4 = after-the-click (landing page behaviour, engagement time, events, WhatsApp/contact/form actions)
- **TikTok schema** expanded with `contentType`, `avgWatchTimeSec`, `completionRate`, `profileVisits`, `websiteClicks` — 2 example videos in schema
- **Google Ads schema** expanded with `searchTerm`, `finalUrl`, `landingPage` — 2 example rows (best vs weak keyword)
- **GA4 schema** fully defined for the first time — all 15 fields including `landingPage`, `pagePath`, `pageTitle`, `sourceMedium`, `sessions`, `users`, `views`, `averageEngagementTime`, `engagementRate`, `eventCount`, `keyEvents`, `whatsappClicks`, `contactClicks`, `formSubmits`, `scrolls` — 3 example pages
- **GA4 export instructions** added — covers GA4 Explore setup, dimensions/metrics to select, event tracking note, and export steps
- TikTok export instructions expanded with per-video metric sourcing guidance
- Instagram 2, TikTok, Google Ads, GA4 import blocks relabelled **"Schema Ready · Wiring Next"** (no longer greyed out)
- Data Source mode status updated to include Website / GA4 chip
- Instagram 1 manual import unchanged and fully working

**Files NOT changed:** `proxy.py`, `config.json`, `index.html`, `social-dashboard.html`, `financial-dashboard.html`, `personal-dashboard.html`, `daftra-pdf-generator_1.html`

---

## [2026-06-12] — Marketing Intelligence: Manual Instagram 1 JSON Import

### marketing-dashboard.html

Added a manual data import layer to the Marketing Intelligence dashboard. No new files. No APIs, OAuth, credentials, fetch calls, AI API calls, or ad write actions.

**What was added:**
- New **Data Source tab** (8th tab) with: mode status indicator, Instagram 1 JSON paste area, export instructions for all platforms (Instagram, TikTok, Google Ads, LinkedIn), full JSON schema examples for all 4 platforms, and a "what's next / API connection" note
- **Instagram 1 JSON import:** user pastes post-level JSON, clicks Load, data is validated with `JSON.parse()` and stored in memory only — no localStorage, no network request, clears on page refresh
- When imported JSON is loaded, the **Instagram 1 tab re-renders entirely from imported data**: organic metrics (reach, impressions, likes, comments, shares, saves, profile visits, website clicks, WhatsApp/message clicks, follows, unfollows, engagement rate, interaction rate), weekly bar charts bucketed by post publish date, content type comparison (Reel/Carousel/Static Post/Story), best content by reach/saves/shares/profile visits, weakest content with reason, audience insight text
- **Paid spend section is hidden in imported mode** — no fake SAR figures mixed with real organic data. Replaced with an explanatory note
- **Mock data remains the fallback** — if no JSON is loaded, all existing mock content is shown as before. Other tabs (Instagram 2, TikTok, Google Ads, LinkedIn, Overview, AI Report) remain on mock data
- Topbar badge and Data Source mode chips update to reflect Instagram 1 import state
- Reset-to-Mock button available in the imported data banner and Data Source tab
- XSS-safe rendering via `esc()` helper on all user-supplied strings

**Files NOT changed:** `proxy.py`, `config.json`, `index.html`, `social-dashboard.html`, `financial-dashboard.html`, `personal-dashboard.html`, `daftra-pdf-generator_1.html`

---

## [2026-06-12] — Marketing Intelligence: Readability, Mock-Data Disclaimers, Organic Instagram Metrics

### marketing-dashboard.html

Improvement pass on the V1 Marketing Intelligence dashboard. No new files. No APIs, OAuth, credentials, AI API calls, or ad write actions introduced.

**Changes:**
- Improved overall readability (font sizes, line-height, secondary text contrast)
- Topbar badge changed from "Sample Data" → "Mock Data" for clarity
- Added amber mock-data disclaimer banners to every panel (Overview, IG1, IG2, TikTok, Google Ads, AI Report), clearly stating all figures are sample/static and spend values are illustrative
- **Instagram 1 & 2 — richer organic metrics added:** Interactions total, Likes, Comments, Shares, Saves, Engagement Rate, Interaction Rate, Profile Visits, Website Clicks, WhatsApp/Message Clicks, Follows Gained, Unfollows
- **Content type comparison grids:** Reels vs Carousels vs Static Posts vs Stories — average reach, engagement rate, and saves per format
- Best content tables by: Reach, Saves, Shares, Profile Visits
- Weakest content section with possible reason per post
- Audience & engagement insight blocks (why interaction is low, why certain posts drive profile visits, CTA gap analysis)
- Weekly interaction bar charts added alongside reach charts on both IG tabs
- `const MOCK` object expanded with interaction arrays for IG1 and IG2
- **AI Report rewritten:** 5 analysis items (engagement issue diagnosis, Before&After reel explanation, content type ranking, Google keyword intent mismatch, TikTok CPL opportunity) + 3 practical next actions + mock-data disclaimer at top and bottom

**Files NOT changed:** `proxy.py`, `config.json`, `index.html`, `social-dashboard.html`, `financial-dashboard.html`, `personal-dashboard.html`, `daftra-pdf-generator_1.html`

---

## [2026-06-12] — Marketing Intelligence Dashboard V1 (Static)

### marketing-dashboard.html (new) · index.html

New standalone Marketing Intelligence dashboard. V1 uses mock/sample data only — no APIs, no OAuth, no credentials, no AI API calls, and no ad budget or campaign write actions.

**Files changed:**
- `marketing-dashboard.html` — new standalone dashboard (no proxy dependency, no fetch calls)
- `index.html` — homepage tile added (Card 5, `status-soon` badge "V1 · Static")

**Files NOT changed:** `proxy.py`, `config.json`, `social-dashboard.html`, `financial-dashboard.html`, `personal-dashboard.html`, `daftra-pdf-generator_1.html`

**Dashboard structure — 7 tabs:**
- Overview — cross-channel summary (Total Spend · Total Reach · Total Leads · Blended CPL) + per-channel mini-cards (Instagram 1, Instagram 2, TikTok, Google Ads)
- Instagram 1 — account-level metrics (Spend, Reach, Impressions, Follower Growth, CTR, CPC, Leads, Cost per Lead, Engagement Rate, Best/Weakest post), weekly bar charts
- Instagram 2 — same layout as Instagram 1
- TikTok — same layout with TikTok labels (Views instead of Reach), weekly bar charts
- Google Ads — search-focused metrics (Impressions, Clicks, CTR, CPC, Cost per Lead, Search Impression Share, Avg. Position), Top/Weakest keywords table
- LinkedIn — placeholder ("coming soon")
- AI Report — 4 structured mock weekly recommendations with channel tags and a clear sample-data disclaimer

**Mock data:** All values are static sample data hardcoded in a `const MOCK` object at the top of the script block. No external data source, no network requests. Replacing `MOCK` with live data in a future session is the intended upgrade path.

**Validation performed (2026-06-12):**
- All 7 tab IDs confirmed to match their 7 panel IDs exactly
- HTTP 200 confirmed for both pages via `localhost:8080`
- Static analysis: zero `fetch()`, `api_key`, `oauth`, `config.json`, `notion`, or `daftra` references in `marketing-dashboard.html`
- Visual screenshot validation: topbar, tab bar, Overview summary cards, and all 4 channel breakdown cards confirmed rendering correctly
- All protected files confirmed untouched vs HEAD (`git diff --name-only HEAD` = `index.html` only)

---

## [2026-06-11] — Personal Task Center: View Modes (Due Soon / Calendar / Board / All Tasks)

### personal-dashboard.html only — no other files changed

Added four view modes to the Personal Task Center. All views are client-side — rendered from the already-loaded `allTasks` array on first load. No extra Notion API calls, no view-metadata queries, no Google Calendar API or OAuth.

**View switcher:** Tab bar above the filter bar (Due Soon / Calendar / Board / All Tasks). Filter bar (status tabs + priority dropdown) is only shown in All Tasks view.

**Due Soon view:**
- Two sections: Overdue (red header, danger border) and Upcoming — Next 7 Days (grey header).
- Both sorted by due date ascending.
- Full task cards with inline expand/edit/archive — same behaviour as All Tasks.
- "You're all clear" empty state when nothing qualifies.

**Calendar view:**
- Tasks grouped by date buckets: Overdue / Today / Tomorrow / This Week / Next Week / Later / No Date.
- Each group has a header with the bucket label and task count.
- Rows show: status dot, time (if the due datetime includes a time component), task name, priority badge, tag badges.
- Overdue header shown in red; Today header in dark.
- Clicking a row switches to All Tasks view and expands that task's edit panel (scrolls into view).

**Board view:**
- 3 columns: Not started (grey dot) / In progress (blue dot) / Done (green dot).
- Cards show: task name, priority badge, tag badges, due date (red + "· Overdue" if overdue).
- Cards sorted by due date ascending within each column.
- Clicking a card switches to All Tasks view and expands that task's edit panel.
- Page max-width widened from 720px to 820px to accommodate 3-column layout comfortably.

**All Tasks view (unchanged behaviour):**
- Existing task list with status/priority filter bar.
- Full inline expand/edit/archive panel on each task card.
- Default view on page load.

**`openInTable(id)` helper:** Switches to All Tasks, sets expandedId, re-renders, then scrolls the card into view with a smooth scroll after 80ms.

**State variable added:** `currentView = 'table'` — controls which render function is dispatched.

**Functions added:**
- `switchView(v)` — updates active vtab, shows/hides filter bar, dispatches render
- `openInTable(id)` — used by Calendar and Board click handlers
- `renderCurrentView()` — dispatcher that also updates the page-meta count
- `renderDueSoon()` — Due Soon logic
- `renderCalendar()`, `buildCalRow(t)` — Calendar logic
- `renderBoard()`, `buildBoardCard(t)` — Board logic

**Functions renamed/refactored:**
- `renderTasks()` → `renderTableView()` (scope-clarifying rename; same logic, now only called for All Tasks)
- All previous callers of `renderTasks()` in loadTasks, saveEdit, confirmArchive updated to call `renderCurrentView()`

**CSS added:** `.view-bar`, `.vtab`, `.board`, `.board-col`, `.board-col-header`, `.board-col-count`, `.board-cards`, `.board-card`, `.board-card-name`, `.board-card-meta`, `.board-card-due`, `.board-empty`, `.cal-group`, `.cal-group-header`, `.cal-group-count`, `.cal-task-row`, `.cal-time`, `.cal-task-name`, `.cal-chips`, `.due-section`, `.due-section-header`, `.due-section-count`

**Files changed:** `personal-dashboard.html` only
**Files NOT changed:** `proxy.py`, `index.html`, `social-dashboard.html`, `financial-dashboard.html`, `daftra-pdf-generator_1.html`, `config.json`

---

## [2026-06-10] — Personal Task Center (Phase 3)

### personal-dashboard.html (new) · index.html · social-dashboard.html

New standalone dashboard for Youssef's personal Notion task list. Separate from `social-dashboard.html` — uses a different Notion workspace, different token, different proxy route.

**Database:** "Tasks" — data source ID `3b74a590-47e4-82cb-ab74-073bb96d4cba` (in "yous moka's Space" workspace).
**Schema:** Name (title), Status (status: Not started/In progress/Done), Priority (select: High/Medium/Low), Assignee (people), Due (date — datetime with time syncs to Google Calendar via Notion), Tags (multi_select: Work/Personal/Finance/Follow-up/Important), Notes (rich_text).
**Proxy route:** `/notion/personal/` — existing route, no `proxy.py` changes needed.

**Features:**
- View switcher: Due Soon / Calendar / Board / All Tasks tabs (added in follow-up commit 2026-06-11; baseline commit has All Tasks only).
- Task list with status/priority filter bar (All Tasks view).
- Tasks sorted: active first (overdue → by date → undated), done last.
- Overdue tasks highlighted with red date and "Overdue" tag. Done tasks struck through.
- Inline expand/edit panel per task — edit Status, Priority, Due, Tags, Notes; Save changes.
- "New Task" button → inline form (name, status, priority, due datetime-local, tags, notes).
  - Creating with a date/time causes it to appear in Google Calendar automatically via Notion's sync.
  - `parent: { type: 'data_source_id', data_source_id: '3b74a590-...' }` required for multi-data-source databases.
- Archive button in edit panel with confirmation — PATCH `/notion/personal/pages/{id}` with `{"archived": true}`. Soft-delete only; recoverable from Notion Trash.
- Keyboard: Enter in name field submits; Escape closes form or collapses expanded card.

**Navigation:**
- `index.html` — new "Personal Task Center" card (Card 4), same-tab `<a href>`.
- `social-dashboard.html` topbar — "Personal Tasks" link next to "← Platform Home".
- `personal-dashboard.html` topbar — "Social Dashboard" and "← Platform Home" links.

**No changes to:** `proxy.py`, `financial-dashboard.html`, `daftra-pdf-generator_1.html`, `config.json`, tags.

---

## [2026-06-10] — Archive (Delete) Task — Social Media Control Center

### social-dashboard.html + proxy.py

Added the ability to archive (soft-delete) any task from the Social Media Control Center detail panel. Archive = Notion `archived: true`. No hard delete. Page is recoverable from Notion Trash.

**UI:**
- "Archive this task…" button at the bottom of the detail panel (red outline, small, below the Comments section).
- Clicking reveals an inline confirmation box (pink background) naming the task and warning it can only be recovered from Notion Trash.
- "Yes, archive it" (red, disables immediately on click, shows "Archiving…") and "Cancel" (restores the button).
- On success: detail panel closes, `loadAllTasks()` fires — task disappears from all views immediately.
- On error: Notion's error message shown inline; button re-enables for retry.

**Proxy route:** `PATCH /notion/social/pages/{page_id}` → `proxy.py` forwards to `https://api.notion.com/v1/pages/{page_id}` with social_media token. Body: `{"archived": true}`.

**proxy.py changes (two fixes, both approved):**

1. `do_PATCH` — updated to allow Notion routes while keeping Daftra blocked:
```python
def do_PATCH(self):
    if self.path.startswith('/daftra/'):
        self._block_daftra_write()
    else:
        route = self._notion_route()
        if route:
            self._proxy_notion(*route)
        else:
            self._block_daftra_write()
```
`/daftra/...` → 405 unconditionally. `/notion/social/` and `/notion/personal/` → proxied. Anything else → 405.

2. `_json_error` — added `Content-Length` header. Without it, PATCH error responses (e.g. 405) closed the connection before the client could read the status code. Fix applies to all error responses.

**localStorage:** Records for archived tasks in `vista_reviews_v1`, `vista_favorites_v1`, `vista_task_relations_v1` are left in place. Orphaned records are inert — the task no longer appears in `allTasks` so no visible UI issues.

**Functions added:** `showArchiveConfirm(id)`, `cancelArchive()`, `confirmArchive(id)`
**CSS added:** `.detail-delete-zone`, `.detail-delete-btn`, `.da-confirm`, `.da-confirm-btns`, `.da-confirm-yes`, `.da-confirm-no`, `.da-error`

**Files changed:** `social-dashboard.html`, `proxy.py`
**Files NOT changed:** `financial-dashboard.html`, `daftra-pdf-generator_1.html`, `index.html`, `config.json`

---

## [2026-06-10] — Create New Task — Social Media Control Center

### social-dashboard.html only — proxy.py unchanged

Hussam granted write permission on the Vista social_media Notion integration. Added the ability to create new tasks in Hussam's shared database directly from the Social Media Control Center.

**UI:**
- `+ New Task` button added to the sidebar Tasks section, below `Refresh Tasks`. Uses `btn-outline` style to distinguish it from the primary refresh action.
- Modal overlay (`#nt-overlay`) opens on click. Fields: Task name (required), Category (select), Assignee (select), Status (select, defaults to "Not started"), Due date (date), Description (textarea, optional). Two-column grid layout for Category/Assignee and Status/Due date pairs.

**Behaviour:**
- Submit button disables immediately on click to prevent duplicate submissions. Label changes to "Creating…".
- On Notion success (HTTP 201): shows green success bar with task name, button changes to "Created", modal closes after 900 ms, `loadAllTasks()` fires so the new task appears immediately.
- On Notion error (non-2xx): Notion's error message surfaces inline in the modal. Submit re-enables so the user can correct and retry without reopening.
- On network error: same inline error path.
- Escape key: closes the new task modal when it is the topmost layer (checked before the task-selector modal and the detail panel in the unified Escape handler).
- Click-outside (overlay backdrop) also closes the modal.
- Task name field receives focus automatically on open.
- Enter in the task name field submits.

**Proxy:** No changes to `proxy.py`. The existing `do_POST` → `_notion_route()` → `_proxy_notion()` path already forwards `POST /notion/social/pages` to the Notion API with the social_media token injected.

**Notion API call:**
```
POST /notion/social/pages
→ proxy forwards to https://api.notion.com/v1/pages
Body: {
  "parent": { "database_id": "35aa2557-c7f8-8140-81f5-000b067a0139" },
  "properties": {
    "Task name": { "title": [...] },
    "Status":    { "status": { "name": "Not started" } },
    "Category":  { "select": { "name": "..." } },   // omitted if blank
    "Assignee":  { "select": { "name": "..." } },   // omitted if blank
    "Due date":  { "date":   { "start": "YYYY-MM-DD" } }, // omitted if blank
    "Description": { "rich_text": [...] }            // omitted if blank
  }
}
```

**Escape key handler unified (side effect fix):**
The existing global `keydown` Escape handler previously called `closeDetail()` unconditionally. Replaced with a prioritised chain: new task modal → task-selector modal → detail panel. Each layer returns early, preventing lower layers from also firing.

**Functions added:**
- `openNewTaskModal()` — resets all fields, shows overlay, focuses task name
- `closeNewTaskModal()` — hides overlay
- `submitNewTask()` — validates, builds Notion API body, POSTs, handles success/error

**CSS added:** `.nt-overlay`, `.nt-modal`, `.nt-header`, `.nt-title`, `.nt-close`, `.nt-body`, `.nt-field`, `.nt-label`, `.nt-req`, `.nt-input`, `.nt-select`, `.nt-textarea`, `.nt-footer`, `.nt-submit`, `.nt-cancel`, `.nt-error`, `.nt-success`

**Files changed:** `social-dashboard.html`, `proxy.py` (logging crash fix only — see below)
**Files NOT changed:** `financial-dashboard.html`, `daftra-pdf-generator_1.html`, `config.json`

**proxy.py change (Windows logging crash fix):**
`log_message()` used the `→` Unicode arrow (U+2192) in a `print()` call. On Windows, Python stdout defaults to cp1252, which cannot encode U+2192. Any Notion route that returned a non-2xx HTTP response triggered this `UnicodeEncodeError` inside `send_response()` — before any response headers were sent — causing the connection to close silently with no response to the caller. Fixed by replacing `→` with `->`. No route logic, auth, CORS, or write-behavior changes.

**Live creation status — BLOCKED (Notion permissions):**
The UI is fully implemented and tested. The proxy correctly routes `POST /notion/social/pages` to Notion. Live task creation is currently blocked because Notion returns HTTP 404:

> "Could not find database with ID: 35aa2557-c7f8-8140-81f5-000b067a0139. Make sure the relevant pages and databases are shared with your integration 'Youssef'."

**Action required (Hussam):** In Notion, open the Vista tasks database → Share → add the "Youssef" integration with at least "Can edit" permission. Once done, the POST will succeed and no code changes are needed.

---

## [2026-06-09] — Invoice printing + Purchasing Invoice local file manager

### daftra-pdf-generator_1.html + proxy.py — commit `d0188c6`

#### Invoice printing

- **Print All Invoices** — generates a single combined PDF from all loaded Daftra invoices using PyMuPDF. Prior implementation used `window.open()` per invoice; browsers block rapid popup sequences after ~2 calls. Fixed with async loop + 400ms delays + blocked-popup fallback links.
- **`printAllInvoices()`** — hardened: stale container guard; extracted `total` for accurate "Preparing invoice N of M" status; 150ms inter-fetch delay; `body.printing-all` CSS class isolation; `waitForImages` resolve-not-reject fix.

#### Purchasing Invoice local file manager (new feature)

Full local file manager for `C:\Users\YousefMokaled\Documents\Vista United Co\purchasing invoices`. All logic isolated from Daftra invoice logic.

**Three-section classification:**
- **Invoices** — default; keyword-excluded files
- **Payment Slips & Receipts** — filenames containing: payment, report, receipt, transfer, bank
- **Others** — filenames containing: quotation, quote, delivery, statement, contract, agreement, or matching `\bdn\b`

**Manual tag override:**
- `localStorage` key `vista_purchasing_file_tags_v1` — schema: `{ "folder/file.pdf": "invoice"|"payment"|"other" }`
- Manual tag wins over auto-classification; stored per filename path; persists across refreshes
- Per-file `<select>` (Auto / Invoice / Payment / Other) in every file row

**Date grouping:**
- Folder names parsed as `D-M-YYYY` by `parsePurchFolderDate(folder)`
- Folders sorted newest-first (`db − da`) within each section
- Files sorted alphabetically within each folder date group
- Subheadings with folder date and per-group file count

**Invoice-only Select All:**
- Checkbox in the Invoices section header — selects/deselects all visible invoice-classified files
- Live count in parentheses next to header: `Invoices (3 selected)`

**Combined PDF (server-side):**
- `POST /purchasing-invoices/combine` — accepts `{ "paths": [...] }` JSON
- Server merges all files (PDF pages via `fitz.insert_pdf`, images via `fitz.open`) in UI order
- Returns `application/pdf` stream; no temp files written to disk
- Returns `503` with install instructions if PyMuPDF is not available
- **PyMuPDF v1.27.2.3 is required** — `pip install PyMuPDF`

**Print All Invoices (Purchasing):**
- Opens combined PDF of all invoice-classified files (excludes payment slips and others by default)
- Sorted newest-date-first then alpha within date, via `_sortPurchFilesForPrint(files)`
- Shows `#purchPrintAllLog` progress panel with blob URL on success; fallback link if popup blocked

**Print Selected:**
- Opens combined PDF of all checked files regardless of classification
- Warning shown if non-invoice files (payment slips, others) are included in the selection

**Other routes (proxy.py):**
- `GET /purchasing-invoices/list` — directory walk returning `{ folder, name, relativePath, size }` per file; `Cache-Control: no-store` to prevent browser caching stale listing
- `GET /purchasing-invoices/file?path=…` — serves a file inline with RFC 5987 Unicode `Content-Disposition` (required for Arabic filenames); three-phase response (validate → headers → stream) to avoid `ConnectionAbortedError` after headers are sent
- `POST /purchasing-invoices/upload` — multipart upload into a `D-M-YYYY` dated subfolder
- `POST /purchasing-invoices/combine` — PyMuPDF-based combined PDF (see above)

**Security:**
- `_safe_purchase_path(rel_path)` blocks path traversal (`..`), absolute paths, and anything escaping `PURCHASE_INVOICE_DIR`
- Only `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp` extensions allowed

**Bug fixes applied during testing:**
- Upload → list refresh not showing new file: two root causes — search filter still active after upload (cleared before refresh) + browser caching list endpoint (Cache-Control + `?t=Date.now()` + `{cache:'no-store'}`)
- PDF page order in combined PDF not matching UI order: extracted `parsePurchFolderDate` to module level; added `_sortPurchFilesForPrint(files)` helper; both print paths call it before `_purchCombinePrint`

**ThreadingHTTPServer:**
- `proxy.py` switched from `HTTPServer` to `ThreadingHTTPServer` to handle concurrent PDF viewer requests

**Files changed:** `daftra-pdf-generator_1.html`, `proxy.py`
**Files NOT changed:** `config.json`, `social-dashboard.html`, `financial-dashboard.html`, all invoice/quotation/delivery-note logic

---

## [2026-06-09] — Workload Estimate card: Completed This Month section added

### social-dashboard.html — feature extension

The Workload Estimate card now includes a second section: **Completed This Month**.

- Filters Done tasks whose `last_edited_time` falls in the current calendar month
- Runs the same `workloadHours()` type-based estimation used for the remaining workload
- Displays: `~36–72 hours delivered · 23 tasks` (figures vary with live data)
- Shows all-time Done count as a supporting note: `62 done total`
- Clearly labelled: *"Estimated completed effort, not a timesheet"*
- Separated from the remaining workload section by a divider line
- No new API calls, no new localStorage keys, no changes to remaining workload logic

---

## [2026-06-09] — Workload Estimate card added to Social Dashboard

### social-dashboard.html — new feature

Added a **Workload Health + Estimated Hours** card at the top of the Social Media Control Center dashboard. The card appears above the chip-strip filters and updates automatically after each task load.

**What it shows:**
- Workload health label: **Light / Moderate / Heavy / Critical**
- Estimated near-term hours range (e.g. `~46–98 hours near-term`)
- Breakdown: active count · due within 14 days · need attention · overdue · blocked · pending feedback · future scheduled

**Scoring window (near-term only):**
- Overdue active tasks — full weight
- In-progress tasks — full weight
- Pending Feedback tasks — counted as low effort
- Blocked tasks — counted as low effort, escalate health to Critical
- Tasks due within the next 14 days — full weight
- Tasks due 15+ days away — **excluded from hour estimate**, shown as "N future scheduled"
- Done tasks — excluded entirely

**Health thresholds:**
- Light: ≤15h estimated, few overdue, low attention count
- Moderate: >15h, or ≥2 overdue, or ≥4 attention tasks
- Heavy: >35h, or ≥4 overdue, or ≥8 attention tasks
- Critical: >100h, or ≥7 overdue, or ≥13 attention tasks, or any blocked task

Hour ranges estimated from task type (video, image, LinkedIn, reports, ads, admin, generic) — not pulled from any Notion field.

---

## [2026-06-08] — Media Library sort: newest post date first

### social-dashboard.html — UX fix

Media Library cards now sort **newest due date first** (top to bottom).

Previously sorted: images first, then alphabetically by task name.
Now sorted: descending by `entry.dueDate` (the task's Due date / planned post date).

- Entries with a due date appear before entries without one
- Entries with no due date fall back to alphabetical order by name
- No re-scan required — `dueDate` was already stored in the media index schema
- No change to scan logic, search, card rendering, or any other feature

---

## [2026-06-08] — Meeting Agendas / Notes full implementation

### social-dashboard.html — Meeting Agendas / Notes sidebar (full)

Replaced the "not yet accessible" placeholder with a fully functional sidebar panel.
Access confirmed — Hussam shared both Meetings Agendas and Meeting Notes pages with the Youssef integration.

**Architecture:**
- Uses `MEETING_PROXY = 'social'` (Hussam's social_media token) — distinct from task fetching which uses `personal` token
- Page IDs hardcoded as JS constants (`MEETING_AGENDA_PAGE_ID`, `MEETING_NOTES_PAGE_ID`) — same pattern as `DATA_SOURCE_ID`
- Child-page lists cached in `_meetingCache` per session; invalidated on proxy restart

**Sidebar behaviour:**
- Two tabs inside the panel: **Agendas** (4 pages) and **Notes** (7 pages)
- Each tab shows pages newest-first (array reversed from Notion's oldest-first order)
- List loaded on first panel expand; cached thereafter
- Loading spinner, empty-state message, and error message for inaccessible pages

**Content viewer:**
- Click any agenda/note title → full-width viewer replaces main content area
- Chips strip, tab row, and legend are hidden while viewer is open
- `← Back` button restores previous state via `closeMeetingViewer()` → `switchTab(currentTab)`
- Page title rendered in Cormorant Garamond italic heading

**Block renderer (`renderNotionBlocks`):**
Handles all block types confirmed present in the Notion pages:
- `heading_2` → `<h2 class="nb-h2">`
- `heading_3` → `<h3 class="nb-h3">`
- `paragraph` → `<p class="nb-p">`
- `bulleted_list_item` → `<ul class="nb-ul">` (consecutive items grouped)
- `numbered_list_item` → `<ol class="nb-ol">` (consecutive items grouped)
- `divider` → `<hr class="nb-divider">`
- `callout` → `<div class="nb-callout">` with emoji icon + styled body
- Unknown block types silently skipped (graceful)

**Rich text renderer (`renderRichText`):**
- Inline links (`r.href`) rendered as `<a class="nb-link">`
- Plain URLs auto-linked via regex
- Bold → `<strong class="nb-bold">`, Italic → `<em class="nb-italic">`, Code → `<code class="nb-code">`

**Files changed:** `social-dashboard.html` only
**config.json:** not modified (page IDs already present from previous session)

---

## [2026-06-08] — Favorites chip + Meeting Agendas sidebar placeholder

### social-dashboard.html — Favorites (Phase 2A.6)

New **Favorites** feature — localStorage only, no Notion write-back.

**localStorage key:** `vista_favorites_v1` — stores `{ favoritedAt, taskName }` per task ID.

**New functions:**
- `FAVORITES_KEY`, `loadFavorites()`, `saveFavorites(data)`
- `isFavorite(t)` — true if task has a favorites record
- `addFavorite(id, name)`, `removeFavorite(id)`, `toggleFavoriteAction(id)`
- `favoriteSectionHTML(t)` — renders gold "★ Favorited" bar or "☆ Add to Favorites" button in detail panel

**UI changes:**
- `Favorites` chip added to quick-filter strip beside `Reviewed` — amber/gold color scheme
- Live count `· N` on chip; updates after every toggle
- `☆`/`★` inline star toggle on every task row — uses `event.stopPropagation()` so it does not open the detail panel
- Gold `★ Favorite` badge in task row badge column when favorited
- `☆ Add to Favorites` button / `★ Favorited · date` bar in task detail panel
- Empty state: "No favorited tasks match the current filters."

**Filter behaviour:**
- Done gate in `getFilteredTasks()` exempted for `activeChip === 'favorites'` (same pattern as Reviewed)
- Category, Assignee, and Search filters apply inside Favorites chip
- Include Done does not hide favorited Done tasks
- Favorites persist across normal browser refreshes; Incognito always starts empty (localStorage isolated)

**Unchanged:** Reviewed logic, `attentionFilter`, `isReviewedAndFresh/ByMe/Stale`, Media Library, proxy, Related Tasks.

### social-dashboard.html — Meeting Agendas sidebar placeholder

New **Meeting Agendas** collapsible section added to the left sidebar (Option A — no new main tab).

**Investigation findings (confirmed by live Notion API queries):**
- Meeting agendas are stored as child pages under "Meetings Agendas" (4 pages) and "Meeting Notes" (7 pages) — both child pages of the Vista United workspace root
- They are **not** in a database — they are freeform Notion pages
- Individual agenda pages return empty content — the Youssef integration does not have access to read them
- Requires Hussam to share these pages with the Youssef connection via Notion → ••• → Add connections

**Implementation:**
- Collapsible sidebar panel (toggle ▶/▼) with a clear "not yet accessible" notice
- Dashboard does not crash if `meeting_agenda_page_id` / `meeting_notes_page_id` are absent from config
- No new Notion API calls; no new proxy routes; no application state affected

### config.example.json

Added two optional fields under `notion.social_media`:
- `meeting_agenda_page_id` — optional, leave blank until Hussam shares the page
- `meeting_notes_page_id` — optional, leave blank until Hussam shares the page

---

## [2026-06-07] — Financial Dashboard — feature branch complete

**Branch:** `feature/financial-dashboard` — rebased onto `stable-reviewed-history`.

### Commits (oldest → newest)

| Hash | Message |
|---|---|
| `8deefc4` | Add read-only Daftra proxy route |
| `af1e6d3` | Create Financial Dashboard shell |
| `24b3924` | Add Financial Dashboard calculations |
| `8ae3ec9` | Add Financial Dashboard pagination |
| `553ff28` | Add Financial Dashboard monthly reports |
| `8013208` | Separate personal transfers from business calculations |
| `e733ad0` | Fix Personal Transfers reference numbers |
| `0bc03db` | Add Financial Dashboard card to homepage |

---

### proxy.py — read-only Daftra proxy route (commit `8deefc4`)

Added `/daftra/...` GET-only proxy route. Browser calls `/daftra/{path+querystring}`; proxy strips the prefix, injects `APIKEY` from `config.json → daftra.api_key`, forwards to `https://{subdomain}.daftra.com/api2/...`, and returns the JSON response. POST, PUT, PATCH, DELETE blocked with 405. `config.example.json` updated with `daftra.subdomain` and `daftra.api_key` placeholder fields.

---

### financial-dashboard.html — new module (commits `af1e6d3` through `e733ad0`)

**Two period-independent top cards:**

- **Yellow — Estimated Profit Tax Payable End of Year**
  Always YTD bounds, regardless of sidebar period selector.
  `taxReserve = Math.max(ytdProfit, 0) × 0.20`
  Subtitle hardcoded: `20% of estimated business profit · management estimate`

- **Red — VAT Reconciliation — {current quarter} So Far**
  Always current Gregorian quarter bounds (Q1=Jan–Mar, Q2=Apr–Jun, Q3=Jul–Sep, Q4=Oct–Dec).
  `vatBalance = outputVAT − inputVAT(purchases) − inputVAT(expenses)`

**Period selector (sidebar):**
Year to Date · This Month · Last Month · Q1 · Q2 · Q3 · Q4 · All Time.
Changing the period affects panels, monthly chart, and monthly table. Never affects the two top cards.

**Personal Transfers section (commit `8013208`):**
Purchase invoices where `supplier_business_name.trim().toLowerCase() === 'personal transfer'` are excluded from all business calculations (profit, VAT, period panels, monthly chart). Shown in a separate purple-tinted panel. Pre-filtered at the top of `renderContent()` before any calculation; `bizPurRecords` used everywhere else.

**Purchase invoice reference field fix (commit `e733ad0`):**
`r.number || r.id || '—'` → `r.no || r.number || r.id || '—'`
Daftra's formatted purchase invoice number is in `r.no` (e.g. `000048`). Without the fix, the Personal Transfers table showed raw DB integers.

**VAT derivation:** `summary_total − summary_subtotal` for all three record types. `summary_tax1` is always null; never used.

**No auto-fetch:** Zero `DOMContentLoaded` / `setInterval` / `setTimeout` data-fetch triggers. Manual Fetch Data button only.

**No localStorage / sessionStorage:** Zero client-side storage. Every fetch starts fresh.

**Pagination:** `limit=100&page=N` on all three endpoints, fetching until no more records.

**Sample regression figures (live data, branch testing 2026-06-07 — management estimate, not official):**

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

### index.html — Financial Dashboard homepage card (commit `0bc03db`)

Replaced the disabled Future Tools placeholder (card 3) with a live Financial Dashboard card.
- Title: `Financial Dashboard` / `لوحة المالية`
- Description: `Live Daftra data — sales vs purchases vs expenses. VAT reconciliation for the current quarter. Estimated profit tax payable end of year.`
- Status badge: `Live` (green `status-live`)
- Link: `financial-dashboard.html` (relative)
- Icon: rising line chart with baseline

Document Generator and Social Media Control Center cards are unchanged.

---

## [2026-06-07] — Platform workflow rules added to documentation

### CLAUDE_CONTEXT.md, docs/CHATGPT_HANDOFF.md, docs/roadmap.md

Added permanent workflow rules to all three documentation files. No application code changed.

**Rules added:**
- Git workflow order: validate locally → document → commit → push after approval
- Push safety: never push `config.json`, never force-push, never push `feature/financial-dashboard` without explicit approval
- Branch assignments: `stable-reviewed-history` is the approved Social Media dashboard branch; `stable-reviewed-history-v1` is the restore tag
- localStorage behaviour: reviewed task history is stored in normal-browser localStorage only; Incognito always shows empty history; F5 refresh does not delete reviewed tasks

---

## [2026-06-05] — Reviewed history: permanent records, Updated Since Review badge

### social-dashboard.html — Reviewed chip corrected to permanent history

**Problem corrected:** Previously, tasks disappeared from the Reviewed chip when:
1. Their Notion status was changed to Done (updating `last_edited_time`, making the review stale).
2. Any Notion edit after the review caused `isReviewedAndFresh` to return false.

**Correct behaviour now:**

- A reviewed task stays in Reviewed until the user manually clicks **Remove Review** — no Notion edit or status change can remove it.
- Done tasks are always visible under Reviewed regardless of the Include Done toggle.
- If a task is edited in Notion after being reviewed, it shows an amber **Updated Since Review** badge in the task row and a highlighted note in the detail panel review bar. It may also return to Needs My Attention (via `isReviewedAndFresh → false`), but it remains in Reviewed.

**New helper functions:**
- `isReviewedByMe(t)` — true if any review record exists, regardless of staleness. Used by: Reviewed chip filter, chip count, task row badge, `reviewSectionHTML`.
- `isReviewedStale(t)` — true if reviewed but edited in Notion after the review. Used by: "Updated Since Review" badge in task row and detail panel.
- `isReviewedAndFresh(t)` — unchanged, still used only by `attentionFilter` to return stale tasks to Needs My Attention.

**Filter changes (`getFilteredTasks`):**
- Done gate (`!showDone && st === 'Done'`) now skipped when `activeChip === 'reviewed'`.
- Reviewed chip now tests `isReviewedByMe(t)` instead of `isReviewedAndFresh(t)`.

**CSS added:**
- `.b-stale` — amber badge (background `#FEF9EC`, color `#B07A20`, border `#E8D4A0`).
- `.review-bar-stale` — amber tint on the detail panel review bar when stale.

**Unchanged:** `attentionFilter`, `isReviewedAndFresh`, Mark Reviewed / Remove Review flows, `vista_reviews_v1` schema, all other filters, Media Library, proxy, Notion fetching.

---

## [2026-06-05] — Reviewed quick-filter chip

### social-dashboard.html — Reviewed chip added to quick-filter strip

New **Reviewed** chip placed immediately after the Overdue chip in the quick-filter row. No new main tab created.

**Behaviour:**
- Clicking the chip switches to the Task Tracker view and shows only tasks where `isReviewedAndFresh(task)` returns true.
- Task rows display exactly as in the normal Task Tracker: full task name, `✓ Reviewed` badge, status, category, assignee, due date, and media indicators.
- Search, Category filter, Assignee filter, and Include Done toggle all apply normally while the chip is active.
- Marking a task reviewed causes it to appear in the Reviewed view on the next filter pass.
- Removing a review causes the task to leave the Reviewed view immediately.
- Stale reviews (task edited in Notion after being reviewed) are automatically excluded — `isReviewedAndFresh` returns false and the task disappears from the Reviewed view without any manual action.
- Clicking any other chip or All deactivates the Reviewed chip.
- Empty state: "No reviewed tasks match the current filters."

**Chip count:**
- The chip label shows a live count: `Reviewed · N`.
- The count reflects all fresh reviewed tasks across the full task list, computed in `applyFilters()` after every filter/render cycle.
- ⚠ **Known behaviour to test:** the count is calculated globally (all fresh reviewed tasks) and may differ from the number of rows visible when Category, Assignee, or Include Done filters are applied. This difference has not been observed to be confusing in practice, but must be tested before deciding whether to change it. No implementation change made yet.

**CSS added:** `.chip-reviewed:hover`, `.chip-reviewed.active` (green `#4A7A52`, matching the `✓ Reviewed` badge colour).

**HTML added:** One chip button `id="chip-reviewed"` with inline `<span id="chip-reviewed-count">` for the live count.

**JS changes (4 targeted lines):**
- `getFilteredTasks()` — `activeChip === 'reviewed'` check added inside the `else` branch, so Category and Assignee sidebar filters continue to apply after it.
- `renderTracker()` — `reviewedEmpty` variable: shows the empty-state message only when `activeChip === 'reviewed'` and `tasks.length === 0`.
- `applyFilters()` — computes `reviewedCount` from `allTasks.filter(isReviewedAndFresh).length` and writes it to `#chip-reviewed-count` after every render.

**Unchanged:** Needs My Attention logic, `attentionFilter()`, `isReviewedAndFresh()`, Mark Reviewed / Remove Review flows, Task Tracker layout, Media Library, `vista_reviews_v1` schema, proxy, Notion fetching.

---

## [2026-06-05] — Phase 2A.5: Related Supporting Tasks — Complete

### social-dashboard.html — Related Supporting Tasks: 5-tier detection system

> **Documentation note:** An earlier draft of this entry described "two signals only" and listed `RELATED_STOP` as removed. That was inaccurate. The approved final implementation is a 5-tier system. `RELATED_STOP`, `sigWords()`, and `sigBigrams()` are active in the code. Only the old functions `sigTaskWords()` and `findRelatedByDescription()` were removed. See `CLAUDE_CONTEXT.md` "APPROVED AND FINAL" for the authoritative description.

**What was actually built and validated:**

| Tier | Label | Type | Logic |
|---|---|---|---|
| 5 | Linked by You | Manual | User-created link stored in `vista_task_relations_v1`. Bidirectional. |
| 4 | Explicit Notion Link | Automatic | `notionLinksFromBlocks()` — `app.notion.com/p/` URL in task body blocks. |
| 3 | Exact Reference | Automatic | Full task name (lowercase) is substring of other task's description, or vice versa. |
| 2 | Strong Match | Automatic | Same category + shared bigram OR 3+ shared sig words; OR cross-category + shared bigram. |
| 1 | Possible Match | Automatic | Same category + 2+ shared significant words from task names. |

**Old functions removed (caused false positives):**
- `sigTaskWords(name)` — replaced by `sigWords(text)` + `sigBigrams(text)`
- `findRelatedByDescription(taskId)` — replaced by `findRelatedTasks(currentTaskId, topBlocks)`

**New functions added:**
- `RELATED_STOP` — stop-word set for Tiers 1–3
- `sigWords(text)`, `sigBigrams(text)` — significant word and bigram extraction
- `findRelatedTasks(currentTaskId, topBlocks)` — all 5 tiers, sorted, capped at 5
- `loadRelations()`, `saveRelations(data)`, `saveRelation(a,b)`, `removeRelation(a,b)`, `getManualRelatedIds(taskId)` — Tier 5 localStorage store
- `removeManualRelation(currentTaskId, relatedTaskId)` — removes relation, re-renders section
- `_refreshRelatedSectionFull(taskId)`, `_lastTopBlocks` — re-render without re-fetch
- `openTaskSelector(currentTaskId)`, `closeTaskSelector()`, `renderTaskSelectorList(query)`, `selectRelatedTask(id)` — manual-link modal

**Functions changed:**
- `buildRelatedTasksHTML(related, currentTaskId)` — renders tier label ("Linked by You", "Explicit Notion Link", "Exact Reference", "Strong Match", "Possible Match"); Remove link only on Tier 5
- `loadDetailMedia` — uses `getManualRelatedIds` + `notionLinksFromBlocks` + `findRelatedTasks`; no `findRelatedByDescription` call

**HTML changes:**
- `#detail-related-wrapper` — `display:none` removed; always visible; "+ Link Supporting Task" button added inside
- `#ts-overlay` modal — added before `</body>`; searchable task list, Escape to close, click-outside to close

**CSS added:** `.related-task-footer`, `.related-task-remove`, `.link-task-btn`, `.ts-overlay`, `.ts-modal`, `.ts-header`, `.ts-title`, `.ts-close`, `.ts-search-wrap`, `.ts-search`, `.ts-list`, `.ts-item`, `.ts-item-name`, `.ts-item-meta`, `.ts-item-badge`, `.ts-item-type`, `.ts-empty`

**localStorage schema — `vista_task_relations_v1`:**
```json
{ "taskAId": [{ "relatedTaskId": "taskBId", "createdAt": 1748000000000 }] }
```
Both directions stored. No Notion write permissions required.

**Validated (2026-06-05):**
- False positive eliminated: "Research tote bag demand in KSA" scores 0 against "Ad keyword research (not final)" — different categories, only 1 shared word, below threshold.
- Same-category tasks sharing 3+ significant words correctly surface as Strong Match.
- Manual link between "Tag keywords as positive or negative" ↔ "Ad keyword research (not final)" confirmed bidirectional.

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
