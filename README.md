# Health Job Scraper

A personal job search automation tool built to solve a real problem: health system careers pages don't have good cross-site alerting, and manually checking 14 portals daily isn't realistic. The scraper monitors those portals across four ATS platforms (Workday, Phenom People, iCIMS, DirectEmployers), filters out clinical and non-target roles, scores matches by relevance, and delivers a daily digest email — running unattended on a GitHub Actions cron schedule.

Built with Claude (Anthropic) as a coding collaborator. I drove the requirements, design decisions, and debugging; Claude handled implementation I didn't yet have fluency in. The goal was a working tool I understood well enough to maintain and extend independently.

---

## Email digests

| Digest | Contents |
|--------|----------|
| **Main** | All active sites — one digest regardless of remote or on-site scope |
| **Payer** | Humana, Elevance, Cigna, Solventum, Veradigm, Waystar *(commented out — activate when ready)* |

`email_bucket` on each site config is `"main"` or `"payer"` and controls which digest the results route to. `remote_only=True` and `location_keywords` are post-scrape filters; `categories` (Phenom) and URL-level params (Workday/iCIMS) are pre-scrape filters.

### Active sites

| Site | ATS | Access method | Filter |
|------|-----|--------------|--------|
| UVA Health | Phenom | DOM + JSON-LD | Category facet (5 categories) |
| VCU Health | Phenom | DOM + JSON-LD | Category facet (6 categories) |
| Duke Health | Phenom | DOM + JSON-LD | Category facet (4 categories) |
| Bon Secours | Workday | CXS intercept | None — full site |
| Carilion Clinic | Workday | CXS intercept | `jobFamilyGroup` ×4 |
| Prisma Health | Workday | CXS intercept | `jobFamilyGroup` ×4 |
| Wellstar Health | Workday | CXS intercept | `jobFamilyGroup` ×7 |
| Atrium Health | Workday | CXS intercept | `jobFamilyGroup` ×8 |
| MUSC | Workday | CXS + detail pages | `jobFamily` ×8 |
| VUMC | Workday | CXS + detail pages | `jobFamilyGroup` ×5 |
| Sentara | Workday | CXS intercept | `jobFamilyGroup` ×4 |
| Shepherd Center | Workday | CXS intercept | None — small site |
| Ascension | iCIMS | Card-embedded | `searchCategory` ×3 |
| Emory Healthcare | Jobsyn | API intercept | None — full site |

**Access method key**

| Method | How it works |
|--------|-------------|
| DOM + JSON-LD | Playwright reads job links from page DOM; visits each detail page for JSON-LD structured data. Used for all Phenom sites (no XHR API available). Category facet filters are applied by clicking `[data-ph-at-id="facet-results-item"]` elements before link extraction — Phenom stores these as `selected_fields.category[]` in a POST body, not URL params. |
| CXS intercept | Intercepts Workday's `/wday/cxs/` XHR response — returns `jobPostings[]` with title, URL, date, and `locationsText` in a single batch. No detail page visits needed. Falls back to DOM + JSON-LD if the intercept misses. |
| CXS + detail pages | Same CXS intercept, but visits each job's detail page for the real `datePosted`. Required for MUSC and VUMC (`wd1` Workday tenant) which omit `postedOn` from CXS responses entirely. |
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
