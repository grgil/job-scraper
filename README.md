# Health Job Scraper

Scrapes VCU Health and UVA Health careers pages daily and sends a single email alert for jobs posted today. Uses Playwright (headless Chromium) because both sites are fully JavaScript-rendered. Runs automatically via GitHub Actions.

---

## Prerequisites

- Python 3.10 or newer
- A Gmail account with 2FA enabled

---

## Setup

### 1. Install Python dependencies

```
pip install -r requirements.txt
```

### 2. Install Playwright's Chromium browser

```
python -m playwright install chromium
```

This downloads a standalone Chromium binary (~150 MB) that Playwright manages separately from any browser you have installed.

### 3. Generate a Gmail App Password

Regular Gmail passwords will not work — Google blocks them for SMTP. You need an App Password:

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already on
3. Search for **App passwords** (or go to myaccount.google.com/apppasswords)
4. App name: `HealthJobScraper` → click **Create**
5. Copy the 16-character password shown (it includes spaces — keep them)

### 4. Configure .env

Copy `.env.example` to `.env` and fill in your values:

```
EMAIL_FROM=your-gmail@gmail.com
EMAIL_TO=where-alerts-go@example.com
EMAIL_APP_PASS=xxxx xxxx xxxx xxxx
```

`EMAIL_FROM` and `EMAIL_TO` can be the same address.

---

## Running manually

```
python scraper.py
```

The script prints progress for each site and each job card it checks. One combined email covering both sites is sent at the end regardless of match count.

---

## Scheduling with GitHub Actions

The workflow at `.github/workflows/scraper.yml` runs automatically at 11:59 PM EST every day. No local machine needs to stay on.

### Required repository secrets

Add these under **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `EMAIL_FROM` | your-gmail@gmail.com |
| `EMAIL_TO` | where-alerts-go@example.com |
| `EMAIL_APP_PASS` | the 16-character App Password |

### Manual trigger

Go to **Actions → Health Job Scraper → Run workflow** to fire it on demand.

### Checking run logs

Click any completed run in the Actions tab to see full scraper output, including which jobs were found and whether the email was sent.

---

## Troubleshooting

### Authentication error (smtplib.SMTPAuthenticationError)

- Confirm 2-Step Verification is enabled on the sending Gmail account
- Regenerate the App Password — they can silently expire or be revoked
- Paste the full 16-character password including the spaces into `.env` (or the repository secret)
- Do not use your regular Gmail login password

### No jobs found / zero emails sent

- Run manually and watch the output. If 0 cards are found on the list page, the page selector may have changed
- Try increasing the `wait_for_selector` timeout in `_get_job_links` if the site is slow
- Check that the search URL is still valid by opening it in a browser

### JSON-LD block missing on a detail page

You'll see `No JSON-LD — skipping` in the output. This happens when:
- The Phenom People platform didn't embed structured data for that specific posting
- The page returned an error (404, redirect)

These jobs are skipped automatically. If it happens for every job on a site, the ATS version may have changed — open a job detail page in Chrome DevTools and search the page source for `application/ld+json`.
