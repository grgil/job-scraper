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
    # ("MUSC", "https://musc.wd1.myworkdayjobs.com/MUSC?locationHierarchy1=b6f39ab6e17a1010ca272712938e0000"),
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


async def probe_musc_cxs() -> None:
    """Navigate MUSC page 1, click Next, capture raw CXS postedOn values from page 2."""
    url = "https://musc.wd1.myworkdayjobs.com/MUSC?locationHierarchy1=b6f39ab6e17a1010ca272712938e0000"
    print(f"\n{'='*60}\n  MUSC CXS postedOn probe\n{'='*60}")

    cxs_bodies: list[bytes] = []

    async def _on_resp(response):
        if "/wday/cxs/" in response.url and response.status == 200:
            try:
                body = await response.body()
                cxs_bodies.append(body)
                print(f"  CXS response captured: {response.url[:100]}")
            except Exception as e:
                print(f"  CXS body error: {e}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        page.on("response", _on_resp)

        print(f"  Loading page 1 ...")
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(5_000)
        print(f"  CXS bodies after page 1: {len(cxs_bodies)}")

        # Click Next to trigger a CXS page-2 request
        try:
            next_btn = page.locator('button[aria-label="next"]')
            await next_btn.wait_for(state="visible", timeout=10_000)
            print("  Clicking Next ...")
            await next_btn.click()
            await page.wait_for_timeout(4_000)
            print(f"  CXS bodies after Next click: {len(cxs_bodies)}")
        except Exception as e:
            print(f"  Next button error: {e}")

        await browser.close()

    import json as _json
    all_posted_on: list[str] = []
    for i, body in enumerate(cxs_bodies):
        try:
            data = _json.loads(body)
            postings = data.get("jobPostings") or []
            if not postings:
                print(f"  Body {i}: top-level keys = {list(data.keys())[:10]}")
                continue
            # Show first posting's full key set + sample values
            first = postings[0]
            print(f"  Body {i}: {len(postings)} postings, keys = {list(first.keys())}")
            print(f"  Body {i} first posting: {_json.dumps(first, indent=2)[:600]}")
            for job in postings:
                v = job.get("postedOn", "")
                if v:
                    all_posted_on.append(v)
        except Exception as e:
            print(f"  Body {i}: parse error — {e}")

    unique = sorted(set(all_posted_on))
    print(f"\n  Unique postedOn values ({len(unique)}):")
    for v in unique:
        print(f"    {repr(v)}")


WORKDAY_PROBE_SITES = [
    ("Bon Secours",            "https://easyservice.wd5.myworkdayjobs.com/BonSecoursMercyHealthCareers"),
    ("Carilion Clinic",        "https://carilionclinic.wd12.myworkdayjobs.com/en-US/External_Careers?jobFamilyGroup=01a109d50e5f10072caa9557e5510000"),
    ("Wellstar",               "https://wellstar.wd1.myworkdayjobs.com/wellstarcareers?jobFamilyGroup=36c48bb8fdf710267875d93c99eb0000"),
    ("Atrium",                 "https://aah.wd5.myworkdayjobs.com/External?jobFamilyGroup=638364634b3b1001bd1e1c9052760000"),
    ("MUSC",                   "https://musc.wd1.myworkdayjobs.com/en-US/MUSC/jobs?jobFamily=b6f39ab6e17a1010bc655fcf712b0002"),
    ("Shepherd Center",        "https://shepherd.wd5.myworkdayjobs.com/ShepherdCenter"),
    ("VUMC",                   "https://vumc.wd1.myworkdayjobs.com/vumccareers?jobFamilyGroup=aa4bc8a45bec1001f06b6f977bfb0000"),
    ("Sentara",                "https://sentara.wd1.myworkdayjobs.com/en-US/SCS?jobFamilyGroup=fb2c628a192710009e83d566e96d0000"),
    ("Prisma Health",          "https://prismahealth.wd5.myworkdayjobs.com/PrismaHealthCorporate?jobFamilyGroup=ee936705568e0156f8bf3bd6df038fc3"),
]


async def _probe_one_site(browser, name: str, url: str) -> dict:
    """Load a Workday site, trigger pagination, return CXS postedOn field status."""
    import json as _json

    cxs_jobs: list[dict] = []

    async def _on_resp(response):
        if "/wday/cxs/" in response.url and response.status == 200:
            try:
                body = await response.body()
                data = _json.loads(body)
                for job in (data.get("jobPostings") or []):
                    cxs_jobs.append(job)
            except Exception:
                pass

    page = await browser.new_page()
    page.on("response", _on_resp)
    result = {"name": name, "cxs_fired": False, "has_posted_on": None, "sample": None, "error": None}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(4_000)

        if not cxs_jobs:
            # Try clicking Next to trigger CXS
            try:
                btn = page.locator('button[aria-label="next"]')
                await btn.wait_for(state="visible", timeout=8_000)
                await btn.click()
                await page.wait_for_timeout(4_000)
            except Exception:
                pass

        if cxs_jobs:
            result["cxs_fired"] = True
            sample_keys = list(cxs_jobs[0].keys())
            result["has_posted_on"] = any("postedOn" in job for job in cxs_jobs)
            result["sample"] = cxs_jobs[0].get("postedOn", "<absent>")
            result["keys"] = sample_keys
        else:
            result["cxs_fired"] = False

    except Exception as e:
        result["error"] = str(e)[:80]
    finally:
        await page.close()

    return result


async def probe_workday_cxs() -> None:
    """Check every active Workday site for postedOn presence in CXS responses."""
    print(f"\n{'='*65}")
    print(f"  Workday CXS postedOn field audit — {len(WORKDAY_PROBE_SITES)} sites")
    print(f"{'='*65}\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        # Run sequentially to avoid rate-limiting
        results = []
        for name, url in WORKDAY_PROBE_SITES:
            print(f"  Probing {name} ...", flush=True)
            r = await _probe_one_site(browser, name, url)
            results.append(r)
        await browser.close()

    print(f"\n{'-'*65}")
    print(f"  {'Site':<22} {'CXS fired':<12} {'postedOn':<12} {'Sample / keys'}")
    print(f"{'-'*65}")
    for r in results:
        if r["error"]:
            print(f"  {r['name']:<22} ERROR: {r['error']}")
        elif not r["cxs_fired"]:
            print(f"  {r['name']:<22} {'no':<12} {'—':<12} (CXS did not fire)")
        else:
            status = "YES" if r["has_posted_on"] else "MISSING"
            sample = repr(r["sample"]) if r["has_posted_on"] else str(r.get("keys", ""))
            print(f"  {r['name']:<22} {'yes':<12} {status:<12} {sample}")
    print(f"{'-'*65}\n")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "musc-cxs":
        asyncio.run(probe_musc_cxs())
    elif len(sys.argv) > 1 and sys.argv[1] == "workday-cxs":
        asyncio.run(probe_workday_cxs())
    else:
        asyncio.run(main())
