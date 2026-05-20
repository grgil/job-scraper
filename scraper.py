import asyncio
import json
import os
import re
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Force UTF-8 console output — Windows cp1252 crashes on Unicode job titles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "scraper.log"
load_dotenv(BASE_DIR / ".env")

EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
EMAIL_APP_PASS = os.environ.get("EMAIL_APP_PASS", "")

# Phenom People ATS
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
    {
        "name": "Duke Health (Lake Norman)",
        "url": (
            "https://careers.dukehealth.org/us/en/search-results"
            "?sortBy=postingdate&descending=true"
        ),
        "location_keywords": {"lake norman", "mooresville"},
        "max_pages": 12,
    },
    {
        "name": "Duke Health (Remote)",
        "url": (
            "https://careers.dukehealth.org/us/en/search-results"
            "?sortBy=postingdate&descending=true"
        ),
        "remote_only": True,
        "max_pages": 12,
    },
]

# Workday ATS
# remote_only=True: only include jobs whose JSON-LD location contains remote keywords
WORKDAY_SITES = [
    {"name": "Bon Secours",     "url": "https://easyservice.wd5.myworkdayjobs.com/BonSecoursMercyHealthCareers", "remote_only": False},
    {"name": "MUSC",            "url": "https://musc.wd1.myworkdayjobs.com/MUSC",                               "remote_only": True, "max_pages": 12},
    {"name": "OhioHealth",      "url": "https://ohiohealth.wd5.myworkdayjobs.com/OhioHealthJobs",               "remote_only": True, "max_pages": 12},
    {"name": "Prisma Health (Greenville)", "url": "https://prismahealth.wd5.myworkdayjobs.com/PrismaHealthCorporate", "location_keywords": {"greenville", "simpsonville", "easley", "patewood"}, "max_pages": 12},
    {"name": "Prisma Health (Remote)",    "url": "https://prismahealth.wd5.myworkdayjobs.com/PrismaHealthCorporate", "remote_only": True, "max_pages": 12},
    {"name": "Humana",          "url": "https://humana.wd5.myworkdayjobs.com/Humana_External_Career_Site",       "remote_only": True, "max_pages": 12},
    {"name": "Elevance Health", "url": "https://elevancehealth.wd1.myworkdayjobs.com/ANT",                       "remote_only": True, "max_pages": 12},
    {"name": "Cigna",           "url": "https://cigna.wd5.myworkdayjobs.com/cignacareers",                       "remote_only": True, "max_pages": 12},
    # Nashville/Chattanooga hybrid — no filter; all jobs (remote + on-site) go to regional email
    {"name": "VUMC",            "url": "https://vumc.wd1.myworkdayjobs.com/vumccareers",                        "remote_only": False, "max_pages": 15},
    # Roanoke/Richmond hybrid — no filter; all jobs (remote + on-site) go to regional email
    {"name": "Carilion Clinic", "url": "https://carilionclinic.wd12.myworkdayjobs.com/External_Careers",        "remote_only": False, "max_pages": 12},
    # Wellstar — Atlanta metro local + remote split
    {"name": "Wellstar Health (Atlanta)", "url": "https://wellstar.wd1.myworkdayjobs.com/wellstarcareers",
     "location_keywords": {"atlanta", "marietta", "smyrna", "kennesaw", "woodstock", "cartersville", "douglasville", "newnan", "austell", "acworth"}, "max_pages": 12},
    {"name": "Wellstar Health (Remote)",  "url": "https://wellstar.wd1.myworkdayjobs.com/wellstarcareers",      "remote_only": True, "max_pages": 12},
    # Remote-only vendors / AMCs
    {"name": "WVU Medicine",    "url": "https://wvumedicine.wd1.myworkdayjobs.com/UHA",                         "remote_only": True, "max_pages": 12},
    {"name": "Solventum (3M HIS)", "url": "https://healthcare.wd1.myworkdayjobs.com/Search",                    "remote_only": True, "max_pages": 12},
    {"name": "Veradigm",        "url": "https://veradigm.wd12.myworkdayjobs.com/VR",                            "remote_only": True, "max_pages": 6},
    # Waystar — Atlanta + Louisville local + remote split
    {"name": "Waystar (Atlanta / Louisville)", "url": "https://waystar.wd1.myworkdayjobs.com/Waystar",
     "location_keywords": {"atlanta", "louisville"}, "max_pages": 6},
    {"name": "Waystar (Remote)", "url": "https://waystar.wd1.myworkdayjobs.com/Waystar",                        "remote_only": True, "max_pages": 6},
    # Centene — both wd5 tenant and jobs.centene.com custom portal blocked/timing out
    # Atrium Health (aah.wd5) — held pending batch refresh fix
    # CorroHealth — Workday maintenance page, blocked
]

