"""
DOM inspector for adding or debugging scraper targets.

Usage:
  1. Add URLs to the appropriate list below (WORKDAY_URLS, ICIMS_URLS, PHENOM_URLS, OTHER_URLS)
  2. python inspect_selectors.py
  3. Review output: JSON-LD presence, job link selectors, pagination, XHR calls, iframes

Clear the URL lists when done — do not commit live URLs to this file.

# ---------------------------------------------------------------------------
# Investigated — blocked, unresolvable, or requires custom scraper
# ---------------------------------------------------------------------------
#
# Novant Health
#   URL: https://jobs.novanthealth.org/careers-home/jobs?page=1&sortBy=posted_date&tags6=Charlotte%20Area&descending=true
#   Result: zero output — no links, XHR, CSS classes, or frames in headless mode
#   Verdict: bot detection or heavy JS gating; requires non-headless or a different entry URL
#   Note: _icims_content_frame() in scraper.py mentions Novant as a known iframe-based iCIMS
#         portal, but the careers-home URL itself produces nothing in headless Chromium
#
# Centene
#   URLs tried: centene.wd5.myworkdayjobs.com (Workday tenant), jobs.centene.com
#   Result: both blocked / timeout at scrape time
#   Verdict: skip; re-inspect if re-evaluated
#
# CorroHealth
#   URL: (Workday tenant — not recorded)
#   Result: Workday maintenance page at scrape time
#   Verdict: skip; re-inspect if re-evaluated
#
# CommonSpirit Health
#   URL: https://www.commonspirit.careers/search-jobs
#   ATS: Custom frontend (commonspirit.careers) wrapping careers-commonspirit.icims.com
#   Result: page loads; job links present (pattern: /job/{city}/{slug}/{dept-id}/{job-id})
#           CSS classes: job-list__job-title, search-results-list__job-link, pagination-view-more
#           No dates in listing cards — detail page visits required per job
#           pagination-view-more = load-more button, not traditional pagination
#   Verdict: feasible but needs a custom scraper (not standard Workday/iCIMS/Phenom)
# ---------------------------------------------------------------------------
"""
import asyncio
from playwright.async_api import async_playwright

WORKDAY_URLS: list[tuple[str, str]] = [
    # ("Org Name", "https://tenant.wd5.myworkdayjobs.com/SiteName"),
]

ICIMS_URLS: list[tuple[str, str]] = [
    # ("Org Name", "https://org.icims.com/jobs/search"),
]

PHENOM_URLS: list[tuple[str, str]] = [
    # ("Org Name", "https://careers.org.org/us/en/search-results?sortBy=postingdate&descending=true"),
]

OTHER_URLS: list[tuple[str, str]] = [
    # ("Org Name", "https://..."),
]


