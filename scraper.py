import asyncio
import json
import os
import smtplib
import ssl
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "scraper.log"
load_dotenv(BASE_DIR / ".env")

EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
EMAIL_APP_PASS = os.environ.get("EMAIL_APP_PASS", "")

SITES = [
    {
        "name": "VCU Health",
        "url": (
            "https://careers.vcuhealth.org/us/en/search-results"
            "?sortBy=postingdate&descending=true"
        ),
    },
    {
        "name": "UVA Health",
        "url": (
            "https://careers.uvahealth.org/us/en/search-results"
            "?sortBy=postingdate&descending=true"
        ),
    },
]

TODAY = date.today()



def _validate_env() -> None:
    missing = [k for k in ("EMAIL_FROM", "EMAIL_TO", "EMAIL_APP_PASS") if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"Missing required .env variable(s): {', '.join(missing)}\n"
            f"Copy .env.example to .env and fill in your credentials."
        )


def _extract_location(json_ld: dict) -> str:
    loc = json_ld.get("jobLocation", {})
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    address = loc.get("address", {})
    parts = [address.get("addressLocality", ""), address.get("addressRegion", "")]
    return ", ".join(p for p in parts if p)


async def _get_job_links(page, url: str) -> list[dict]:
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    # Wait for the sort dropdown — confirms SPA framework has hydrated
    try:
        await page.wait_for_selector('#sortselect', timeout=30_000)
    except PlaywrightTimeoutError:
        _log("  WARNING: Sort dropdown not found — page may not have hydrated")
        return []

    current_sort = await page.eval_on_selector('#sortselect', 'el => el.value')
    _log(f"  Sort dropdown value after load: '{current_sort}'")

    # Capture the first job href before any sort change (to detect re-render)
    first_href_before = await page.evaluate(
        """() => (document.querySelector('a[href*="/job/"]') || {}).href || ''"""
    )

    if current_sort == 'Most relevant':
        _log("  Selecting 'Most recent' and dispatching change event ...")
        await page.select_option('#sortselect', 'Most recent')
        # Fire events for both Aurelia (standard dispatch) and React-based canvas
        # (React ignores direct .value assignment; native setter triggers synthetic events)
        await page.evaluate("""() => {
            const sel = document.querySelector('#sortselect');
            const nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLSelectElement.prototype, 'value'
            ).set;
            nativeSetter.call(sel, 'Most recent');
            ['input', 'change'].forEach(t =>
                sel.dispatchEvent(new Event(t, { bubbles: true, cancelable: true }))
            );
        }""")
        # Wait for the result list to actually re-render (first href must change)
        try:
            await page.wait_for_function(
                f"""() => {{
                    const a = document.querySelector('a[href*="/job/"]');
                    return a && a.href !== {repr(first_href_before)};
                }}""",
                timeout=15_000,
            )
            _log("  Result list refreshed after sort change")
        except PlaywrightTimeoutError:
            _log("  WARNING: Sort re-render did not detect a change — results may be unsorted")

    # Wait until at least 5 job links with actual text content are present
    # (guards against firing on empty template elements before results load)
    try:
        await page.wait_for_function(
            """() => Array.from(document.querySelectorAll('a[href*="/job/"]'))
                .filter(a => (a.innerText || a.textContent || '').trim().length > 8)
                .length >= 5""",
            timeout=30_000,
        )
    except PlaywrightTimeoutError:
        _log("  WARNING: Timed out waiting for job links with content")
        return []

    return await page.evaluate("""() => {
        const seen = new Set();
        const results = [];
        document.querySelectorAll('a[href*="/job/"]').forEach(a => {
            if (seen.has(a.href)) return;
            seen.add(a.href);
            const title = (a.innerText || a.textContent || '').trim();
            if (title.length > 8) results.push({ title, url: a.href });
        });
        return results;
    }""")