REMOTE_LOCATION_KEYWORDS = {"remote", "work at home", "work from home", "virtual", "telecommute", "home based"}

# Title exclusion filter — applied at email-build time, not scrape time
TITLE_EXCLUDE_PHRASES = {
    # Nursing
    "registered nurse", "licensed practical", "licensed vocational",
    "nurse practitioner", "certified nursing assistant", "certified nurse anesthetist",
    "clinical nurse specialist", "nurse midwife", "travel nurse",
    "charge nurse", "staff nurse", "nursing supervisor", "nurse manager",
    "patient care tech", "patient care assistant", "nursing",
    # Allied health
    "physical therapist", "physical therapy assistant",
    "occupational therapist", "occupational therapy assistant", "speech",
    "speech language", "speech-language", "audiologist", "physiologist",
    "respiratory therapist", "respiratory therapy", "advanced practice",
    # Imaging
    "radiologic technolog", "radiology tech", "x-ray tech",
    "ultrasound tech", "sonographer", "mri tech", "ct tech",
    "nuclear medicine tech", "mammograph", "pathologist",
    # Lab / procedural
    "lab technician", "laboratory technician", "phlebotomist",
    "histotechnologist", "cytotechnologist", "medical laboratory",
    "surgical tech", "scrub tech", "sterile processing", "central sterile",
    "ekg tech", "eeg tech", "cardiac tech", "echo tech",
    "dialysis tech", "ophthalm", "optometr",
    "dental assistant", "dental hygienist",
    # Pharmacy (bedside)
    "pharmacist", "pharmacy technician",
    # Clinical support
    "medical assistant", "medical scribe",
    "patient transporter", "patient transport",
    # Facilities / non-clinical
    "housekeeper", "housekeeping", "environmental services",
    "food service", "food and nutrition", "dietary aide",
    "dietary tech", "dietary assistant",
    "security officer", "security guard", "public safety officer",
    "chaplain", "pastoral care",
    "maintenance technician", "facilities technician",
    "valet", "groundskeeper", "supply chain",
    # Front desk / scheduling
    "patient scheduler", "appointment scheduler",
    "front desk", "receptionist", "welcome",
    "parking assistant",
    # Rad tech shorthand (not caught by "radiologic technolog" / "radiology tech")
    "rad tech",
    # Pharmacy tech shorthand (not caught by "pharmacy technician")
    "pharmacy tech",
    # Bedside support / CNA-adjacent
    "care partner", "patient support assistant",
    # Allied health / EMS
    "athletic trainer", "ambulance attendant",
    "rehab aide", "rehabilitation aide",
    "polysomnography",
    "audiology",
    # Nursing roles not caught by "registered nurse" / "nurse manager"
    "nurse residency", "nurse case manager",
    "lic practical",        # "Lic Practical Nurse" abbreviation
    "nurse supervisor",     # "nursing supervisor" was in list; "nurse supervisor" wasn't
    "nurse mgr",            # forward abbreviation
    "mgr, nurse",           # "Mgr, Nurse" reversed form seen in Emory listings
    # Dietary variant
    "kitchen",
    # ED tech (not caught by "patient care tech")
    "emergency department tech", "ed tech",
    # Mental health therapist (direct patient care; not behavioral health admin/management)
    "primary therapist",
    # Lab / clinical tech shorthand (not caught by "lab technician")
    "med asst",             # "Coord, Med Asst" and similar abbreviations
    "neurodiagnostic",      # nerve conduction / neurodiagnostic tech roles
    "specimen processing",  # lab specimen handling tech (distinct from sterile processing)
}
TITLE_EXCLUDE_WORDS = {"rn", "lpn", "lvn", "cna", "crna", "cns", "emt", "paramedic",
                       "scribe", "app", "sales",
                       "np",    # NP (nurse practitioner) — catches "NP/PA" and similar
                       "acnp",  # Acute Care Nurse Practitioner credential
                       }

