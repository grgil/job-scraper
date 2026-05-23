# Health Job Scraper

Daily and weekly job alert scraper covering 14 health system portals across Workday, Phenom, iCIMS, and DirectEmployers (Jobsyn) ATS platforms. Uses Playwright (headless Chromium) for JavaScript-rendered pages. Runs automatically via GitHub Actions and sends targeted email digests by market.

---

## Email digests

| Digest | Orgs |
|--------|------|
| **Regional** | VCU Health, UVA Health, Bon Secours, Carilion Clinic, Prisma Health (Greenville), Wellstar (Atlanta), Atrium Health (Charlotte), Emory Healthcare (Atlanta) |
| **Remote** | Duke Health, MUSC, VUMC, Prisma Health, Emory Healthcare, Ascension |
| **Payer** | Humana, Elevance, Cigna, Solventum, Veradigm, Waystar *(commented out — activate when ready)* |

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
3. Set `email_bucket` to `"regional"`, `"remote"`, or `"payer"`
4. Set `remote_only: True` or `location_keywords: {...}` as needed

**Decision framework:**
- Payer/vendor org → `email_bucket: "payer"`, `remote_only: True`, comment out until activated
- Health system in a target metro → `email_bucket: "regional"`, `location_keywords`
- Health system outside target metros, remote-friendly → `email_bucket: "remote"`, `remote_only: True`
- Spans both → two entries (one per bucket, same URL)

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

Stop reason values:
| Value | Meaning |
|-------|---------|
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

**New ATS / selectors changed** — run `inspect_selectors.py` with the portal URL to audit selectors and XHR patterns before editing the scraper.
