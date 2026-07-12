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
#
# UNC Health
#   URL: https://jobs.unchealthcare.org/search/.../jobs  (marketing/front-end site)
#   Result: entire domain (including /robots.txt, /sitemap.xml) returns HTTP 403 from
#           an interactive Cloudflare Turnstile "Verify you are human" challenge
#           (cf-mitigated: challenge header). Not a passive JS bot-check like Novant/
#           Centene above — a real CAPTCHA-style wall. Not automated around.
#   Verdict: skip this domain entirely. See below for the real integration point.
#
# UNC Health — Infor CloudSuite HCM (Landmark career site) — INTEGRATED
#   URL: https://css-unchealthunc-prd.inforcloudsuite.com/hcm/Jobs/list/
#        JobPosting.SearchForJobsResults?csk.JobBoard=EXTERNAL&csk.HROrganization=9999
#        &menu=JobsNavigationMenu.JobSearch
#   Found via the site's "Returning Applicant" portal link — a separate domain from
#   jobs.unchealthcare.org with no Cloudflare protection (plain HTTP 200).
#   ATS: Infor CloudSuite HCM, "Candidate Experience" module (Landmark UI framework,
#        data-automation-id="lm-*"). Not previously integrated (distinct from Workday/
#        iCIMS/Phenom/Jobsyn).
#   Result: page.goto() on the entry URL bootstraps an anonymous SSO session; the real
#           job data endpoint (.../hcm/Jobs/list/JobPosting.SearchForJobsResults?
#           pageop=load&pagesize=200&sortOrderName=JobPosting.ByPostDateBeginSet&
#           isAscending=false&...) is a directly GET-able JSON API (via page.request.get,
#           reusing the session's cookies) — not the output=spec calls, which are just
#           UI/form metadata. Each row's fields include Description (title), Category
#           (label matches the marketing site's URL category slugs), a stable numeric
#           JobId/JobRequisition, PostingDateRange (clean YYYYMMDD, no detail-page fetch
#           needed for dates), and LocationOfJobDescriptionForSort ("US:NC:City").
#           Pagination is cursor-based (pagingInfo.fk/lk/hasNext, sorted newest-first) —
#           >200 total postings, so cursor chaining is required beyond page 1.
#           Per-job detail is an inline SPA panel, not a real anchor href, but
#           .../hcm/Jobs/form/JobPosting%5BJobPostingSet%5D(9999,{id},1).JobPostingDisplay
#           ?menu=JobsNavigationMenu.JobSearch&csk.JobBoard=EXTERNAL&csk.HROrganization=9999
#           renders correctly as a cold/standalone deep link — used as the email job URL.
#           Category filtering in the UI is session/state-based (no clean replayable
#           query param found); scraper filters client-side on the free Category field
#           instead of reproducing the UI's stateful filter.
#   Verdict: integrated — see INFOR_SITES / scrape_infor_site() in scraper.py.
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


PHENOM_PROBE_SITES = [
    ("UVA Health",  "https://careers.uvahealth.org/us/en/search-results?sortBy=postingdate&descending=true"),
    ("VCU Health",  "https://careers.vcuhealth.org/us/en/search-results?sortBy=postingdate&descending=true"),
    ("Duke Health", "https://careers.dukehealth.org/us/en/search-results?sortBy=postingdate&descending=true"),
]

# Partial-match strings — probe clicks the first facet item whose text contains one of these.
# Keep them specific enough to avoid false matches.
PHENOM_TARGET_CATEGORIES = {
    "UVA Health": [
        "Finance",
        "Business",
        "Information Management",
        "Services",
        "Research",
        "Safety",
        "Compliance",
        "Regulatory",
    ],
    "VCU Health": [
        "Revenue Cycle",
        "Information Technology",
        "Health System",
        "Quality",
        "Administrative Support",
    ],
    "Duke Health": [
        "Corporate",
        "Information Technology",
        "Revenue Management",
        "Administrative and Support",
        "Population Health",
        "Patient Quality and Safety",
        "Revenue Cycle",
    ],
}

_FACET_JS = """() => {
    // Expand any collapsed "Show more" / "View all" facet buttons first
    document.querySelectorAll('[data-ph-at-id*="facet"] button, [data-ph-at-id*="facet"] a').forEach(el => {
        const t = (el.innerText || el.textContent || '').toLowerCase();
        if (t.includes('show') || t.includes('view') || t.includes('more') || t.includes('all')) {
            try { el.click(); } catch {}
        }
    });
    // Collect all facet-results-item LIs with their text and any href
    return [...document.querySelectorAll('[data-ph-at-id="facet-results-item"]')].map(el => {
        const raw = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
        const clean = raw.replace(/\\(\\d+\\+?\\)\\s*jobs?/gi, '').trim();
        const a = el.querySelector('a');
        return {
            raw,
            clean,
            href: (a && a.href && !a.href.endsWith('#')) ? a.href : null,
        };
    });
}"""