# iCIMS ATS — URLs need verification (public vs employee portals)
ICIMS_SITES: list[dict] = []

# Emory Healthcare — DirectEmployers/Jobsyn SPA (emory.jobs)
# Scraper uses response interception; direct API fetch always returns 403.
# max_pages kept at 20 during verification; remove cap once confirmed reliable.
EMORY_SITES = [
    {
        "name": "Emory Healthcare (Atlanta)",
        "page_url": "https://emory.jobs/jobs/",
        "location_keywords": {
            "atlanta", "decatur", "johns creek", "dunwoody", "sandy springs",
            "tucker", "lithonia", "brookhaven", "clarkston", "college park",
            "east point", "forest park",
        },
        "remote_only": False,
    },
]

TODAY = date.today() - timedelta(days=1)


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


def _log(message: str) -> None:
    entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(entry)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(entry + "\n")


def _page_freshness(newest_seen: date | None) -> str:
    if newest_seen is None:
        return "newest_seen=unknown"
    days_old = (date.today() - newest_seen).days
    return f"newest_seen={newest_seen} ({days_old}d old)"


# ---------------------------------------------------------------------------
# Shared detail extractor — platform-agnostic JSON-LD parsing (all 3 ATSes)
# ---------------------------------------------------------------------------

async def _get_job_details(page, job_url: str) -> dict | None:
    try:
        await page.goto(job_url, wait_until="domcontentloaded", timeout=60_000)
    except Exception:
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


# ---------------------------------------------------------------------------
# Phenom People (UVA Health)
# ---------------------------------------------------------------------------

async def _get_job_links(page, url: str) -> list[dict]:
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    has_sort_dropdown = True
    try:
        await page.wait_for_selector('#sortselect', timeout=30_000)
    except PlaywrightTimeoutError:
        has_jobs = await page.evaluate(
            "() => document.querySelectorAll('a[href*=\"/job/\"]').length > 0"
        )
        if not has_jobs:
            _log("  WARNING: Sort dropdown not found and no job links — page may not have loaded")
            return []
        _log("  Sort dropdown not found — assuming URL-level sort is active, proceeding")
        has_sort_dropdown = False

    if has_sort_dropdown:
        current_sort = await page.eval_on_selector('#sortselect', 'el => el.value')
        _log(f"  Sort dropdown value after load: '{current_sort}'")

        first_href_before = await page.evaluate(
            """() => (document.querySelector('a[href*="/job/"]') || {}).href || ''"""
        )

        if current_sort == 'Most relevant':
            _log("  Selecting 'Most recent' and dispatching change event ...")
            await page.select_option('#sortselect', 'Most recent')
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


async def scrape_site(browser, site: dict, since_date: date) -> tuple[list[dict], int, date | None]:
    _log(f"Scraping {site['name']} (Phenom People) ...")
    search_page = await browser.new_page()
    detail_page = await browser.new_page()
    try:
        links = await _get_job_links(search_page, site["url"])
        page_num = 1
        _log(f"  {len(links)} job(s) on page {page_num}")

        results = []
        skipped = 0
        consecutive_empty = 0
        newest_seen: date | None = None

        while links:
            page_matches = 0
            for i, job in enumerate(links, 1):
                details = await _get_job_details(detail_page, job["url"])

                if details is None:
                    _log(f"  [p{page_num}/{i}] No JSON-LD — {job['title'][:60]}")
                    skipped += 1
                    continue

                dp = details["date_posted"]
                if newest_seen is None or dp > newest_seen:
                    newest_seen = dp
                if dp == since_date:
                    loc = (details.get("location") or "").lower()
                    loc_kw = site.get("location_keywords", set())
                    is_remote = any(k in loc for k in REMOTE_LOCATION_KEYWORDS)
                    if site.get("remote_only"):
                        qualifies = is_remote
                    elif loc_kw:
                        qualifies = any(k in loc for k in loc_kw)
                    else:
                        qualifies = True
                    if qualifies:
                        results.append({**job, **details})
                        _log(f"  [p{page_num}/{i}] MATCH {dp}{'[r]' if is_remote else '  '} — {job['title'][:60]}")
                    else:
                        _log(f"  [p{page_num}/{i}] SKIP  {dp} ({details.get('location')}) — {job['title'][:60]}")
                if dp >= since_date:
                    page_matches += 1

            freshness = _page_freshness(newest_seen)
            if page_matches == 0:
                consecutive_empty += 1
                _log(
                    f"  Page {page_num}: 0 recent ({consecutive_empty}/5), {freshness}"
                    f" — {'stopping' if consecutive_empty >= 5 else 'next'}"
                )
                if consecutive_empty >= 5:
                    break
            else:
                _log(f"  Page {page_num}: {page_matches} recent, {freshness}")
                consecutive_empty = 0

            page_num += 1
            max_pages = site.get("max_pages")
            if max_pages and page_num > max_pages:
                _log(f"  Page limit ({max_pages}) reached — stopping")
                break
            links = await _get_next_page_links(search_page)
        else:
            _log("  No more pages")

        return results, skipped, newest_seen
    finally:
        await search_page.close()
        await detail_page.close()


