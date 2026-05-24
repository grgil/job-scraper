# Health Job Scraper

Daily and weekly job alert scraper covering health system portals across Workday, Phenom People, iCIMS, and DirectEmployers (Jobsyn) ATS platforms. Uses Playwright (headless Chromium) for JavaScript-rendered pages. Runs automatically via GitHub Actions.

---

## Email digests

| Digest | Contents |
|--------|----------|
| **Main** | All active sites — one digest regardless of remote or on-site scope |
| **Payer** | Humana, Elevance, Cigna, Solventum, Veradigm, Waystar *(commented out — activate when ready)* |

`email_bucket` on each site config is `"main"` or `"payer"`. `remote_only=True` controls scraping behavior; `email_bucket` controls which digest the results route to.

### Active sites

| Site | ATS | Access method | Market |
|------|-----|--------------|--------|
| UVA Health | Phenom | DOM + JSON-LD | Richmond regional |
| VCU Health | Phenom | DOM + JSON-LD | Richmond regional |
| Duke Health | Phenom | DOM + JSON-LD | Remote only |
| Bon Secours | Workday | CXS intercept | Richmond regional |
| Carilion Clinic | Workday | CXS intercept | Richmond regional |
| Prisma Health (Greenville) | Workday | CXS intercept | Greenville SC regional |
| Wellstar Health | Workday | CXS intercept | Atlanta regional |
| Atrium Health | Workday | CXS intercept | Charlotte regional |
| MUSC | Workday | CXS intercept | Remote only |
| VUMC | Workday | CXS + detail pages | Remote only |
| Sentara | Workday | CXS + detail pages | Remote only |
| Prisma Health (Remote) | Workday | CXS + detail pages | Remote only |
| Ascension | iCIMS | Card-embedded | Remote only |
| Emory Healthcare (Atlanta) | Jobsyn | API intercept | Atlanta regional |
| Emory Healthcare (Remote) | Jobsyn | API intercept | Remote only |

**Access method key**

| Method | How it works |
|--------|-------------|
| DOM + JSON-LD | Playwright reads job links from page DOM; visits each detail page for JSON-LD structured data. Used for all Phenom sites (no XHR API available). |
| CXS intercept | Intercepts Workday's `/wday/cxs/` XHR response — returns `jobPostings[]` with title, URL, date, and `locationsText` in a single batch. No detail page visits needed. Falls back to DOM + JSON-LD if the intercept misses. |
| CXS + detail pages | Same CXS intercept, but always visits each job's detail page afterward. Required for `remote_only=True` sites because `locationsText` in the CXS response reflects the office address, not the work arrangement — only JSON-LD reliably exposes remote/hybrid status. |
| Card-embedded | Newer iCIMS portals (Ascension) embed date and location directly in listing cards. No detail page visits. |
| API intercept | Intercepts the Jobsyn `prod-search-api.jobsyn.org` search response — returns full job records (title, date, location) as JSON. No page visits beyond the initial load. |

---

## Setup

### 1. Install dependencies

```
pip install -r requirements.txt
playwright install chromium --with-deps
```

### 2. Configure secrets

Copy `.env.example` to `.env` and fill in your values:

```
EMAIL_FROM=your-gmail@gmail.com
EMAIL_TO=where-alerts-go@example.com
EMAIL_APP_PASS=xxxx xxxx xxxx xxxx
```

