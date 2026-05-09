# Health Job Scraper

Scrapes UVA Health and VCU Health careers pages daily and sends email alerts for jobs posted today or yesterday. Uses Playwright (headless Chromium) because both sites are fully JavaScript-rendered.

---

## Prerequisites

- Python 3.10 or newer
- Windows (Task Scheduler integration)
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

The script prints progress for each site and each job card it checks. If qualifying jobs are found, one email per site is sent immediately.

Expected output when jobs are found:

```
Scraping UVA Health ...
  42 job card(s) on list page
  [1/42] Registered Nurse - ICU
    MATCH  posted 2025-05-04
  [2/42] Patient Care Technician
    Older (2025-05-01) — stopping

  1 qualifying job(s) for UVA Health
  Email sent to you@gmail.com
```

---

## Scheduling with Windows Task Scheduler

Right-click `setup_task.bat` → **Run as administrator**.

The script:
- Auto-detects your Python executable path
- Deletes any existing `HealthJobScraper` task first
- Registers a new daily task at 11:59 PM

To verify the task was created, open **Task Scheduler** (search in Start) and look for `HealthJobScraper` under Task Scheduler Library.

To trigger it immediately for testing:
```
schtasks /run /tn "HealthJobScraper"
```

To remove it:
```
schtasks /delete /tn "HealthJobScraper" /f
```

---

## Troubleshooting

### Authentication error (smtplib.SMTPAuthenticationError)

- Confirm 2-Step Verification is enabled on the sending Gmail account
- Regenerate the App Password — they can silently expire or be revoked
- Paste the full 16-character password including the spaces into `.env`
- Do not use your regular Gmail login password

### No jobs found / zero emails sent

- Run manually and watch the output. If 0 cards are found on the list page, the page selector may have changed
- Try increasing the `wait_for_selector` timeout in `_get_job_links` if the site is slow
- Check that the search URL is still valid by opening it in a browser

### JSON-LD block missing on a detail page

You'll see `No JSON-LD block — skipping` in the output. This happens when:
- The Phenom People platform didn't embed structured data for that specific posting
- The page returned an error (404, redirect)

These jobs are skipped automatically. If it happens for every job on a site, the ATS version may have changed — open a job detail page in Chrome DevTools and search the page source for `application/ld+json`.

### Task Scheduler not firing

1. Open Task Scheduler → find `HealthJobScraper` → check **Last Run Result** (should be `0x0` for success)
2. Check that the task's **Security options** shows **Run only when user is logged on** OR switch to **Run whether user is logged on or not** and enter your Windows password
3. Check that the working directory issue doesn't apply: `scraper.py` resolves its `.env` path relative to its own location (`Path(__file__).parent`), so it works regardless of what directory Task Scheduler starts in
4. To see output from the scheduled run, edit the task action to redirect stdout: `python "C:\path\scraper.py" >> "C:\path\scraper.log" 2>&1`