# ---------------------------------------------------------------------------
# Workday ATS
# ---------------------------------------------------------------------------

async def _get_workday_job_links(page, site: dict) -> list[dict]:
    _log(f"  Loading {site['url']}")
    await page.goto(site["url"], wait_until="networkidle", timeout=90_000)
    try:
        await page.wait_for_function(
            """() => document.querySelectorAll(
                'a[data-automation-id="jobTitle"]'
            ).length >= 5""",
            timeout=30_000,
        )
    except PlaywrightTimeoutError:
        _log(f"  {site['name']}: WARN — Workday job titles timed out"
             " (selector: a[data-automation-id=jobTitle])")
        return []
    return await page.evaluate("""() => {
        const seen = new Set();
        const results = [];
        document.querySelectorAll('a[data-automation-id="jobTitle"]').forEach(a => {
            if (seen.has(a.href)) return;
            seen.add(a.href);
            const title = (a.innerText || a.textContent || '').trim();
            if (title.length > 3) results.push({ title, url: a.href });
        });
        return results;
    }""")


async def _get_workday_next_page(page) -> list[dict]:
    next_btn = page.locator('button[data-uxi-element-id="next"], button[aria-label="next"]').last
    if not await next_btn.is_visible() or not await next_btn.is_enabled():
        return []
    first_href = await page.evaluate(
        """() => (document.querySelector('a[data-automation-id="jobTitle"]') || {}).href || ''"""
    )
    await next_btn.click()
    try:
        await page.wait_for_function(
            f"""() => {{
                const a = document.querySelector('a[data-automation-id="jobTitle"]');
                return a && a.href !== {repr(first_href)};
            }}""",
            timeout=20_000,
        )
        await page.wait_for_function(
            """() => document.querySelectorAll(
                'a[data-automation-id="jobTitle"]'
            ).length >= 5""",
            timeout=15_000,
        )
    except PlaywrightTimeoutError:
        _log("  WARN — Workday next page did not load new content")
        return []
    return await page.evaluate("""() => {
        const seen = new Set();
        const results = [];
        document.querySelectorAll('a[data-automation-id="jobTitle"]').forEach(a => {
            if (seen.has(a.href)) return;
            seen.add(a.href);
            const title = (a.innerText || a.textContent || '').trim();
            if (title.length > 3) results.push({ title, url: a.href });
        });
        return results;
    }""")