`EMAIL_APP_PASS` must be a [Gmail App Password](https://myaccount.google.com/apppasswords) — regular passwords are blocked by Google's SMTP.

### 3. Run locally

```
python scraper.py               # daily run (today's new jobs)
python scraper.py --weekly      # weekly recap (primary-only, 7-day lookback)
python scraper.py --no-email    # write preview_*.html instead of sending
python scraper.py --since YYYY-MM-DD  # override the since-date
```

---

## GitHub Actions

| Workflow | Schedule | Trigger |
|----------|----------|---------|
| `scraper.yml` | Daily **6:00 AM UTC** (2 AM EDT) | `workflow_dispatch` |
| `scraper_weekly.yml` | Sunday **7:00 AM UTC** (3 AM EDT) | `workflow_dispatch` |

The weekly run uses `--weekly`: 7-day lookback, primary-only scoring, sends a `[Job Alert [Recap]]` digest. It runs one hour after the daily cron on Sundays to avoid `seen_jobs.json` push conflicts.

### Required repository secrets

| Secret | Value |
|--------|-------|
| `EMAIL_FROM` | sending Gmail address |
| `EMAIL_TO` | recipient address |
| `EMAIL_APP_PASS` | 16-character Gmail App Password |

### State persistence

`seen_jobs.json` is committed back to the repo after each run by the workflow bot. This deduplicates jobs across runs and prevents batch-refresh floods (e.g. Workday portals that reset all `datePosted` fields nightly). The first run after setup will include all currently active jobs; subsequent runs show only new ones.

The file uses a discriminated union schema — values are either a plain date string (secondary job or pre-schema entry) or a metadata object (primary job):

```json
{
  "https://url-secondary": "2026-05-21",
  "https://url-primary": {
    "first_seen": "2026-05-21",
    "title": "Clinical Data Analyst",
    "site": "UVA Health"
  }
}
```

Primary metadata is used by the weekly recap to recover jobs the rescrape may have missed (e.g. listings that expired mid-week). Both value types are pruned at 45 days.

---

## Adding a new org

1. Run `inspect_selectors.py` to identify the ATS platform and selectors
2. Add an entry to the appropriate list in `scraper.py`:
   - Workday → `WORKDAY_SITES`
   - Phenom → `SITES`
   - iCIMS → `ICIMS_SITES`
   - DirectEmployers/Jobsyn → `EMORY_SITES`
3. Set `email_bucket` to `"main"` or `"payer"`
4. Set `remote_only=True` or `location_keywords={...}` as needed

**Decision framework:**

| Org type | `remote_only` | `location_keywords` | `email_bucket` | Notes |
|----------|--------------|---------------------|----------------|-------|
| Payer / vendor | `True` | — | `"payer"` | Comment out until payer digest is activated |
| Health system in target metro | `False` | city set | `"main"` | URL location filter + keywords = two-pass |
| Health system outside target metros | `True` | — | `"main"` | remote_only handles the filtering |
| Spans both | — | — | `"main"` | Two entries: one with location_keywords, one with remote_only=True |

---

## URL-level filtering

Each site optionally pre-filters results at the portal URL before the scraper applies its own post-filters (`remote_only`, `location_keywords`). Two filter types are used:

**Location type** — platform-native location IDs extracted from the Workday or iCIMS UI. Exact match against a specific location entity; stable long-term.

**Keyword search** — `?q=` parameter matching text in job title or description. Used only when no location-type filter was available for a given tenant. Fuzzier — may surface false positives (e.g. "Remote Patient Monitoring Tech" matching `?q=remote`); `remote_only=True` catches these in the post-filter.

| Site | URL filter | Type |
|------|-----------|------|
| UVA Health, VCU Health, Duke Health | `?sortBy=postingdate&descending=true` | Sort only |
| Bon Secours, Carilion | none | — |
| Prisma Health (Greenville) | `?primaryLocation=…` ×12 | Location type — Workday location IDs for Greenville area |
| Wellstar Health (Atlanta) | `?locations=…` ×7 | Location type — Workday location IDs for Atlanta metro |
| Atrium Health (Charlotte) | `?locationRegionStateProvince=…&locations=…` ×24 | Location type — NC state + Charlotte-area location IDs |
| MUSC | `?locationHierarchy1=…` | Location type — Workday hierarchy ID for remote locations |
| VUMC | `?remoteType=…` ×2 | Location type — Workday native remote/hybrid category IDs |
| Sentara | `?q=remote` | Keyword search |
| Prisma Health (Remote) | `?q=remote` | Keyword search |
| Ascension | `?searchLocation=--Remote` | Location type — iCIMS native remote location value |
| Emory Healthcare | none | — (post-filter via `location_keywords` or `remote_only`) |

**Note on Workday CXS sort:** Workday's CXS API (the XHR endpoint the scraper intercepts) does not expose a sort-by-date parameter. Injecting a `"sort"` field into the POST body is silently ignored. Date ordering relies on the `consecutive_empty` stopping mechanism — the scraper stops after 5 consecutive pages with no in-window jobs.

---

## Retention

| File | Retention | Notes |
|------|-----------|-------|
| `scraper.log` | 14 days (auto-trimmed on startup) | gitignored; local only |
| `seen_jobs.json` | 45 days per entry (auto-pruned) | committed to repo |
| GitHub Actions run logs | 90 days (GitHub default) | full stdout captured per run |

---

## Performance report

```
python perf_report.py            # most recent run
python perf_report.py --run 2    # second-most-recent run
```

Prints a per-site table with columns: elapsed seconds, qualifying count, skipped count, stop reason, and freshness.

| Stop reason | Meaning |
|-------------|---------|
| `no_more_pages` | Paginated to natural end of results |
| `consecutive (pN)` | 5 consecutive pages with 0 in-window jobs, stopped at page N |
| `max_pages (pN)` | Hit the configured page cap at page N |
| `batch_refresh` | Workday portal served all-same-date results (refresh artifact) |
| `sort_collapsed` | Sort order failed server-side; results unreliable |

Requires at least one run after the timing instrumentation was added (May 2026).

---

## Troubleshooting

**SMTPAuthenticationError** — regenerate the Gmail App Password; they can silently expire.

**No jobs / 0 results** — run locally and watch output. If `WARN — batch refresh suspected` appears, the portal reset all posting dates; the seen-jobs cache will handle deduplication on the next run.

**JSON-LD missing** — some ATS versions don't embed structured data on every detail page. Jobs are skipped and counted in the `skipped` total logged per site.

**New ATS / selectors changed** — run `inspect_selectors.py` with the portal URL to audit selectors and XHR patterns before editing the scraper. Previously investigated orgs that are blocked or require custom scrapers are documented in the `inspect_selectors.py` header.

**Workday location IDs changed** — the opaque hash IDs in `?locations=`, `?primaryLocation=`, `?remoteType=` etc. are extracted from the Workday UI facet URLs. If a site stops returning the expected location-filtered results, re-inspect the portal, apply the filters manually in the browser, and extract the updated IDs from the URL.