async def probe_phenom_categories() -> None:
    """Click each target category facet using a real Playwright click and capture:
      - Any href on the facet <a> link (usable directly as a scraper URL)
      - The POST body sent to /widgets (reveals category field name + value)
      - Whether the page URL changes (query param approach)

    Run with: python inspect_selectors.py phenom-categories
    """
    import json as _json

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        for name, base_url in PHENOM_PROBE_SITES:
            targets = PHENOM_TARGET_CATEGORIES.get(name, [])
            print(f"\n{'='*65}\n  {name}\n{'='*65}")

            page = await browser.new_page()
            widget_requests: list[dict] = []

            async def _on_req(req, _cap=widget_requests):
                if req.resource_type in ("xhr", "fetch") and "/widgets" in req.url:
                    try:
                        body = req.post_data or ""
                    except Exception:
                        body = ""
                    _cap.append({"method": req.method, "url": req.url[:100], "body": body})

            page.on("request", _on_req)
            await page.goto(base_url, wait_until="networkidle", timeout=90_000)
            await page.wait_for_timeout(2_000)
            widget_requests.clear()

            # Full facet list (no truncation) — also tries to expand "show more"
            all_facets = await page.evaluate(_FACET_JS)
            print(f"\n  All category facet items ({len(all_facets)}):")
            for f in all_facets:
                href_note = f"  → {f['href']}" if f["href"] else ""
                print(f"    '{f['clean']}'{href_note}")

            # Click each target category in turn
            for target in targets:
                match = next(
                    (f for f in all_facets if target.lower() in f["clean"].lower()),
                    None,
                )
                if not match:
                    print(f"\n  [{target}] — NOT FOUND in facet list")
                    continue

                # If the <a> has a real href, report it and skip the click
                if match["href"]:
                    print(f"\n  [{target}] — has href: {match['href']}")
                    continue

                # Real Playwright click — fires React event handlers
                widget_requests.clear()
                print(f"\n  [{target}] clicking '{match['clean']}' ...")
                try:
                    locator = page.locator('[data-ph-at-id="facet-results-item"]').filter(has_text=target)
                    await locator.first.scroll_into_view_if_needed(timeout=5_000)
                    await locator.first.click(timeout=8_000)
                    await page.wait_for_timeout(3_500)

                    if widget_requests:
                        for r in widget_requests:
                            print(f"    POST {r['url']}")
                            if r["body"]:
                                try:
                                    parsed = _json.loads(r["body"])
                                    print(f"    body: {_json.dumps(parsed, indent=6)[:800]}")
                                except Exception:
                                    print(f"    body (raw): {r['body'][:400]}")
                    else:
                        print(f"    (no /widgets POST fired)")

                    if page.url != base_url:
                        print(f"    URL changed: {page.url}")

                    # Deselect and reset for next target
                    widget_requests.clear()
                    try:
                        await locator.first.click(timeout=4_000)
                        await page.wait_for_timeout(2_000)
                        widget_requests.clear()
                    except Exception:
                        await page.goto(base_url, wait_until="networkidle", timeout=60_000)
                        await page.wait_for_timeout(2_000)
                        widget_requests.clear()
                        all_facets = await page.evaluate(_FACET_JS)

                except Exception as e:
                    print(f"    click error: {e}")
                    await page.goto(base_url, wait_until="networkidle", timeout=60_000)
                    await page.wait_for_timeout(2_000)
                    widget_requests.clear()
                    all_facets = await page.evaluate(_FACET_JS)

            await page.close()

        await browser.close()


async def probe_emory_categories() -> None:
    """Load emory.jobs/jobs/, capture the prod-search-api.jobsyn.org call structure,
    then click available filter controls and compare the resulting API calls.

    Objective: determine whether the API accepts department/category params so we can
    filter before scraping rather than bumping max_pages.

    Run with: python inspect_selectors.py emory-categories
    """
    import json as _json
    from urllib.parse import urlparse, parse_qs

    page_url = "https://emory.jobs/jobs/"
    api_calls: list[dict] = []

    async def _on_req(req, _cap=api_calls):
        if "prod-search-api.jobsyn.org" in req.url:
            try:
                body = req.post_data or ""
            except Exception:
                body = ""
            _cap.append({"method": req.method, "url": req.url, "body": body})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        page.on("request", _on_req)

        print(f"\n{'='*65}\n  Emory Healthcare — Jobsyn API filter probe\n{'='*65}")
        print(f"  Loading {page_url} ...")
        await page.goto(page_url, wait_until="networkidle", timeout=90_000)
        await page.wait_for_timeout(3_000)

        # Print initial API call — the full URL reveals available params
        if api_calls:
            initial = api_calls[0]
            print(f"\n  Initial API call [{initial['method']}]:")
            print(f"    {initial['url']}")
            parsed = urlparse(initial["url"])
            params = parse_qs(parsed.query)
            if params:
                print(f"    Query params:")
                for k, v in params.items():
                    print(f"      {k} = {v}")
            if initial["body"]:
                try:
                    print(f"    POST body: {_json.dumps(_json.loads(initial['body']), indent=4)[:600]}")
                except Exception:
                    print(f"    POST body (raw): {initial['body'][:400]}")
        else:
            print("\n  WARNING: No prod-search-api call captured on page load")

        # Enumerate DOM filter controls
        print(f"\n  --- Filter controls ---")

        selects = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('select')).map(sel => ({
                id: sel.id, name: sel.name,
                cls: (sel.className || '').toString().slice(0, 60),
                options: Array.from(sel.options)
                    .map(o => ({value: o.value, text: o.text.trim()}))
                    .filter(o => o.value && o.text)
                    .slice(0, 15),
            }))
        """)
        if selects:
            print(f"\n  Dropdowns ({len(selects)}):")
            for s in selects:
                print(f"    <select id={repr(s['id'])} name={repr(s['name'])} class={repr(s['cls'])}>")
                for o in s["options"]:
                    print(f"      value={repr(o['value'])}  text={repr(o['text'])}")
        else:
            print("  No <select> elements found")

        checkboxes = await page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                let text = '';
                if (cb.id) {
                    const lbl = document.querySelector(`label[for="${cb.id}"]`);
                    if (lbl) text = (lbl.innerText || lbl.textContent || '').trim();
                }
                if (!text) {
                    const parent = cb.closest('label') || cb.parentElement;
                    if (parent) text = (parent.innerText || parent.textContent || '').trim();
                }
                out.push({id: cb.id, name: cb.name, value: cb.value, text: text.slice(0, 80), checked: cb.checked});
            });
            return out.slice(0, 40);
        }""")
        if checkboxes:
            print(f"\n  Checkboxes ({len(checkboxes)}):")
            for cb in checkboxes:
                print(f"    id={repr(cb['id'])} name={repr(cb['name'])} val={repr(cb['value'])}  label={repr(cb['text'])}")
        else:
            print("  No checkboxes found")

        # Try to click a filter (prefer relevance keywords, fall back to first checkbox)
        relevant_kw = ["technology", "information", "finance", "analytics", "data", "quality", "compliance", "revenue", "informatics"]
        target_cb = None
        for cb in checkboxes:
            if any(kw in cb["text"].lower() for kw in relevant_kw):
                target_cb = cb
                break
        if not target_cb and checkboxes:
            target_cb = checkboxes[0]

        if target_cb:
            api_calls.clear()
            print(f"\n  Clicking checkbox: {repr(target_cb['text'])} (id={target_cb['id']}) ...")
            try:
                if target_cb["id"]:
                    cb_loc = page.locator(f'input#{ target_cb["id"] }')
                else:
                    cb_loc = page.locator(f'input[value="{target_cb["value"]}"]').first
                await cb_loc.check(timeout=5_000)
                await page.wait_for_timeout(3_500)

                if api_calls:
                    after = api_calls[-1]
                    print(f"\n  API call after filter click [{after['method']}]:")
                    print(f"    {after['url']}")
                    parsed_after = urlparse(after["url"])
                    params_after = parse_qs(parsed_after.query)
                    if params_after:
                        print(f"    Query params:")
                        for k, v in params_after.items():
                            print(f"      {k} = {v}")
                    if after["body"]:
                        try:
                            print(f"    POST body: {_json.dumps(_json.loads(after['body']), indent=4)[:600]}")
                        except Exception:
                            print(f"    POST body (raw): {after['body'][:400]}")
                else:
                    print("  (no new API call captured after click — filter may update client-side only)")
            except Exception as e:
                print(f"  Click error: {e}")

        # Try select dropdown if no useful checkboxes
        elif selects:
            target_sel = selects[0]
            if target_sel["options"]:
                first_opt = target_sel["options"][0]
                api_calls.clear()
                print(f"\n  Selecting dropdown option: {repr(first_opt['text'])} in select id={target_sel['id']} ...")
                try:
                    await page.select_option(f'select#{target_sel["id"]}' if target_sel["id"] else "select", first_opt["value"])
                    await page.wait_for_timeout(3_500)
                    if api_calls:
                        after = api_calls[-1]
                        print(f"\n  API call after dropdown select [{after['method']}]:")
                        print(f"    {after['url']}")
                        parsed_after = urlparse(after["url"])
                        params_after = parse_qs(parsed_after.query)
                        if params_after:
                            print(f"    Query params:")
                            for k, v in params_after.items():
                                print(f"      {k} = {v}")
                    else:
                        print("  (no new API call captured after dropdown change)")
                except Exception as e:
                    print(f"  Dropdown error: {e}")
        else:
            print("\n  No filter controls found to click")

        await browser.close()