async def scrape_workday_site(browser, site: dict, since_date: date) -> tuple[list[dict], int, date | None]:
    _log(f"Scraping {site['name']} (Workday) ...")
    search_page = await browser.new_page()
    detail_page = await browser.new_page()
    try:
        links = await _get_workday_job_links(search_page, site)
        page_num = 1
        _log(f"  {len(links)} job(s) on page {page_num}")

        results = []
        skipped = 0
        consecutive_empty = 0
        newest_seen: date | None = None

        while links:
            page_matches = 0
            for i, job in enumerate(links, 1):
                details = await _get_job_details(detail_page, job["url"])

                if details is None:
                    _log(f"  [p{page_num}/{i}] No JSON-LD — {job['title'][:60]}")
                    skipped += 1
                    continue

                dp = details["date_posted"]
                if newest_seen is None or dp > newest_seen:
                    newest_seen = dp
                if dp == since_date:
                    loc = (details.get("location") or "").lower()
                    loc_kw = site.get("location_keywords", set())
                    is_remote = any(k in loc for k in REMOTE_LOCATION_KEYWORDS)
                    if site.get("remote_only"):
                        qualifies = is_remote
                    elif loc_kw:
                        qualifies = any(k in loc for k in loc_kw)
                    else:
                        qualifies = True
                    if qualifies:
                        results.append({**job, **details})
                        _log(f"  [p{page_num}/{i}] MATCH {dp}{'[r]' if is_remote else '  '} — {job['title'][:60]}")
                    else:
                        _log(f"  [p{page_num}/{i}] SKIP  {dp} ({details.get('location')}) — {job['title'][:60]}")
                if dp >= since_date:
                    page_matches += 1

            freshness = _page_freshness(newest_seen)
            if page_matches == 0:
                consecutive_empty += 1
                _log(
                    f"  Page {page_num}: 0 recent ({consecutive_empty}/5), {freshness}"
                    f" — {'stopping' if consecutive_empty >= 5 else 'next'}"
                )
                if consecutive_empty >= 5:
                    break
            else:
                _log(f"  Page {page_num}: {page_matches} recent, {freshness}")
                consecutive_empty = 0

            page_num += 1
            max_pages = site.get("max_pages")
            if max_pages and page_num > max_pages:
                _log(f"  Page limit ({max_pages}) reached — stopping")
                break
            links = await _get_workday_next_page(search_page)
        else:
            _log("  No more pages")

        return results, skipped, newest_seen
    finally:
        await search_page.close()
        await detail_page.close()


# ---------------------------------------------------------------------------
# iCIMS ATS
# ---------------------------------------------------------------------------

async def _get_icims_job_links(page, site: dict) -> list[dict]:
    sep = "&" if "?" in site["url"] else "?"
    url = site["url"] + sep + "ss=1&sortby=date&in_jsch=1"
    _log(f"  Loading {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        await page.wait_for_selector(
            'div.iCIMS_JobsTable, div[class*="iCIMS_Jobs"], [id*="icims-jobs"]',
            timeout=30_000,
        )
    except PlaywrightTimeoutError:
        _log(
            f"  {site['name']}: WARN — iCIMS job table not found"
            " (selectors: .iCIMS_JobsTable, [class*=iCIMS_Jobs])"
        )
        return []
    return await page.evaluate("""() => {
        const seen = new Set();
        const results = [];
        document.querySelectorAll([
            '.iCIMS_JobsTable a[href*="/jobs/"]',
            '[class*="iCIMS_Jobs"] a[href*="/jobs/"]',
            'a[href*="/jobs/"][class*="iCIMS"]',
        ].join(', ')).forEach(a => {
            if (seen.has(a.href)) return;
            seen.add(a.href);
            const title = (a.innerText || a.textContent || '').trim();
            if (title.length > 3) results.push({ title, url: a.href });
        });
        return results;
    }""")


async def _get_icims_next_page(page) -> list[dict]:
    next_link = page.locator('a[title="Next Page"], a[aria-label="Next"]').last
    if not await next_link.is_visible():
        return []
    first_href = await page.evaluate(
        """() => (document.querySelector(
            '.iCIMS_JobsTable a[href*="/jobs/"], [class*="iCIMS_Jobs"] a[href*="/jobs/"]'
        ) || {}).href || ''"""
    )
    await next_link.click()
    try:
        await page.wait_for_function(
            f"""() => {{
                const a = document.querySelector(
                    '.iCIMS_JobsTable a[href*="/jobs/"], [class*="iCIMS_Jobs"] a[href*="/jobs/"]'
                );
                return a && a.href !== {repr(first_href)};
            }}""",
            timeout=20_000,
        )
        await page.wait_for_selector(
            'div.iCIMS_JobsTable, div[class*="iCIMS_Jobs"]',
            timeout=15_000,
        )
    except PlaywrightTimeoutError:
        _log("  WARN — iCIMS next page did not load new content")
        return []
    return await page.evaluate("""() => {
        const seen = new Set();
        const results = [];
        document.querySelectorAll([
            '.iCIMS_JobsTable a[href*="/jobs/"]',
            '[class*="iCIMS_Jobs"] a[href*="/jobs/"]',
            'a[href*="/jobs/"][class*="iCIMS"]',
        ].join(', ')).forEach(a => {
            if (seen.has(a.href)) return;
            seen.add(a.href);
            const title = (a.innerText || a.textContent || '').trim();
            if (title.length > 3) results.push({ title, url: a.href });
        });
        return results;
    }""")