async def _get_job_details(page, job_url: str) -> dict | None:
    try:
        await page.goto(job_url, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightTimeoutError:
        return None

    json_ld_text = await page.evaluate("""() => {
        for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
            try {
                const d = JSON.parse(s.textContent);
                if (d['@type'] === 'JobPosting') return s.textContent;
            } catch {}
        }
        return null;
    }""")

    if not json_ld_text:
        return None

    data = json.loads(json_ld_text)
    raw_date = data.get("datePosted", "")
    if not raw_date:
        return None

    emp_map = {"FULL_TIME": "Full-time", "PART_TIME": "Part-time",
               "CONTRACTOR": "Contract", "TEMPORARY": "Temporary", "PER_DIEM": "PRN"}
    raw_emp = data.get("employmentType", [])
    if isinstance(raw_emp, str):
        raw_emp = [raw_emp]
    employment_type = ", ".join(emp_map.get(e, e) for e in raw_emp)

    return {
        "date_posted": date.fromisoformat(raw_date[:10]),
        "location": _extract_location(data),
        "occupational_category": data.get("occupationalCategory", ""),
        "work_hours": data.get("workHours", ""),
        "employment_type": employment_type,
    }


async def _get_next_page_links(search_page) -> list[dict]:
    next_btn = search_page.locator('[data-ph-at-id="pagination-next-link"]').last
    if not await next_btn.is_visible():
        return []

    first_href = await search_page.evaluate(
        "() => (document.querySelector('a[href*=\"/job/\"]') || {}).href || ''"
    )
    await next_btn.click()

    try:
        await search_page.wait_for_function(
            f"""() => {{
                const a = document.querySelector('a[href*="/job/"]');
                return a && a.href !== {repr(first_href)};
            }}""",
            timeout=20_000,
        )
        await search_page.wait_for_function(
            """() => Array.from(document.querySelectorAll('a[href*="/job/"]'))
                .filter(a => (a.innerText || a.textContent || '').trim().length > 8)
                .length >= 5""",
            timeout=15_000,
        )
    except PlaywrightTimeoutError:
        _log("  WARNING: Next page did not load new content")
        return []

    return await search_page.evaluate("""() => {
        const seen = new Set();
        const results = [];
        document.querySelectorAll('a[href*="/job/"]').forEach(a => {
            if (seen.has(a.href)) return;
            seen.add(a.href);
            const title = (a.innerText || a.textContent || '').trim();
            if (title.length > 8) results.push({ title, url: a.href });
        });
        return results;
    }""")


async def scrape_site(browser, site: dict, since_date: date) -> list[dict]:
    _log(f"Scraping {site['name']} ...")
    # Two pages: search_page stays on the results list for pagination;
    # detail_page loads each job so we never navigate search_page away.
    search_page = await browser.new_page()
    detail_page = await browser.new_page()
    try:
        links = await _get_job_links(search_page, site["url"])
        page_num = 1
        _log(f"  {len(links)} job(s) on page {page_num}")

        results = []
        consecutive_empty = 0

        while links:
            page_matches = 0
            for i, job in enumerate(links, 1):
                _log(f"  [p{page_num}/{i}] {job['title'][:72]}")
                details = await _get_job_details(detail_page, job["url"])

                if details is None:
                    _log("    No JSON-LD — skipping")
                    continue

                dp = details["date_posted"]
                if dp >= since_date:
                    results.append({**job, **details})
                    _log(f"    MATCH  posted {dp}")
                    page_matches += 1
                else:
                    _log(f"    Older ({dp})")

            if page_matches == 0:
                consecutive_empty += 1
                _log(f"  Page {page_num}: 0 matches ({consecutive_empty}/5) — stopping" if consecutive_empty >= 5 else f"  Page {page_num}: 0 matches ({consecutive_empty}/5) — checking next page")
                if consecutive_empty >= 5:
                    break
            else:
                consecutive_empty = 0

            page_num += 1
            links = await _get_next_page_links(search_page)
            if links:
                _log(f"  {len(links)} job(s) on page {page_num}")
        else:
            _log("  No more pages")

        return results
    finally:
        await search_page.close()
        await detail_page.close()


def _build_site_section(site_name: str, jobs: list[dict]) -> str:
    count = len(jobs)
    job_items_html = []
    for job in jobs:
        subtext_parts = [
            job["location"] or "Location not listed",
            job.get("occupational_category", ""),
            job.get("employment_type", ""),
            job.get("work_hours", ""),
        ]
        subtext = " · ".join(p for p in subtext_parts if p)
        job_items_html.append(
            f'<li style="margin-bottom:10px;">'
            f'<a href="{job["url"]}" style="color:#1a4a7a;font-weight:bold;">{job["title"]}</a><br>'
            f'<span style="color:#666;font-size:13px;">{subtext}</span>'
            f'</li>'
        )
    body = (
        f'<ul style="padding-left:20px;line-height:1.9;">{"".join(job_items_html)}</ul>'
        if job_items_html else
        '<p style="color:#aaa;font-size:13px;">No new listings today.</p>'
    )
    return (
        f'<h3 style="color:#1a4a7a;margin-bottom:4px;">{site_name}</h3>'
        f'<p style="margin-top:0;font-size:13px;color:#555;">{count} new posting{"s" if count != 1 else ""}</p>'
        f'{body}'
    )


def build_html_email(results: list[tuple[str, list[dict]]], today: date) -> str:
    total = sum(len(jobs) for _, jobs in results)
    date_str = today.strftime('%b %d, %Y')
    sections = '<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">'.join(
        _build_site_section(site_name, jobs) for site_name, jobs in results
    )
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;padding:24px;color:#222;">
  <h2 style="color:#1a4a7a;margin-bottom:4px;">Health Job Alert</h2>
  <p style="color:#888;font-size:13px;margin-top:0;">{date_str} · {total} new posting{"s" if total != 1 else ""}</p>
  {sections}
  <hr style="border:none;border-top:1px solid #eee;margin-top:32px;">
  <p style="color:#bbb;font-size:12px;">Scraped automatically</p>
</body>
</html>"""


def send_email(results: list[tuple[str, list[dict]]], today: date) -> None:
    total = sum(len(jobs) for _, jobs in results)
    subject = f"[Job Alert] {total} new posting{'s' if total != 1 else ''} — {today.strftime('%Y-%m-%d')}"
    html = build_html_email(results, today)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(EMAIL_FROM, EMAIL_APP_PASS)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    _log(f"  Email sent to {EMAIL_TO}")


def _log(message: str) -> None:
    entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(entry)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(entry + "\n")


async def main() -> None:
    _validate_env()
    _log(f"Run started (since={TODAY})")

    try:
        async with async_playwright() as pw:
            _log("Launching browser ...")
            browser = await pw.chromium.launch(headless=True)
            _log("Browser ready")
            try:
                results = []
                for site in SITES:
                    jobs = await scrape_site(browser, site, TODAY)
                    _log(f"{site['name']}: {len(jobs)} qualifying job(s)")
                    results.append((site["name"], jobs))

                send_email(results, TODAY)
            finally:
                await browser.close()
    except Exception as e:
        _log(f"ERROR: {e}")
        raise

    _log("Run complete")


if __name__ == "__main__":
    asyncio.run(main())