_PHENOM_SORT_SITES = [
    ("UVA Health",  "https://careers.uvahealth.org/us/en/search-results?sortBy=postingdate&descending=true"),
    ("VCU Health",  "https://careers.vcuhealth.org/us/en/search-results?sortBy=postingdate&descending=true"),
    ("Duke Health", "https://careers.dukehealth.org/us/en/search-results?sortBy=postingdate&descending=true"),
]

_JOB_LINKS_JS = """() => {
    const seen = new Set();
    const out = [];
    document.querySelectorAll('a[href*="/job/"]').forEach(a => {
        if (seen.has(a.href)) return;
        seen.add(a.href);
        const title = (a.innerText || a.textContent || '').trim();
        if (title.length > 8) out.push({ title: title.slice(0, 55), url: a.href });
    });
    return out.slice(0, 5);
}"""

_JSON_LD_DATE_JS = """() => {
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
            const d = JSON.parse(s.textContent);
            if (d['@type'] === 'JobPosting') return d.datePosted || null;
        } catch {}
    }
    return null;
}"""


async def probe_phenom_sort() -> None:
    """Verify that sortBy=postingdate in the URL produces date-fresh results
    equivalent to the dropdown 'Most recent' selection, for UVA / VCU / Duke.

    For each site:
      1. Load with sortBy=postingdate URL → record dropdown value + first-5 jobs
      2. If dropdown shows 'Most relevant', trigger native change to 'Most recent'
         → record first-5 jobs after re-render (or timeout)
      3. Fetch datePosted from JSON-LD on the first job's detail page for each ordering
      4. Report: dropdown value, whether lists match, datePosted for ordering A vs B

    Run with: python inspect_selectors.py phenom-sort
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        for name, url in _PHENOM_SORT_SITES:
            print(f"\n{'='*65}\n  {name}\n  URL: {url}\n{'='*65}")
            page = await browser.new_page()
            detail_page = await browser.new_page()

            try:
                # ── Step 1: Load with URL sort param ─────────────────────────
                print(f"\n  [A] Loading with URL sortBy=postingdate ...")
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

                # Wait for sort dropdown or jobs
                dropdown_val = None
                try:
                    await page.wait_for_selector('#sortselect', timeout=30_000)
                    dropdown_val = await page.eval_on_selector('#sortselect', 'el => el.value')
                    print(f"  Dropdown value on load: '{dropdown_val}'")
                except Exception:
                    print(f"  Dropdown #sortselect not found within 30s")

                # Wait for job links
                try:
                    await page.wait_for_function(
                        """() => document.querySelectorAll('a[href*="/job/"]').length >= 3""",
                        timeout=20_000,
                    )
                except Exception:
                    print(f"  WARNING: fewer than 3 job links appeared")

                links_url_sort = await page.evaluate(_JOB_LINKS_JS)
                print(f"\n  [A] First {len(links_url_sort)} jobs (URL sort):")
                for i, j in enumerate(links_url_sort, 1):
                    print(f"    {i}. {j['title']}")

                # Fetch datePosted for job #1 via JSON-LD
                date_a = None
                if links_url_sort:
                    try:
                        await detail_page.goto(links_url_sort[0]["url"], wait_until="domcontentloaded", timeout=30_000)
                        await detail_page.wait_for_timeout(2_000)
                        date_a = await detail_page.evaluate(_JSON_LD_DATE_JS)
                    except Exception as e:
                        print(f"  Detail fetch error (A): {e}")
                print(f"  [A] Job #1 datePosted: {date_a or 'not found'}")

                # ── Step 2: Dropdown manipulation ─────────────────────────────
                if dropdown_val == 'Most relevant':
                    print(f"\n  [B] Dropdown shows 'Most relevant' — triggering change to 'Most recent' ...")
                    first_href_before = await page.evaluate(
                        "() => (document.querySelector('a[href*=\"/job/\"]') || {}).href || ''"
                    )
                    await page.select_option('#sortselect', 'Most recent')
                    await page.evaluate("""() => {
                        const sel = document.querySelector('#sortselect');
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLSelectElement.prototype, 'value'
                        ).set;
                        setter.call(sel, 'Most recent');
                        ['input', 'change'].forEach(t =>
                            sel.dispatchEvent(new Event(t, { bubbles: true, cancelable: true }))
                        );
                    }""")
                    rerender_fired = False
                    try:
                        await page.wait_for_function(
                            f"""() => {{
                                const a = document.querySelector('a[href*="/job/"]');
                                return a && a.href !== {repr(first_href_before)};
                            }}""",
                            timeout=15_000,
                        )
                        rerender_fired = True
                        print(f"  Re-render fired — first href changed")
                    except Exception:
                        print(f"  Re-render timed out (15s) — first href did NOT change")

                    links_dropdown_sort = await page.evaluate(_JOB_LINKS_JS)
                    print(f"\n  [B] First {len(links_dropdown_sort)} jobs (dropdown sort):")
                    for i, j in enumerate(links_dropdown_sort, 1):
                        print(f"    {i}. {j['title']}")

                    # Fetch datePosted for job #1 after dropdown change
                    date_b = None
                    if links_dropdown_sort:
                        try:
                            await detail_page.goto(links_dropdown_sort[0]["url"], wait_until="domcontentloaded", timeout=30_000)
                            await detail_page.wait_for_timeout(2_000)
                            date_b = await detail_page.evaluate(_JSON_LD_DATE_JS)
                        except Exception as e:
                            print(f"  Detail fetch error (B): {e}")
                    print(f"  [B] Job #1 datePosted: {date_b or 'not found'}")

                    # ── Compare ────────────────────────────────────────────────
                    urls_a = [j["url"] for j in links_url_sort]
                    urls_b = [j["url"] for j in links_dropdown_sort]
                    lists_match = urls_a == urls_b
                    print(f"\n  {'─'*55}")
                    print(f"  Re-render fired:        {rerender_fired}")
                    print(f"  Job lists identical:    {lists_match}")
                    print(f"  datePosted A (URL):     {date_a or '—'}")
                    print(f"  datePosted B (dropdown):{date_b or '—'}")
                    if lists_match:
                        print(f"  VERDICT: URL sort == dropdown sort — skipping dropdown is SAFE")
                    elif not rerender_fired:
                        print(f"  VERDICT: re-render timed out; lists appear same (URL sort controls order)")
                    else:
                        print(f"  VERDICT: *** LISTS DIFFER — URL sort produces different order than dropdown ***")
                elif dropdown_val == '':
                    print(f"\n  Dropdown is empty — URL sort is already the active sort control.")
                    print(f"  [A] datePosted: {date_a or '—'}")
                    print(f"  VERDICT: URL sort active, no dropdown manipulation needed")
                else:
                    print(f"\n  Dropdown value '{dropdown_val}' — no 'Most relevant' path triggered.")
                    print(f"  [A] datePosted: {date_a or '—'}")

            except Exception as e:
                print(f"  ERROR: {e}")
            finally:
                await page.close()
                await detail_page.close()

        await browser.close()


_FILTER_HEALTH_SITES = [
    (
        "VCU Health",
        "https://careers.vcuhealth.org/us/en/search-results?sortBy=postingdate&descending=true",
        ["Revenue Cycle", "Information Technology", "Administrative Support", "Finance", "Professional"],
    ),
    (
        "Duke Health",
        "https://careers.dukehealth.org/us/en/search-results?sortBy=postingdate&descending=true",
        ["Corporate", "Information Technology", "Revenue Management", "Administrative and Support Services"],
    ),
]


async def probe_phenom_filter_health() -> None:
    """Replicate scraper's exact filter-click sequence for VCU and Duke.

    Reports per site:
      - Sort dropdown value before and after filter clicks
      - Which categories were found / clicked / failed
      - Job count and result count text after filters settle
      - All visible facet names (so we can spot renames or missing items)
      - datePosted from JSON-LD for the first 3 jobs post-filter
      - Page count: paginate until no-more-pages or 8 pages, tracking dates per page

    Run with: python inspect_selectors.py phenom-filter-health
    """
    import re as _re

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        detail_page = await browser.new_page()

        for name, url, categories in _FILTER_HEALTH_SITES:
            print(f"\n{'='*65}\n  {name}\n  URL: {url}\n  Categories: {categories}\n{'='*65}")
            page = await browser.new_page()

            try:
                # ── Load ──────────────────────────────────────────────────────
                print(f"\n  Loading ...")
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

                # Sort dropdown before filter clicks
                sort_before = None
                try:
                    await page.wait_for_selector('#sortselect', timeout=30_000)
                    sort_before = await page.eval_on_selector('#sortselect', 'el => el.value')
                    print(f"  Sort dropdown (before filter): '{sort_before}'")
                except Exception:
                    print(f"  Sort dropdown: #sortselect not found within 30s")

                # ── All facet items visible on page ───────────────────────────
                await page.wait_for_timeout(1_500)
                all_facets = await page.evaluate("""() =>
                    [...document.querySelectorAll('[data-ph-at-id="facet-results-item"]')]
                    .map(el => (el.innerText || el.textContent || '').trim()
                               .replace(/\\s+/g, ' ')
                               .replace(/\\(\\d+\\+?\\)\\s*jobs?/gi, '')
                               .trim())
                    .filter(t => t.length > 0)
                """)
                print(f"\n  All visible facet items ({len(all_facets)}):")
                for f in all_facets:
                    print(f"    '{f}'")

                # ── Panel open logic (mirrors scraper exactly) ─────────────────
                first_item = page.locator('[data-ph-at-id="facet-results-item"]').first
                try:
                    panel_visible = await first_item.is_visible(timeout=2_000)
                except Exception:
                    panel_visible = False
                if not panel_visible:
                    print(f"\n  Panel not visible — trying openers ...")
                    for open_sel in [
                        '[data-ph-at-id="facets-and-filters-button"]',
                        '[data-ph-at-id="facet-heading-link"]',
                        'button:has-text("Filter")',
                        'a:has-text("Refine")',
                    ]:
                        try:
                            opener = page.locator(open_sel).first
                            if await opener.count() and await opener.is_visible(timeout=1_000):
                                await opener.click(timeout=3_000)
                                await page.wait_for_timeout(1_000)
                                print(f"  Opened panel via: {open_sel}")
                                break
                        except Exception:
                            pass
                else:
                    print(f"\n  Panel visible — no opener needed")

                # ── Filter clicks (mirrors scraper exactly) ────────────────────
                print(f"\n  Filter clicks:")
                clicked = 0
                for cat in categories:
                    loc = page.locator('[data-ph-at-id="facet-results-item"]').filter(has_text=cat).first
                    visible = await loc.is_visible()
                    if not visible:
                        print(f"    [{cat}]  NOT VISIBLE — skip (matches scraper log behavior)")
                        continue
                    try:
                        await loc.scroll_into_view_if_needed(timeout=3_000)
                        await loc.click(timeout=5_000)
                        await page.wait_for_timeout(300)
                        print(f"    [{cat}]  clicked OK")
                        clicked += 1
                    except Exception as e:
                        short = str(e)[:80]
                        print(f"    [{cat}]  FAILED — {short}")

                if clicked:
                    await page.wait_for_timeout(2_500)
                print(f"\n  {clicked}/{len(categories)} categories clicked")

                # Sort dropdown after filter clicks
                try:
                    sort_after = await page.eval_on_selector('#sortselect', 'el => el.value')
                    changed = sort_after != sort_before
                    print(f"  Sort dropdown (after  filter): '{sort_after}'"
                          + ("  *** CHANGED ***" if changed else "  (unchanged)"))
                except Exception:
                    print(f"  Sort dropdown: not found after filter")

                # ── Result count text ──────────────────────────────────────────
                result_text = await page.evaluate("""() => {
                    for (const sel of [
                        '[data-ph-at-id="search-results-count"]',
                        '[class*="result-count"]',
                        '[class*="resultCount"]',
                        '[class*="jobs-count"]',
                    ]) {
                        const el = document.querySelector(sel);
                        if (el) return (el.innerText || el.textContent || '').trim();
                    }
                    return null;
                }""")
                if result_text:
                    print(f"  Result count text: '{result_text}'")

                # ── Wait for jobs ──────────────────────────────────────────────
                try:
                    await page.wait_for_function(
                        """() => Array.from(document.querySelectorAll('a[href*="/job/"]'))
                            .filter(a => (a.innerText||a.textContent||'').trim().length > 8)
                            .length >= 1""",
                        timeout=20_000,
                    )
                except Exception:
                    print(f"\n  WARNING: no job links appeared after filter — possible 0-result filter")

                # ── Dates: first 3 jobs on page 1 ─────────────────────────────
                p1_links = await page.evaluate(_JOB_LINKS_JS)
                print(f"\n  Page 1 jobs ({len(p1_links)} shown):")
                p1_dates = []
                for j in p1_links[:3]:
                    try:
                        await detail_page.goto(j["url"], wait_until="domcontentloaded", timeout=25_000)
                        await detail_page.wait_for_timeout(1_500)
                        dp = await detail_page.evaluate(_JSON_LD_DATE_JS)
                    except Exception:
                        dp = "fetch-err"
                    p1_dates.append(dp)
                    print(f"    {dp or '—'}  {j['title']}")

                # ── Paginate: up to 8 pages, record first-job date per page ───
                print(f"\n  Paginating (up to 8 pages):")
                for pg in range(2, 9):
                    next_btn = page.locator('[data-ph-at-id="pagination-next-link"]').last
                    if not await next_btn.is_visible():
                        print(f"    page {pg}: no next button — stop")
                        break
                    first_href = await page.evaluate(
                        "() => (document.querySelector('a[href*=\"/job/\"]') || {}).href || ''"
                    )
                    await next_btn.click()
                    try:
                        await page.wait_for_function(
                            f"""() => {{
                                const a = document.querySelector('a[href*="/job/"]');
                                return a && a.href !== {repr(first_href)};
                            }}""",
                            timeout=30_000,
                        )
                    except Exception:
                        print(f"    page {pg}: next-page load timed out — stop")
                        break

                    pg_links = await page.evaluate(_JOB_LINKS_JS)
                    # Fetch date for just the first job on this page
                    first_date = None
                    if pg_links:
                        try:
                            await detail_page.goto(pg_links[0]["url"], wait_until="domcontentloaded", timeout=25_000)
                            await detail_page.wait_for_timeout(1_000)
                            first_date = await detail_page.evaluate(_JSON_LD_DATE_JS)
                        except Exception:
                            first_date = "fetch-err"
                    print(f"    page {pg}: {len(pg_links)} jobs, first datePosted={first_date or '—'}")

            except Exception as e:
                print(f"\n  TOP-LEVEL ERROR: {e}")
            finally:
                await page.close()

        await detail_page.close()
        await browser.close()


_DUKE_URL = "https://careers.dukehealth.org/us/en/search-results"
_DUKE_CATEGORIES = ["Corporate", "Information Technology", "Revenue Management", "Administrative and Support Services"]


async def probe_duke_sort_fix() -> None:
    """Iterative Duke sort investigation.

    Step 1 — Dropdown options: what values does #sortselect expose?
    Step 2 — Pre-filter sort: what does page 1 look like unfiltered?
    Step 3 — Post-filter, pre-sort: apply categories, read page-1 dates and dropdown value.
    Step 4 — Force 'Most recent' after filter: native-setter dispatch, check re-render.
    Step 5 — Post-sort page-1 dates: are they fresh now?
    Step 6 — Pagination check: does page 2 load cleanly after the sort change?

    Run with: python inspect_selectors.py duke-sort-fix
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        detail = await browser.new_page()

        async def _first3_dates(pg):
            links = await pg.evaluate(_JOB_LINKS_JS)
            out = []
            for j in links[:3]:
                try:
                    await detail.goto(j["url"], wait_until="domcontentloaded", timeout=25_000)
                    await detail.wait_for_timeout(1_200)
                    dp = await detail.evaluate(_JSON_LD_DATE_JS)
                except Exception:
                    dp = "fetch-err"
                out.append((dp, j["title"]))
            return out

        try:
            # ── Step 1: inspect dropdown options ─────────────────────────────
            print(f"\n{'='*65}\n  STEP 1 — Duke dropdown options\n{'='*65}")
            await page.goto(_DUKE_URL, wait_until="domcontentloaded", timeout=60_000)
            try:
                await page.wait_for_selector('#sortselect', timeout=30_000)
                opts = await page.evaluate("""() => {
                    const sel = document.querySelector('#sortselect');
                    return sel ? Array.from(sel.options).map(o => ({val: o.value, text: o.text})) : [];
                }""")
                val = await page.eval_on_selector('#sortselect', 'el => el.value')
                print(f"  Current value: '{val}'")
                print(f"  Options ({len(opts)}):")
                for o in opts:
                    print(f"    value={repr(o['val'])}  text={repr(o['text'])}")
            except Exception as e:
                print(f"  #sortselect error: {e}")

            # ── Step 2: page-1 dates BEFORE filter ────────────────────────────
            print(f"\n{'='*65}\n  STEP 2 — page-1 dates (no filter, URL sort only)\n{'='*65}")
            try:
                await page.wait_for_function(
                    "() => document.querySelectorAll('a[href*=\"/job/\"]').length >= 3",
                    timeout=20_000,
                )
            except Exception:
                pass
            dates_pre = await _first3_dates(page)
            print(f"  First 3 jobs (no filter):")
            for dp, title in dates_pre:
                print(f"    {dp}  {title}")

            # ── Step 3: apply filters, read dropdown and dates ────────────────
            print(f"\n{'='*65}\n  STEP 3 — apply category filter, read page-1 dates\n{'='*65}")
            first_item = page.locator('[data-ph-at-id="facet-results-item"]').first
            try:
                panel_visible = await first_item.is_visible(timeout=2_000)
            except Exception:
                panel_visible = False
            if not panel_visible:
                print("  Panel not visible — skipping (unexpected for Duke)")

            clicked = 0
            for cat in _DUKE_CATEGORIES:
                loc = page.locator('[data-ph-at-id="facet-results-item"]').filter(has_text=cat).first
                if not await loc.is_visible():
                    print(f"  [{cat}] NOT VISIBLE")
                    continue
                try:
                    await loc.scroll_into_view_if_needed(timeout=3_000)
                    await loc.click(timeout=5_000)
                    await page.wait_for_timeout(300)
                    print(f"  [{cat}] clicked")
                    clicked += 1
                except Exception as e:
                    print(f"  [{cat}] FAILED: {str(e)[:60]}")
            await page.wait_for_timeout(2_500)
            print(f"  {clicked}/{len(_DUKE_CATEGORIES)} clicked")

            sort_post_filter = await page.eval_on_selector('#sortselect', 'el => el.value')
            print(f"  Dropdown after filter: '{sort_post_filter}'")

            dates_post_filter = await _first3_dates(page)
            print(f"  First 3 jobs (filtered, pre-sort):")
            for dp, title in dates_post_filter:
                print(f"    {dp}  {title}")

            # ── Step 4: force 'Most recent' via native setter ─────────────────
            print(f"\n{'='*65}\n  STEP 4 — force sort='Most recent' after filter\n{'='*65}")
            first_href = await page.evaluate(
                "() => (document.querySelector('a[href*=\"/job/\"]') || {}).href || ''"
            )
            print(f"  first_href before sort change: {first_href[-60:]!r}")
            await page.select_option('#sortselect', 'Most recent')
            await page.evaluate("""() => {
                const sel = document.querySelector('#sortselect');
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLSelectElement.prototype, 'value'
                ).set;
                setter.call(sel, 'Most recent');
                ['input', 'change'].forEach(t =>
                    sel.dispatchEvent(new Event(t, { bubbles: true, cancelable: true }))
                );
            }""")
            rerender = False
            try:
                await page.wait_for_function(
                    f"""() => {{
                        const a = document.querySelector('a[href*="/job/"]');
                        return a && a.href !== {repr(first_href)};
                    }}""",
                    timeout=15_000,
                )
                rerender = True
                print(f"  Re-render fired — first href changed")
            except Exception:
                print(f"  Re-render timed out (15s) — first href unchanged")

            sort_after_change = await page.eval_on_selector('#sortselect', 'el => el.value')
            print(f"  Dropdown after sort change: '{sort_after_change}'")

            # ── Step 4b: two-step seed — 'Most relevant' → 'Most recent' ────────
            # UVA works because its React state is 'Most relevant'; seeding Duke
            # to that state first may trigger React to recognise the next transition.
            print(f"\n{'='*65}\n  STEP 4b — seed 'Most relevant' first, then 'Most recent'\n{'='*65}")
            await page.select_option('#sortselect', 'Most relevant')
            await page.evaluate("""() => {
                const sel = document.querySelector('#sortselect');
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLSelectElement.prototype, 'value'
                ).set;
                setter.call(sel, 'Most relevant');
                ['input', 'change'].forEach(t =>
                    sel.dispatchEvent(new Event(t, { bubbles: true, cancelable: true }))
                );
            }""")
            await page.wait_for_timeout(1_500)
            seed_val = await page.eval_on_selector('#sortselect', 'el => el.value')
            print(f"  After seed to 'Most relevant': dropdown='{seed_val}'")
            first_href_4b = await page.evaluate(
                "() => (document.querySelector('a[href*=\"/job/\"]') || {}).href || ''"
            )
            await page.select_option('#sortselect', 'Most recent')
            await page.evaluate("""() => {
                const sel = document.querySelector('#sortselect');
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLSelectElement.prototype, 'value'
                ).set;
                setter.call(sel, 'Most recent');
                ['input', 'change'].forEach(t =>
                    sel.dispatchEvent(new Event(t, { bubbles: true, cancelable: true }))
                );
            }""")
            rerender_4b = False
            try:
                await page.wait_for_function(
                    f"""() => {{
                        const a = document.querySelector('a[href*="/job/"]');
                        return a && a.href !== {repr(first_href_4b)};
                    }}""",
                    timeout=15_000,
                )
                rerender_4b = True
                print(f"  Re-render fired after two-step seed")
            except Exception:
                print(f"  Re-render timed out (15s) after two-step seed")
            val_4b = await page.eval_on_selector('#sortselect', 'el => el.value')
            print(f"  Dropdown after: '{val_4b}'")
            if rerender_4b:
                dates_4b = await _first3_dates(page)
                print(f"  First 3 jobs after 4b:")
                for dp, title in dates_4b:
                    print(f"    {dp}  {title}")

            # ── Step 4c: visual click on the option element ───────────────────
            # Bypasses React event wiring — simulates real user interaction.
            print(f"\n{'='*65}\n  STEP 4c — visual click on #sortselect option element\n{'='*65}")
            first_href_4c = await page.evaluate(
                "() => (document.querySelector('a[href*=\"/job/\"]') || {}).href || ''"
            )
            try:
                await page.click('#sortselect', timeout=5_000)
                await page.wait_for_timeout(500)
                # Select 'Most recent' option by clicking it
                opt = page.locator('#sortselect option[value="Most recent"]')
                if await opt.count():
                    # Dispatch a direct click on the <select> with the value set first
                    await page.evaluate("""() => {
                        const sel = document.querySelector('#sortselect');
                        sel.value = 'Most recent';
                        sel.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        sel.dispatchEvent(new MouseEvent('mouseup',   {bubbles: true}));
                        sel.dispatchEvent(new MouseEvent('click',     {bubbles: true}));
                        sel.dispatchEvent(new Event('input',  {bubbles: true}));
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                    }""")
                    print(f"  Dispatched mousedown/up/click/input/change on #sortselect")
                else:
                    print(f"  'Most recent' option element not found")
            except Exception as e:
                print(f"  Visual click error: {str(e)[:80]}")
            rerender_4c = False
            try:
                await page.wait_for_function(
                    f"""() => {{
                        const a = document.querySelector('a[href*="/job/"]');
                        return a && a.href !== {repr(first_href_4c)};
                    }}""",
                    timeout=15_000,
                )
                rerender_4c = True
                print(f"  Re-render fired after visual click")
            except Exception:
                print(f"  Re-render timed out (15s) after visual click")
            val_4c = await page.eval_on_selector('#sortselect', 'el => el.value')
            print(f"  Dropdown after: '{val_4c}'")
            if rerender_4c:
                dates_4c = await _first3_dates(page)
                print(f"  First 3 jobs after 4c:")
                for dp, title in dates_4c:
                    print(f"    {dp}  {title}")

            # ── Step 5: page-1 dates after sort change ────────────────────────
            print(f"\n{'='*65}\n  STEP 5 — page-1 dates after forced sort\n{'='*65}")
            dates_post_sort = await _first3_dates(page)
            print(f"  First 3 jobs (filtered + forced sort):")
            for dp, title in dates_post_sort:
                print(f"    {dp}  {title}")

            # Summary table
            print(f"\n  {'-'*55}")
            print(f"  Re-render: 4a={rerender}  4b={rerender_4b}  4c={rerender_4c}")
            print(f"  Page-1 job #1 date: BEFORE filter={dates_pre[0][0] if dates_pre else '-'}  "
                  f"after filter={dates_post_filter[0][0] if dates_post_filter else '-'}  "
                  f"after sort={dates_post_sort[0][0] if dates_post_sort else '-'}")

            # ── Step 6: paginate to page 2, check date ────────────────────────
            # 6a: compare unfiltered vs text-filtered first_href (Theory A)
            # 6b: if unfiltered times out, retry wait with text-filtered selector (Theory A fix)
            # Step 7: reload without sortBy param and repeat (Theory B)
            print(f"\n{'='*65}\n  STEP 6 — paginate to page 2 (does it load? what date?)\n{'='*65}")
            next_btn = page.locator('[data-ph-at-id="pagination-next-link"]').last
            if not await next_btn.is_visible():
                print(f"  No next button visible — stopped at page 1")
            else:
                # 6a: capture both selector variants BEFORE clicking
                href_unfiltered = await page.evaluate(
                    "() => (document.querySelector('a[href*=\"/job/\"]') || {}).href || ''"
                )
                href_filtered = await page.evaluate("""() => {
                    const a = Array.from(document.querySelectorAll('a[href*="/job/"]'))
                        .find(a => (a.innerText || a.textContent || '').trim().length > 8);
                    return a ? a.href : '';
                }""")
                print(f"  6a — first_href (unfiltered): ...{href_unfiltered[-70:]!r}")
                print(f"  6a — first_href (filtered):   ...{href_filtered[-70:]!r}")
                print(f"  6a — Same? {href_unfiltered == href_filtered}  {'<-- Theory A CONFIRMED: breadcrumb is first' if href_unfiltered != href_filtered else '(no breadcrumb divergence)'}")

                await next_btn.click()

                # 6: try unfiltered selector (current scraper logic)
                p2_loaded_unfiltered = False
                try:
                    await page.wait_for_function(
                        f"""() => {{
                            const a = document.querySelector('a[href*="/job/"]');
                            return a && a.href !== {repr(href_unfiltered)};
                        }}""",
                        timeout=35_000,
                    )
                    p2_loaded_unfiltered = True
                    print(f"  6  — unfiltered wait: PAGE 2 LOADED (DOM changed within 35s)")
                except Exception:
                    print(f"  6  — unfiltered wait: TIMED OUT (35s) — this is the current scraper failure")

                # 6b: if unfiltered timed out, check if filtered selector NOW shows new href
                # (page may have loaded but unfiltered first link stayed the same)
                href_filtered_now = await page.evaluate("""() => {
                    const a = Array.from(document.querySelectorAll('a[href*="/job/"]'))
                        .find(a => (a.innerText || a.textContent || '').trim().length > 8);
                    return a ? a.href : '';
                }""")
                filtered_changed = href_filtered_now != href_filtered and href_filtered_now != ''
                print(f"  6b — filtered href after click: ...{href_filtered_now[-70:]!r}")
                print(f"  6b — Filtered changed? {filtered_changed}  {'<-- Theory A fix WORKS: page 2 DID load' if filtered_changed and not p2_loaded_unfiltered else ''}")

                if p2_loaded_unfiltered or filtered_changed:
                    p2_links = await page.evaluate(_JOB_LINKS_JS)
                    p2_date = None
                    if p2_links:
                        try:
                            await detail.goto(p2_links[0]["url"], wait_until="domcontentloaded", timeout=25_000)
                            await detail.wait_for_timeout(1_200)
                            p2_date = await detail.evaluate(_JSON_LD_DATE_JS)
                        except Exception:
                            p2_date = "fetch-err"
                    print(f"  Page 2 first job: {p2_date}  {p2_links[0]['title'] if p2_links else '—'}")
                else:
                    print(f"  Page 2: neither selector loaded new content — genuine pagination failure")

            # ── Step 7: reload WITHOUT sortBy param, repeat pagination (Theory B) ─
            print(f"\n{'='*65}\n  STEP 7 — reload without sortBy param (Theory B)\n{'='*65}")
            _DUKE_URL_NO_SORT = "https://careers.dukehealth.org/us/en/search-results"
            await page.goto(_DUKE_URL_NO_SORT, wait_until="domcontentloaded", timeout=60_000)
            try:
                await page.wait_for_selector('#sortselect', timeout=30_000)
                sort_7 = await page.eval_on_selector('#sortselect', 'el => el.value')
                print(f"  Dropdown after load (no sort param): '{sort_7}'")
            except Exception:
                print(f"  #sortselect not found")
            # Re-apply categories
            clicked7 = 0
            for cat in _DUKE_CATEGORIES:
                loc = page.locator('[data-ph-at-id="facet-results-item"]').filter(has_text=cat).first
                if not await loc.is_visible():
                    continue
                try:
                    await loc.scroll_into_view_if_needed(timeout=3_000)
                    await loc.click(timeout=5_000)
                    await page.wait_for_timeout(300)
                    clicked7 += 1
                except Exception:
                    pass
            if clicked7:
                await page.wait_for_timeout(2_500)
            print(f"  {clicked7}/{len(_DUKE_CATEGORIES)} categories clicked")

            next_btn7 = page.locator('[data-ph-at-id="pagination-next-link"]').last
            if not await next_btn7.is_visible():
                print(f"  No next button visible after filter (may be only 1 page)")
            else:
                href_unf7 = await page.evaluate(
                    "() => (document.querySelector('a[href*=\"/job/\"]') || {}).href || ''"
                )
                href_flt7 = await page.evaluate("""() => {
                    const a = Array.from(document.querySelectorAll('a[href*="/job/"]'))
                        .find(a => (a.innerText || a.textContent || '').trim().length > 8);
                    return a ? a.href : '';
                }""")
                print(f"  first_href unfiltered: ...{href_unf7[-70:]!r}")
                print(f"  first_href filtered:   ...{href_flt7[-70:]!r}")
                await next_btn7.click()
                p2_7 = False
                try:
                    await page.wait_for_function(
                        f"""() => {{
                            const a = Array.from(document.querySelectorAll('a[href*="/job/"]'))
                                .find(a => (a.innerText || a.textContent || '').trim().length > 8);
                            return a && a.href !== {repr(href_flt7)};
                        }}""",
                        timeout=35_000,
                    )
                    p2_7 = True
                    print(f"  7 — filtered wait: PAGE 2 LOADED  {'<-- Theory B CONFIRMED: no-sort URL + filtered selector works' if not p2_loaded_unfiltered else ''}")
                except Exception:
                    print(f"  7 — filtered wait: TIMED OUT (genuine pagination failure on no-sort URL too)")
                if p2_7:
                    p2_links7 = await page.evaluate(_JOB_LINKS_JS)
                    print(f"  Page 2 first job: {p2_links7[0]['title'] if p2_links7 else '—'}")

        except Exception as e:
            print(f"\n  TOP-LEVEL ERROR: {e}")
        finally:
            await page.close()
            await detail.close()
        await browser.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "musc-cxs":
        asyncio.run(probe_musc_cxs())
    elif len(sys.argv) > 1 and sys.argv[1] == "workday-cxs":
        asyncio.run(probe_workday_cxs())
    elif len(sys.argv) > 1 and sys.argv[1] == "phenom-categories":
        asyncio.run(probe_phenom_categories())
    elif len(sys.argv) > 1 and sys.argv[1] == "emory-categories":
        asyncio.run(probe_emory_categories())
    elif len(sys.argv) > 1 and sys.argv[1] == "phenom-sort":
        asyncio.run(probe_phenom_sort())
    elif len(sys.argv) > 1 and sys.argv[1] == "phenom-filter-health":
        asyncio.run(probe_phenom_filter_health())
    elif len(sys.argv) > 1 and sys.argv[1] == "duke-sort-fix":
        asyncio.run(probe_duke_sort_fix())
    else:
        asyncio.run(main())