async def scrape_icims_site(browser, site: dict, since_date: date) -> tuple[list[dict], int, date | None]:
    _log(f"Scraping {site['name']} (iCIMS) ...")
    search_page = await browser.new_page()
    detail_page = await browser.new_page()
    try:
        links = await _get_icims_job_links(search_page, site)
        page_num = 1
        _log(f"  {len(links)} job(s) on page {page_num}")

        results = []
        skipped = 0
        consecutive_empty = 0
        newest_seen: date | None = None

        while links:
            page_matches = 0
            for i, job in enumerate(links, 1):
                details = await _get_job_details(detail_page, job["url"])

                if details is None:
                    _log(f"  [p{page_num}/{i}] No JSON-LD — {job['title'][:60]}")
                    skipped += 1
                    continue

                dp = details["date_posted"]
                if newest_seen is None or dp > newest_seen:
                    newest_seen = dp
                if dp == since_date:
                    results.append({**job, **details})
                    _log(f"  [p{page_num}/{i}] MATCH {dp}   — {job['title'][:60]}")
                if dp >= since_date:
                    page_matches += 1

            freshness = _page_freshness(newest_seen)
            if page_matches == 0:
                consecutive_empty += 1
                _log(
                    f"  Page {page_num}: 0 recent ({consecutive_empty}/5), {freshness}"
                    f" — {'stopping' if consecutive_empty >= 5 else 'next'}"
                )
                if consecutive_empty >= 5:
                    break
            else:
                _log(f"  Page {page_num}: {page_matches} recent, {freshness}")
                consecutive_empty = 0

            page_num += 1
            links = await _get_icims_next_page(search_page)
        else:
            _log("  No more pages")

        return results, skipped, newest_seen
    finally:
        await search_page.close()
        await detail_page.close()


# ---------------------------------------------------------------------------
# Emory Healthcare (DirectEmployers / Jobsyn)
# ---------------------------------------------------------------------------

async def _intercept_emory_api(page) -> dict:
    """Await the next prod-search-api.jobsyn.org response on this page."""
    future: asyncio.Future = asyncio.get_event_loop().create_future()

    async def on_response(response):
        if "prod-search-api.jobsyn.org" in response.url and not future.done():
            try:
                body = await response.json()
                future.set_result(body)
            except Exception as exc:
                future.set_exception(exc)

    page.on("response", on_response)
    try:
        return await asyncio.wait_for(future, timeout=20.0)
    except asyncio.TimeoutError:
        return {}
    finally:
        page.remove_listener("response", on_response)