async def inspect(page, url: str, label: str) -> None:
    print(f"\n{'='*60}\n  {label}\n  URL: {url}\n{'='*60}")

    captured_xhr: list[str] = []
    async def on_response(response):
        if response.request.resource_type in ("xhr", "fetch"):
            captured_xhr.append(f"  {response.status} {response.url[:120]}")
    page.on("response", on_response)

    await page.goto(url, wait_until="networkidle", timeout=90_000)
    await page.wait_for_timeout(3_000)

    # Workday automation IDs
    auto_ids = await page.evaluate("""() => {
        const ids = new Set();
        document.querySelectorAll('[data-automation-id]').forEach(el =>
            ids.add(el.getAttribute('data-automation-id'))
        );
        return Array.from(ids).sort();
    }""")
    if auto_ids:
        job_ids = [v for v in auto_ids if any(k in v.lower() for k in ("job", "post", "found", "result", "paginat"))]
        print(f"\nWorkday auto-ids: {job_ids or auto_ids[:10]}")

    # CSS classes relevant to job cards and pagination
    classes = await page.evaluate("""() => {
        const seen = new Set();
        document.querySelectorAll('[class]').forEach(el =>
            (el.className || '').toString().split(/\\s+/).forEach(c => seen.add(c))
        );
        return Array.from(seen).filter(c =>
            ['job','card','result','paginat','icims','posting','listing','search-item'].some(k => c.toLowerCase().includes(k))
        ).sort();
    }""")
    if classes:
        print(f"CSS classes (job/card/result/pagination): {classes}")

    # First visible job-like links
    links = await page.evaluate("""() =>
        Array.from(document.querySelectorAll('a[href]'))
            .filter(a => {
                const href = a.href || '';
                const text = (a.innerText || a.textContent || '').trim();
                return text.length > 5 && (href.includes('/job') || href.includes('/jobs/'));
            })
            .slice(0, 3)
            .map(a => ({text: (a.innerText||'').trim().slice(0,60), href: a.href}))
    """)
    print(f"\nJob-like links: {links}")

    # Date hints — postedOn, date, ago patterns
    date_hints = await page.evaluate("""() => {
        const hints = new Set();
        document.querySelectorAll('[data-automation-id="postedOn"], time, [class*="date"], [class*="posted"]').forEach(el => {
            const t = (el.innerText || el.textContent || '').trim();
            if (t.length > 0 && t.length < 80) hints.add(t);
        });
        return Array.from(hints).slice(0, 6);
    }""")
    print(f"Date hints: {date_hints}")

    # Pagination candidates
    pag = await page.evaluate("""() =>
        Array.from(document.querySelectorAll('button, a, [role="button"]'))
            .filter(el => {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                const a = (el.getAttribute('aria-label') || '').toLowerCase();
                return t.includes('next') || a.includes('next') || t === '>' || t === '›';
            })
            .slice(0, 3)
            .map(el => ({
                tag: el.tagName,
                text: (el.innerText||'').trim().slice(0,30),
                cls: (el.className||'').toString().slice(0,60),
                aria: el.getAttribute('aria-label') || '',
                vis: el.offsetParent !== null,
            }))
    """)
    print(f"Pagination candidates: {pag}")

    # JSON-LD
    json_ld = await page.evaluate("""() => {
        for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
            try {
                const d = JSON.parse(s.textContent);
                if (d['@type'] === 'JobPosting') return {
                    found: true, datePosted: d.datePosted,
                    loc: JSON.stringify(d.jobLocation||null).slice(0,100),
                };
            } catch {}
        }
        return {found: false};
    }""")
    print(f"JSON-LD JobPosting: {'FOUND — datePosted=' + str(json_ld.get('datePosted')) if json_ld['found'] else 'NOT FOUND'}")

    # XHR/fetch calls
    print(f"XHR/fetch: {captured_xhr[:5]}")

    # Iframe probe
    frames = page.frames
    non_blank = [f for f in frames[1:] if f.url and "about:" not in f.url]
    print(f"Frames: {len(frames)} — {[f.url for f in non_blank]}")
    for frame in non_blank[:2]:
        try:
            flinks = await frame.evaluate("""() =>
                Array.from(document.querySelectorAll('a[href*="/jobs/"]'))
                    .slice(0,2).map(a => (a.innerText||'').trim().slice(0,50))
            """)
            if flinks:
                print(f"  Frame job titles: {flinks}")
        except Exception:
            pass


async def main() -> None:
    all_urls = (
        [(name, url, "WORKDAY") for name, url in WORKDAY_URLS]
        + [(name, url, "ICIMS")   for name, url in ICIMS_URLS]
        + [(name, url, "PHENOM")  for name, url in PHENOM_URLS]
        + [(name, url, "OTHER")   for name, url in OTHER_URLS]
    )
    if not all_urls:
        print("No URLs configured. Add entries to WORKDAY_URLS, ICIMS_URLS, PHENOM_URLS, or OTHER_URLS.")
        return
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for name, url, platform in all_urls:
                page = await browser.new_page()
                try:
                    await inspect(page, url, f"{platform} — {name}")
                except Exception as e:
                    print(f"  ERROR: {e}")
                finally:
                    await page.close()
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
