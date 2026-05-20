# Health Job Scraper

Daily job alert scraper covering 19 health system and payer portals across Workday, Phenom, iCIMS, and DirectEmployers (Jobsyn) ATS platforms. Uses Playwright (headless Chromium) for JavaScript-rendered pages. Runs automatically via GitHub Actions and sends targeted email digests by market.

---

## Email digests

| Digest | Orgs |
|--------|------|
| **Regional** | VCU Health, UVA Health, Bon Secours, Carilion Clinic, Duke (Lake Norman), Atrium Health, Emory (Atlanta), Prisma Health (Greenville), Wellstar (Atlanta), Ascension (Regional) |
| **Remote** | MUSC, Duke, VUMC, WVU Medicine, OhioHealth, Emory (Remote), Prisma Health (Remote), Wellstar (Remote), Ascension (Remote) |
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
python scraper.py
```

---

## GitHub Actions

The workflow at `.github/workflows/scraper.yml` runs at **6:00 AM UTC** (2 AM EDT) daily and can also be triggered manually from the Actions tab.

### Required repository secrets

| Secret | Value |
|--------|-------|
| `EMAIL_FROM` | sending Gmail address |
| `EMAIL_TO` | recipient address |
| `EMAIL_APP_PASS` | 16-character Gmail App Password |

### State persistence

`seen_jobs.json` is committed back to the repo after each run by the workflow bot. This deduplicates jobs across runs and prevents batch-refresh floods (e.g. Workday portals that reset all `datePosted` fields nightly). The first run after setup will include all currently active jobs; subsequent runs show only new ones.

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

## Troubleshooting

**SMTPAuthenticationError** — regenerate the Gmail App Password; they can silently expire.

**No jobs / 0 results** — run locally and watch output. If `WARN — batch refresh suspected` appears, the portal reset all posting dates; the seen-jobs cache will handle deduplication on the next run.

**JSON-LD missing** — some ATS versions don't embed structured data on every detail page. Jobs are skipped and counted in the `skipped` total logged per site.

**New ATS / selectors changed** — run `inspect_selectors.py` with the portal URL to audit selectors and XHR patterns before editing the scraper.