async def scrape_emory_site(browser, site: dict, since_date: date) -> tuple[list[dict], int, date | None]:
    _log(f"Scraping {site['name']} (Jobsyn/DirectEmployers) ...")
    page = await browser.new_page()
    try:
        loc_kw = site.get("location_keywords", set())
        max_pages = site.get("max_pages")
        results: list[dict] = []
        skipped = 0
        consecutive_old = 0
        newest_seen: date | None = None
        page_num = 1

        # Page 1: capture the SPA's initial API call during page load
        _log(f"  Loading {site['page_url']}")
        intercept_task = asyncio.ensure_future(_intercept_emory_api(page))
        await page.goto(site["page_url"], wait_until="networkidle", timeout=90_000)
        await page.wait_for_timeout(2_000)
        api_data = await intercept_task

        while api_data:
            jobs_raw = api_data.get("jobs") or []
            pagination = api_data.get("pagination") or {}
            has_more = pagination.get("has_more_pages", False)
            _log(f"  {len(jobs_raw)} job(s) on page {page_num}")

            if not jobs_raw:
                break

            page_had_fresh = False
            for job in jobs_raw:
                raw_title = (job.get("title_exact") or "").strip()
                raw_date  = (job.get("date_new") or "")[:10]
                title_slug = (job.get("title_slug") or "").strip()
                guid       = (job.get("guid") or "").strip()
                loc_exact  = (job.get("location_exact") or "").strip()
                city       = (job.get("city_exact") or "").strip()
                state      = (job.get("state_short") or "").strip()

                if not raw_title or not raw_date or not guid:
                    skipped += 1
                    continue

                if _is_excluded_title(raw_title):
                    skipped += 1
                    continue

                try:
                    dp = date.fromisoformat(raw_date)
                except ValueError:
                    skipped += 1
                    continue

                if newest_seen is None or dp > newest_seen:
                    newest_seen = dp

                if dp < since_date:
                    continue

                page_had_fresh = True

                if dp != since_date:
                    continue

                job_url = f"https://emory.jobs/jobs/{title_slug}/{guid}/"
                location = loc_exact or (f"{city}, {state}" if city and state else city or state)
                loc_str = location.lower()

                if loc_kw and not any(k in loc_str for k in loc_kw):
                    _log(f"  SKIP  {dp} ({location}) — {raw_title[:60]}")
                    continue

                results.append({
                    "title": raw_title,
                    "url": job_url,
                    "date_posted": dp,
                    "location": location,
                    "occupational_category": "",
                    "work_hours": "",
                    "employment_type": "",
                })
                _log(f"  MATCH {dp}   — {raw_title[:60]}")

            freshness = _page_freshness(newest_seen)
            if not page_had_fresh:
                consecutive_old += 1
                _log(
                    f"  Page {page_num}: all old ({consecutive_old}/3), {freshness}"
                    f" — {'stopping' if consecutive_old >= 3 else 'checking next'}"
                )
                if consecutive_old >= 3:
                    break
            else:
                consecutive_old = 0
                _log(f"  Page {page_num}: fresh jobs, {freshness}")

            if not has_more:
                _log("  No more pages (API)")
                break

            page_num += 1
            if max_pages and page_num > max_pages:
                _log(f"  Page limit ({max_pages}) reached — stopping")
                break

            # Click the full-width "More" load-more button and intercept the API response
            more_btn = page.locator("button:not(.text-sm)").filter(has_text="More")
            if not await more_btn.count():
                _log("  More button not found in DOM — stopping")
                break
            intercept_task = asyncio.ensure_future(_intercept_emory_api(page))
            await more_btn.first.click()
            api_data = await intercept_task
            if not api_data:
                _log(f"  WARN — no API response after More click on page {page_num}")
                break

        return results, skipped, newest_seen
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# Title exclusion filter
# ---------------------------------------------------------------------------

def _is_excluded_title(title: str) -> bool:
    t = title.lower()
    if any(phrase in t for phrase in TITLE_EXCLUDE_PHRASES):
        return True
    return any(re.search(rf'\b{re.escape(word)}\b', t) for word in TITLE_EXCLUDE_WORDS)


# ---------------------------------------------------------------------------
# Email builders
# ---------------------------------------------------------------------------

def _sort_collapsed(results: list[dict], newest_seen: date | None, since_date: date) -> bool:
    """True when scraper hit its threshold with 0 matches and newest date seen is
    suspiciously stale — strong signal that the server returned a broken sort order."""
    if results or newest_seen is None:
        return False
    return newest_seen < since_date - timedelta(days=2)


def _build_site_section(site_name: str, jobs: list[dict], skipped: int, sort_warning: bool = False, newest_seen: date | None = None) -> str:
    shown_jobs = [j for j in jobs if not _is_excluded_title(j["title"])]
    filtered = len(jobs) - len(shown_jobs)
    count = len(shown_jobs)
    job_items_html = []
    for job in shown_jobs:
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
    skip_note = (
        f'<p style="color:#bbb;font-size:12px;margin-top:4px;">{skipped} skipped (no structured data)</p>'
        if skipped else ""
    )
    filter_note = (
        f'<p style="color:#bbb;font-size:12px;margin-top:2px;">{filtered} filtered (clinical/non-informatics title)</p>'
        if filtered else ""
    )
    newest_str = newest_seen.strftime("%b %d") if newest_seen else "unknown"
    warning_note = (
        f'<p style="color:#c0392b;font-size:13px;margin-top:6px;">'
        f'&#9888; Sort may have failed — newest posting seen was from {newest_str}. Manual check recommended.</p>'
        if sort_warning else ""
    )
    return (
        f'<h3 style="color:#1a4a7a;margin-bottom:4px;">{site_name}</h3>'
        f'<p style="margin-top:0;font-size:13px;color:#555;">{count} new posting{"s" if count != 1 else ""}</p>'
        f'{warning_note}{body}{skip_note}{filter_note}'
    )


