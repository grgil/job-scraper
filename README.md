# Health Job Scraper

A personal job search automation tool built to solve a real problem: health system careers pages don't have good cross-site alerting, and manually checking 15 portals daily isn't realistic. The scraper monitors those portals across five ATS platforms (Workday, Phenom People, iCIMS, DirectEmployers, Infor CloudSuite), filters out clinical and non-target roles, and delivers a daily digest email — running unattended on a GitHub Actions cron schedule.

Built with Claude (Anthropic) as a coding collaborator. I drove the requirements, design decisions, and debugging; Claude handled implementation I didn't yet have fluency in. The goal was a working tool I understood well enough to maintain and extend independently.

---

## How it works

**Access method key**

| Method | How it works |
|--------|-------------|
| DOM + JSON-LD | Playwright reads job links from page DOM; visits each detail page for JSON-LD structured data. Used for all Phenom sites (no XHR API available). Category facet filters are applied by clicking `[data-ph-at-id="facet-results-item"]` elements before link extraction — Phenom stores these as `selected_fields.category[]` in a POST body, not URL params. |
| CXS intercept | Intercepts Workday's `/wday/cxs/` XHR response — returns `jobPostings[]` with title, URL, date, and `locationsText` in a single batch. No detail page visits needed. Falls back to DOM + JSON-LD if the intercept misses. |
| CXS + detail pages | Same CXS intercept, but visits each job's detail page for the real `datePosted`. Required for MUSC and VUMC (`wd1` Workday tenant) which omit `postedOn` from CXS responses entirely. |
| Card-embedded | Newer iCIMS portals (Ascension) embed date and location directly in listing cards. No detail page visits. |
| API intercept | Intercepts the Jobsyn `prod-search-api.jobsyn.org` search response — returns full job records (title, date, location) as JSON. No page visits beyond the initial load. |
| Direct JSON fetch | Used for UNC Health (Infor CloudSuite / Landmark). One `page.goto()` bootstraps an anonymous SSO session, then `page.request.get()` calls the `JobPosting.SearchForJobsResults` list endpoint directly (cursor-paginated via `pagingInfo.fk`/`lk`) — no DOM parsing or XHR interception needed. Title, category, location, requisition ID, and posting date all come from the list response; no detail-page visits required. Category filtering is done client-side against the `Category` field rather than replaying the portal UI's session-based filter. |

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
python scraper.py --no-email    # write preview_*.html instead of sending
python scraper.py --since YYYY-MM-DD  # override the since-date
```

---

## GitHub Actions

| Workflow | Schedule | Trigger |
|----------|----------|---------|
| `scraper.yml` | Daily **6:00 AM UTC** (2 AM EDT) | `workflow_dispatch` |

### Required repository secrets

| Secret | Value |
|--------|-------|
| `EMAIL_FROM` | sending Gmail address |
| `EMAIL_TO` | recipient address |
| `EMAIL_APP_PASS` | 16-character Gmail App Password |

### State persistence

`seen_jobs.json` is committed back to the repo after each run by the workflow bot. This deduplicates jobs across runs and prevents batch-refresh floods (e.g. Workday portals that reset all `datePosted` fields nightly). The first run after setup will include all currently active jobs; subsequent runs show only new ones.

Each entry maps a job URL (or a stable Workday `domain:reqid` key) to the ISO date it was first seen:

```json
{
  "https://url-example": "2026-05-21"
}
```

Entries are pruned at 45 days. (Older entries may still contain a metadata object instead of a plain date string — a leftover from a since-removed feature; both formats are read fine, but new entries are always plain strings.)

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


---

## Troubleshooting

**SMTPAuthenticationError** — regenerate the Gmail App Password; they can silently expire.

**No jobs / 0 results** — run locally and watch output. If `WARN — batch refresh suspected` appears, the portal reset all posting dates; the seen-jobs cache will handle deduplication on the next run.

**JSON-LD missing** — some ATS versions don't embed structured data on every detail page. Jobs are skipped and counted in the `skipped` total logged per site.

**New ATS / selectors changed** — run `inspect_selectors.py` with the portal URL to audit selectors and XHR patterns before editing the scraper. Previously investigated orgs that are blocked or require custom scrapers are documented in the `inspect_selectors.py` header.

**Workday jobFamilyGroup IDs changed** — the opaque hash IDs in `?jobFamilyGroup=` params are extracted from the Workday portal UI facet URLs. If a site stops returning expected results, re-inspect the portal, apply the job family filters manually in the browser, and extract the updated IDs from the URL.