def build_html_email(results: list[tuple[str, list[dict], int, bool, date | None]], today: date) -> str:
    total = sum(1 for _, jobs, _, _, _ in results for j in jobs if not _is_excluded_title(j["title"]))
    date_str = today.strftime('%b %d, %Y')
    sections = '<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">'.join(
        _build_site_section(site_name, jobs, skipped, sort_warning, newest_seen)
        for site_name, jobs, skipped, sort_warning, newest_seen in results
        if jobs or skipped or sort_warning
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


def send_email(results: list[tuple[str, list[dict], int, bool, date | None]], today: date, label: str = "") -> None:
    total = sum(1 for _, jobs, _, _, _ in results for j in jobs if not _is_excluded_title(j["title"]))
    tag = f" [{label}]" if label else ""
    subject = f"[Job Alert{tag}] {total} new posting{'s' if total != 1 else ''} — {today.strftime('%Y-%m-%d')}"
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


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def _run_site(
    sem: asyncio.Semaphore,
    scraper_fn,
    browser,
    site: dict,
    since_date: date,
) -> tuple:
    async with sem:
        try:
            jobs, skipped, newest_seen = await scraper_fn(browser, site, since_date)
            if _sort_collapsed(jobs, newest_seen, since_date):
                _log(f"  {site['name']}: sort collapse detected (newest={newest_seen}) — retrying")
                await asyncio.sleep(30)
                jobs, skipped, newest_seen = await scraper_fn(browser, site, since_date)
            sort_warning = _sort_collapsed(jobs, newest_seen, since_date)
            if sort_warning:
                _log(f"  {site['name']}: sort still collapsed after retry — flagging in email")
            _log(f"{site['name']}: {len(jobs)} qualifying job(s), {skipped} skipped — {_page_freshness(newest_seen)}")
            return (site["name"], jobs, skipped, sort_warning, newest_seen, site.get("remote_only", False))
        except Exception as e:
            _log(f"  {site['name']}: ERROR — {e}")
            return (site["name"], [], 0, False, None, site.get("remote_only", False))


async def main() -> None:
    _validate_env()
    _log(f"Run started (since={TODAY})")

    try:
        async with async_playwright() as pw:
            _log("Launching browser ...")
            browser = await pw.chromium.launch(headless=True)
            _log("Browser ready")
            try:
                sem = asyncio.Semaphore(3)
                # Heaviest sites first (LPT heuristic): filtered/remote Workday sites
                # iterate many pages; Phenom sites are faster; Bon Secours (non-filtered
                # Workday) stops early when dates go stale so goes last.
                ordered = (
                    [(scrape_workday_site, s) for s in WORKDAY_SITES if s.get("remote_only") or s.get("location_keywords")]
                    + [(scrape_emory_site, s) for s in EMORY_SITES]
                    + [(scrape_site, s) for s in SITES]
                    + [(scrape_icims_site, s) for s in ICIMS_SITES]
                    + [(scrape_workday_site, s) for s in WORKDAY_SITES if not s.get("remote_only") and not s.get("location_keywords")]
                )
                tasks = [_run_site(sem, fn, browser, site, TODAY) for fn, site in ordered]
                results = list(await asyncio.gather(*tasks))

                local_results  = [(n, j, sk, sw, ns) for n, j, sk, sw, ns, ro in results if not ro]
                remote_results = [(n, j, sk, sw, ns) for n, j, sk, sw, ns, ro in results if ro]

                if any(j or sk or sw for _, j, sk, sw, _ in local_results):
                    send_email(local_results, TODAY, label="Richmond/Regional")
                if any(j or sk or sw for _, j, sk, sw, _ in remote_results):
                    send_email(remote_results, TODAY, label="Remote")
            finally:
                await browser.close()
    except Exception as e:
        _log(f"ERROR: {e}")
        raise

    _log("Run complete")


if __name__ == "__main__":
    asyncio.run(main())
