import argparse
import asyncio
import json
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Force UTF-8 console output — Windows cp1252 crashes on Unicode job titles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "scraper.log"
SEEN_JOBS_FILE = BASE_DIR / "seen_jobs.json"
LOG_MAX_DAYS = 14
SEEN_MAX_AGE_DAYS = 45
load_dotenv(BASE_DIR / ".env")

EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
EMAIL_APP_PASS = os.environ.get("EMAIL_APP_PASS", "")

# Phenom People ATS — ordered by expected elapsed (UVA first: serial detail fetches, up to 20 pages)
SITES = [
    {
        "name": "UVA Health",
        "url": (
            "https://careers.uvahealth.org/us/en/search-results"
            "?sortBy=postingdate&descending=true"
        ),
        "categories": [
            "Finance, Business & Human Resources",
            "Information Management, Services & Technology",
            "Research",
            "Safety, Compliance, Regulatory, & Privacy",
            "Managerial & Supervisory",
        ],
        "max_pages": 20,
        "email_bucket": "main",
    },
    {
        "name": "VCU Health",
        "url": (
            "https://careers.vcuhealth.org/us/en/search-results"
            "?sortBy=postingdate&descending=true"
        ),
        "categories": [
            "Revenue Cycle",
            "Information Technology",
            "Health System Management/Operations",
            "Quality & Safety",
            "Administrative Support",
            "Finance",
        ],
        "email_bucket": "main",
    },
    {
        "name": "Duke Health",
        "url": (
            "https://careers.dukehealth.org/us/en/search-results"
            "?sortBy=postingdate&descending=true"
        ),
        "categories": [
            "Corporate",
            "Information Technology",
            "Revenue Management",
            "Administrative and Support Services",
        ],
        "max_pages": 12,
        "email_bucket": "main",
    },
]

WORKDAY_SITES = [
    # Detail-page fetch sites first (postedOn absent in CXS) — slowest, claim slots early
    {"name": "MUSC",
     "url": (
         "https://musc.wd1.myworkdayjobs.com/en-US/MUSC/jobs"
         "?jobFamily=b6f39ab6e17a1010bc655fcf712b0002"
         "&jobFamily=b6f39ab6e17a1010bc655c33335c0001"
         "&jobFamily=b6f39ab6e17a1010bc6550c483c10000"
         "&jobFamily=b6f39ab6e17a1010bc65666e99430001"
         "&jobFamily=b6f39ab6e17a1010bc6556c88b900000"
         "&jobFamily=b6f39ab6e17a1010bc655896ac3c0001"
         "&jobFamily=63ee0f7615fd10010766102ddb7a0000"
         "&jobFamily=b6f39ab6e17a1010bc655f3564f60000"
     ),
     "max_pages": 6, "email_bucket": "main"},
    {"name": "VUMC",
     "url": (
         "https://vumc.wd1.myworkdayjobs.com/vumccareers"
         "?jobFamilyGroup=aa4bc8a45bec1001f06b6f977bfb0000"
         "&jobFamilyGroup=aa4bc8a45bec1001f06b6a2b58be0000"
         "&jobFamilyGroup=aa4bc8a45bec1001f06b6e6159d20000"
         "&jobFamilyGroup=aa4bc8a45bec1001f06b685af99d0000"
         "&jobFamilyGroup=aa4bc8a45bec1001f06b638780e10000"
     ),
     "max_pages": 8, "email_bucket": "main"},
    # CXS date-exhaustion sites — fast, run after slow sites have claimed slots
    {"name": "Prisma Health",
     "url": (
         "https://prismahealth.wd5.myworkdayjobs.com/PrismaHealthCorporate"
         "?jobFamilyGroup=ee936705568e0156f8bf3bd6df038fc3"
         "&jobFamilyGroup=ee936705568e01f28d87e2b1ae03e174"
         "&jobFamilyGroup=ee936705568e013fff99a8b1ae03db74"
         "&jobFamilyGroup=ee936705568e01a412a842d6df0391c3"
     ),
     "max_pages": 12, "email_bucket": "main"},
    {"name": "Sentara",
     "url": (
         "https://sentara.wd1.myworkdayjobs.com/en-US/SCS"
         "?jobFamilyGroup=fb2c628a192710009e83d566e96d0000"
         "&jobFamilyGroup=3214b993574410009e7ab156508c0000"
         "&jobFamilyGroup=501d9eef9f7610009e808c09d90e0000"
         "&jobFamilyGroup=cf38025fbfe110009e80fb0da5ac0000"
     ),
     "max_pages": 6, "email_bucket": "main"},
    {"name": "Wellstar Health (Atlanta)",
     "url": (
         "https://wellstar.wd1.myworkdayjobs.com/wellstarcareers"
         "?jobFamilyGroup=36c48bb8fdf710267875d93c99eb0000"
         "&jobFamilyGroup=36c48bb8fdf710267875de0dc37f0000"
         "&jobFamilyGroup=36c48bb8fdf710267875dc3f210e0000"
         "&jobFamilyGroup=36c48bb8fdf710267875d63a6e820000"
         "&jobFamilyGroup=36c48bb8fdf710267875d50610d30000"
         "&jobFamilyGroup=36c48bb8fdf7102678759eded26a0001"
         "&jobFamilyGroup=36c48bb8fdf710267875dea8593e0000"
     ),
     "max_pages": 8, "email_bucket": "main"},
    {"name": "Atrium Health (Charlotte)",
     "url": (
         "https://aah.wd5.myworkdayjobs.com/External"
         "?jobFamilyGroup=638364634b3b1001bd1e1c9052760000"
         "&jobFamilyGroup=1c18ea8cf0e80110c66748713bf70000"
         "&jobFamilyGroup=1c18ea8cf0e80110c66470a390fe0000"
         "&jobFamilyGroup=1c18ea8cf0e80110c667016009800000"
         "&jobFamilyGroup=1c18ea8cf0e80110c6631d7636600000"
         "&jobFamilyGroup=1c18ea8cf0e80110c6640728c9720000"
         "&jobFamilyGroup=1c18ea8cf0e80110c66793b407520000"
         "&jobFamilyGroup=1c18ea8cf0e80110c6610267df590000"
     ),
     "max_pages": 8, "email_bucket": "main"},
    {"name": "Carilion Clinic",
     "url": (
         "https://carilionclinic.wd12.myworkdayjobs.com/en-US/External_Careers"
         "?jobFamilyGroup=01a109d50e5f10072caa9557e5510000"
         "&jobFamilyGroup=01a109d50e5f10072caa94bdafd50000"
         "&jobFamilyGroup=01a109d50e5f10072caa9a274e930000"
         "&jobFamilyGroup=01a109d50e5f10072caa9f9111e20000"
     ),
     "max_pages": 12, "email_bucket": "main"},
    {"name": "Bon Secours",               "url": "https://easyservice.wd5.myworkdayjobs.com/BonSecoursMercyHealthCareers", "email_bucket": "main"},
    {"name": "Shepherd Center",           "url": "https://shepherd.wd5.myworkdayjobs.com/ShepherdCenter",                                             "email_bucket": "main"},
    # Payer / vendor — commented out; activate when payer digest is ready
    # {"name": "Humana",          "url": "https://humana.wd5.myworkdayjobs.com/Humana_External_Career_Site",  "remote_only": True, "max_pages": 12, "email_bucket": "payer"},
    # {"name": "Elevance Health", "url": "https://elevancehealth.wd1.myworkdayjobs.com/ANT",                  "remote_only": True, "max_pages": 12, "email_bucket": "payer"},
    # {"name": "Cigna",           "url": "https://cigna.wd5.myworkdayjobs.com/cignacareers",                  "remote_only": True, "max_pages": 12, "email_bucket": "payer"},
    # {"name": "Solventum (3M HIS)", "url": "https://healthcare.wd1.myworkdayjobs.com/Search",               "remote_only": True, "max_pages": 12, "email_bucket": "payer"},
    # {"name": "Veradigm",        "url": "https://veradigm.wd12.myworkdayjobs.com/VR",                       "remote_only": True, "max_pages": 6,  "email_bucket": "payer"},
    # {"name": "Waystar (Atlanta / Louisville)", "url": "https://waystar.wd1.myworkdayjobs.com/Waystar",
    #  "location_keywords": {"atlanta", "louisville"}, "max_pages": 6, "email_bucket": "payer"},
    # {"name": "Waystar (Remote)", "url": "https://waystar.wd1.myworkdayjobs.com/Waystar",                   "remote_only": True, "max_pages": 6,  "email_bucket": "payer"},
]

# Title exclusion filter — applied at email-build time, not scrape time
TITLE_EXCLUDE_PHRASES = {
    "registered nurse", "licensed practical", "licensed vocational",
    "nurse practitioner", "certified nursing assistant", "certified nurse anesthetist",
    "clinical nurse specialist", "nurse midwife", "travel nurse",
    "charge nurse", "staff nurse", "nursing supervisor", "nurse manager",
    "patient care tech", "patient care assistant", "nursing",
    "nurse residency", "nurse case manager", "nurse supervisor",
    "nurse mgr", "mgr, nurse", "lic practical",
    "physical therapist", "physical therapy assistant",
    "occupational therapist", "occupational therapy assistant",
    "speech", "speech language", "speech-language",
    "social work", "social worker",
    "audiologist", "audiology", "physiologist",
    "respiratory therapist", "respiratory therapy", "respiratory care practitioner",
    "advanced practice",
    "athletic trainer", "rehab aide", "rehabilitation aide",
    "ambulance attendant", "polysomnography",
    "radiologic technolog", "radiology tech", "rad tech", "x-ray tech",
    "ultrasound tech", "sonographer", "mri tech", "ct tech",
    "nuclear medicine", "mammograph", "pathologist",
    "lab technician", "laboratory technician", "medical laboratory", "med asst",
    "phlebotomist", "phlebotomy", "histotechnologist", "cytotechnologist",
    "neurodiagnostic", "specimen processing",
    "ekg tech", "eeg tech", "cardiac tech", "echo tech", "echocardiography", "dialysis tech",
    "surgical tech", "scrub tech", "sterile processing", "central sterile",
    "ophthalm", "optometr",
    "dental assistant", "dental hygienist",
    "pharmacist", "pharmacy technician", "pharmacy tech", "pharmacy specialist", "pharmacy intern",
    "medical assistant", "medical scribe",
    "patient transporter", "patient transport",
    "care partner", "patient support assistant",
    "emergency department tech", "ed tech",
    "primary therapist",
    "housekeeper", "housekeeping", "environmental services",
    "food service", "food and nutrition", "dietary aide", "dietary tech", "dietary assistant",
    "dietitian", "kitchen",
    "security officer", "security guard", "public safety officer",
    "chaplain", "pastoral care", "spiritual health",
    "maintenance technician", "facilities technician",
    "valet", "groundskeeper", "supply chain",
    "patient scheduler", "appointment scheduler", "clinic scheduler",
    "front desk", "receptionist", "welcome", "parking assistant",
    "infection preventionist",
    "coding specialist", "coding denials",
    "unit clerk", "unit secretary", "ward clerk",
    "safety attendant",
    "precertification",
    "floor care",
    "billing follow up",
    "acct resolution",
    "payment variance",
    "postdoc", "post doc", "post-doc",  # postdoctoral / post doctoral / post-doctoral
    "food &",
    "dining",
    "medical lab",
    "dietician",                         # alternate spelling of dietitian
    "child life specialist",
    "technologist",
}
TITLE_EXCLUDE_WORDS = {
    "rn", "lpn", "lvn", "cna", "crna", "cns", "emt", "paramedic",
    "np", "acnp", "scribe", "app", "sales",
    "physician", "psychologist",
    "coder",
    "painter", "elevator",
    "schegistrar",
    "lcsw", "lpc",
    "cook",
    "president",   # also matches vice president
}
PAYER_EXCLUDE_WORDS = {"lead", "senior", "manager", "director", "principal"}

# ---------------------------------------------------------------------------
# Priority scoring config — tune patterns and org tiers here
# ---------------------------------------------------------------------------
# Role families: health informatics · clinical data analytics · clinical workflow/
# process improvement · clinical application support/design · research data mgmt ·
# patient quality & safety · clinical documentation integrity ·
# population/community health analytics · health BI/BA
PRIORITY_CONFIG: dict = {
    "strong_title_patterns": [
        # Health informatics
        r"informatics",
        # Clinical data analytics
        r"clinical data anal",      # "clinical data analyst/analytics", not "coordinator"
        r"clinical analytics",
        # Clinical application support / design
        r"\bepic\b",                # Epic EHR — almost always an application analyst role
        r"ehr (analyst|specialist|consultant|build)",
        r"emr (analyst|specialist|consultant|build)",
        r"application (analyst|specialist|build|consultant)",
        # Research data management
        r"research data",
        r"data governance",
        # Patient quality & safety
        r"patient safety (analyst|specialist|data)",
        r"quality (improvement analyst|data analyst)",
        # Clinical documentation integrity
        r"documentation integrity",
        r"\bcdi\b",
        # Population / community health analytics
        r"population health",
        r"community health (analyst|data|informatics)",
        r"public health (analyst|data|informatics)",
        # Health BI / analytics
        r"\banalytics\b",
        r"business intelligence",
        r"\bbi (analyst|developer|specialist)\b",
        r"reporting analyst",
        r"decision support",
        r"data engineer",
        r"data scientist",
        r"data architect",
        # Clinical workflow / process improvement
        r"clinical workflow",
        r"workflow (analyst|specialist|consultant)",
        r"process improvement (analyst|specialist|consultant)",
        r"performance improvement (analyst|specialist)",
        r"clinical data",
    ],
    "supporting_title_patterns": [
        r"data analyst",
        r"data specialist",
        r"data manager",
        r"systems analyst",
        r"outcomes analyst",
        r"outcomes research",
        r"health(care)? analyst",
        r"business analyst",
        r"quality analyst",
        r"quality improvement\b",
    ],
    "downgrade_patterns": [
        r"clinical documentation specialist",   # medical records/HIM, not CDI
        r"charge entry",
        r"medical record",
        r"transcription",
        r"\bdirector\b",
    ],
    # All get +1 score boost — must match "name" field in site config exactly
    "preferred_orgs": [
        "UVA Health",
        "VCU Health",
        "Bon Secours",
        "VUMC",
        "Duke Health",
        "Emory Healthcare",
        "Shepherd Center",
    ],
    # Subset for teal color accent — no scoring difference from other preferred orgs
    "richmond_orgs": [
        "UVA Health",
        "VCU Health",
        "Bon Secours",
    ],
    "score_thresholds": {"primary": 3},
}

# iCIMS ATS
ICIMS_SITES: list[dict] = [
    {
        "name": "Ascension",
        "urls": [
            "https://ascensionjobs1-ascension.icims.com/jobs/search?ss=1&searchRelation=keyword_all&searchCategory=27586",
            "https://ascensionjobs1-ascension.icims.com/jobs/search?ss=1&searchRelation=keyword_all&searchCategory=27616",
            "https://ascensionjobs1-ascension.icims.com/jobs/search?ss=1&searchRelation=keyword_all&searchCategory=27596",
        ],
        "max_pages": 6,
        "email_bucket": "main",
    },
]

# Emory Healthcare — DirectEmployers/Jobsyn SPA (emory.jobs)
# Scraper uses response interception; direct API fetch always returns 403.
EMORY_SITES = [
    {
        "name": "Emory Healthcare",
        "page_url": "https://emory.jobs/jobs/",
        "max_pages": 20,
        "email_bucket": "main",
    },
]

TODAY = date.today() - timedelta(days=1)


def _load_seen_jobs() -> set[str]:
    if SEEN_JOBS_FILE.exists():
        data = json.loads(SEEN_JOBS_FILE.read_text(encoding="utf-8"))
        cutoff = (date.today() - timedelta(days=SEEN_MAX_AGE_DAYS)).isoformat()
        return {
            url for url, v in data.items()
            if (v if isinstance(v, str) else v["first_seen"]) >= cutoff
        }
    return set()


def _save_seen_jobs(seen: dict) -> None:
    cutoff = (date.today() - timedelta(days=SEEN_MAX_AGE_DAYS)).isoformat()
    pruned = {
        url: v for url, v in seen.items()
        if (v if isinstance(v, str) else v["first_seen"]) >= cutoff
    }
    SEEN_JOBS_FILE.write_text(json.dumps(pruned, indent=2), encoding="utf-8")


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


def _parse_posted_on(s: str) -> date | None:
    """Convert Workday CXS 'postedOn' string to a date. Returns None if unparseable."""
    t = s.lower().strip()
    if not t:
        return None
    if "today" in t:
        return date.today()
    if "yesterday" in t:
        return date.today() - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return date.today() - timedelta(days=int(m.group(1)))
    _log(f"  WARN — unrecognized postedOn format: {s!r}")
    return None


def _rotate_log() -> None:
    if not LOG_FILE.exists():
        return
    cutoff = (date.today() - timedelta(days=LOG_MAX_DAYS)).strftime("%Y-%m-%d")
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    kept = [l for l in lines if not l.startswith("[") or l[1:11] >= cutoff]
    LOG_FILE.write_text("".join(kept), encoding="utf-8")


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

async def _make_detail_page(browser):
    """Create a browser tab for detail-page visits, blocking non-essential resources."""
    page = await browser.new_page()
    await page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in ("image", "stylesheet", "font", "media")
        else route.continue_(),
    )
    return page


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

async def _get_job_links(page, url: str, categories: list[str] | None = None) -> list[dict]:
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
        elif current_sort == '':
            _log("  Sort dropdown empty — sort likely URL-controlled, skipping")

    # Apply category facet filters — Phenom stores these in a POST body, not the URL.
    # Clicks are additive (OR logic): each selected category adds to selected_fields.category[].
    if categories:
        # Some deployments (e.g. UVA) hide the facet panel behind a toggle — try to open it.
        first_item = page.locator('[data-ph-at-id="facet-results-item"]').first
        try:
            panel_visible = await first_item.is_visible(timeout=2_000)
        except Exception:
            panel_visible = False
        if not panel_visible:
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
                        break
                except Exception:
                    pass

        clicked = 0
        for cat in categories:
            try:
                loc = page.locator('[data-ph-at-id="facet-results-item"]').filter(has_text=cat).first
                await loc.scroll_into_view_if_needed(timeout=3_000)
                await loc.click(timeout=5_000)
                await page.wait_for_timeout(300)
                _log(f"  Category filter: {cat}")
                clicked += 1
            except Exception as e:
                _log(f"  WARN — category filter failed for {cat!r}: {e}")
        if clicked:
            await page.wait_for_timeout(2_500)  # let filtered results settle

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
    detail_page = await _make_detail_page(browser)
    try:
        links = await _get_job_links(search_page, site["url"], site.get("categories"))
        page_num = 1
        results = []
        skipped_excl = 0
        skipped_err = 0
        consecutive_empty = 0
        newest_seen: date | None = None

        while links:
            page_matches = 0
            for i, job in enumerate(links, 1):
                details = await _get_job_details(detail_page, job["url"])

                if details is None:
                    _log(f"  [p{page_num}/{i}] No JSON-LD — {job['title'][:60]}")
                    skipped_err += 1
                    continue

                dp = details["date_posted"]
                if newest_seen is None or dp > newest_seen:
                    newest_seen = dp
                if dp >= since_date:
                    loc = (details.get("location") or "").lower()
                    loc_kw = site.get("location_keywords", set())
                    if loc_kw:
                        qualifies = any(k in loc for k in loc_kw)
                    else:
                        qualifies = True
                    if qualifies:
                        results.append({**job, **details})
                if dp >= since_date:
                    page_matches += 1

            freshness = _page_freshness(newest_seen)
            if page_matches == 0:
                consecutive_empty += 1
                if consecutive_empty >= 5:
                    _log(f"  {site['name']}: consecutive stop — page {page_num}, {freshness}")
                    break
                _log(f"  Page {page_num}: 0 recent ({consecutive_empty}/5), {freshness} — next")
            else:
                _log(f"  Page {page_num}: {page_matches} recent, {freshness}")
                consecutive_empty = 0

            page_num += 1
            max_pages = site.get("max_pages")
            if max_pages and page_num > max_pages:
                _log(f"  {site['name']}: page limit ({max_pages}) reached — stopping at page {page_num}")
                break
            links = await _get_next_page_links(search_page)
        else:
            _log("  No more pages")

        return results, skipped_excl, skipped_err, newest_seen
    finally:
        await search_page.close()
        await detail_page.close()


# ---------------------------------------------------------------------------
# Workday ATS
# ---------------------------------------------------------------------------

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


_WORKDAY_DOM_LINKS_JS = """() => {
    const seen = new Set();
    const results = [];
    document.querySelectorAll('a[data-automation-id="jobTitle"]').forEach(a => {
        if (seen.has(a.href)) return;
        seen.add(a.href);
        const title = (a.innerText || a.textContent || '').trim();
        if (title.length > 3) results.push({ title, url: a.href });
    });
    return results;
}"""


async def scrape_workday_site(browser, site: dict, since_date: date) -> tuple[list[dict], int, date | None]:
    _log(f"Scraping {site['name']} (Workday) ...")
    search_page = await browser.new_page()
    detail_page = await _make_detail_page(browser)

    loc_kw = site.get("location_keywords", set())
    base_url = re.sub(r"/jobs$", "", site["url"].split("?")[0].rstrip("/"))  # externalPath is relative to tenant root, e.g. /job/...
    _cxs_buf: list[dict] = []

    async def _on_cxs(response):
        if "/wday/cxs/" not in response.url or response.status != 200:
            return
        if "json" not in response.headers.get("content-type", ""):
            return
        try:
            data = await response.json()
            if not isinstance(data, dict):
                return
            postings = data.get("jobPostings", [])
            if postings:
                _cxs_buf.extend(postings)
        except Exception:
            pass

    search_page.on("response", _on_cxs)

    try:
        _log(f"  Loading {site['url']}")
        await search_page.goto(site["url"], wait_until="networkidle", timeout=90_000)
        await search_page.wait_for_timeout(500)

        use_cxs = bool(_cxs_buf)
        if use_cxs:
            _log(f"  CXS intercept active ({len(_cxs_buf)} postings on page 1)")
        else:
            _log(f"  CXS not intercepted — using DOM+JSON-LD fallback")
            try:
                await search_page.wait_for_function(
                    "() => document.querySelectorAll('a[data-automation-id=\"jobTitle\"]').length >= 5",
                    timeout=30_000,
                )
            except PlaywrightTimeoutError:
                _log(f"  {site['name']}: WARN — job titles timed out")
                return [], 0, None

        results = []
        skipped_excl = 0
        skipped_err = 0
        consecutive_empty = 0
        newest_seen: date | None = None
        page_num = 1
        # DOM fallback only: page 1 is already loaded, subsequent pages come from _get_workday_next_page
        dom_pending: list[dict] | None = None

        while True:
            # ── Collect this page's job list ─────────────────────────────────
            if use_cxs:
                raw_page = list(_cxs_buf)
                _cxs_buf.clear()
            else:
                raw_page = (
                    await search_page.evaluate(_WORKDAY_DOM_LINKS_JS)
                    if page_num == 1
                    else (dom_pending or [])
                )

            if not raw_page:
                _log("  No more pages")
                break

            page_matches = 0
            page_dates: list[date] = []

            for i, job in enumerate(raw_page, 1):
                if use_cxs:
                    title = re.sub(r" {2,}", " ", (job.get("title") or "").strip())
                    ext_path = job.get("externalPath", "")
                    if not title or not ext_path:
                        skipped_err += 1
                        continue
                    url = base_url + ext_path
                    cxs_loc = (job.get("locationsText") or "").lower()
                    cxs_dp = _parse_posted_on(job.get("postedOn", ""))

                    # If CXS omitted postedOn (e.g. MUSC tenant), fetch the detail page for
                    # the real date rather than defaulting every job to since_date.
                    if cxs_dp is None:
                        details = await _get_job_details(detail_page, url)
                        if details is None:
                            _log(f"  [p{page_num}/{i}] No JSON-LD — {title[:60]}")
                            skipped_err += 1
                            continue
                        dp = details["date_posted"]
                        loc = (details.get("location") or "").lower()
                    else:
                        dp = cxs_dp
                        loc = cxs_loc

                    if loc_kw and not any(k in loc for k in loc_kw):
                        page_dates.append(dp)
                        if dp >= since_date:
                            page_matches += 1
                        continue

                    page_dates.append(dp)
                    if newest_seen is None or dp > newest_seen:
                        newest_seen = dp
                    if dp >= since_date:
                        entry = {"title": title, "url": url, "date_posted": dp,
                                 "location": job.get("locationsText", ""),
                                 "employment_type": "", "work_hours": "", "occupational_category": ""}
                        if cxs_dp is None and details:
                            entry.update({k: details[k] for k in ("employment_type", "work_hours", "occupational_category") if k in details})
                        results.append(entry)
                    if dp >= since_date:
                        page_matches += 1

                else:
                    # DOM+JSON-LD fallback — identical to original
                    details = await _get_job_details(detail_page, job["url"])
                    if details is None:
                        _log(f"  [p{page_num}/{i}] No JSON-LD — {job['title'][:60]}")
                        skipped_err += 1
                        continue
                    dp = details["date_posted"]
                    page_dates.append(dp)
                    if newest_seen is None or dp > newest_seen:
                        newest_seen = dp
                    if dp >= since_date:
                        loc = (details.get("location") or "").lower()
                        if loc_kw:
                            qualifies = any(k in loc for k in loc_kw)
                        else:
                            qualifies = True
                        if qualifies:
                            results.append({**job, **details})
                    if dp >= since_date:
                        page_matches += 1

            freshness = _page_freshness(newest_seen)

            if page_dates and len(set(page_dates)) == 1 and page_dates[0] >= date.today():
                _log(f"  WARN — batch refresh suspected ({site['name']}): all dates = {page_dates[0]} — stopping")
                break

            if page_matches == 0:
                consecutive_empty += 1
                if consecutive_empty >= 5:
                    _log(f"  {site['name']}: consecutive stop — page {page_num}, {freshness}")
                    break
                _log(f"  Page {page_num}: 0 recent ({consecutive_empty}/5), {freshness} — next")
            else:
                _log(f"  Page {page_num}: {page_matches} recent, {freshness}")
                consecutive_empty = 0

            page_num += 1
            if site.get("max_pages") and page_num > site["max_pages"]:
                _log(f"  {site['name']}: page limit ({site['max_pages']}) reached — stopping at page {page_num}")
                break

            # ── Advance to next page ──────────────────────────────────────────
            # _get_workday_next_page clicks, waits for DOM refresh, and returns links.
            # For CXS: the wait also guarantees the CXS response has arrived.
            next_links = await _get_workday_next_page(search_page)
            if not next_links:
                _log("  No more pages")
                break
            if use_cxs and not _cxs_buf:
                # Next-page DOM changed but CXS didn't fire — fall back for remaining pages
                _log(f"  WARN — CXS silent on page {page_num}, switching to DOM+JSON-LD")
                use_cxs = False
                dom_pending = next_links
            elif not use_cxs:
                dom_pending = next_links

        max_results = site.get("max_results")
        if max_results and len(results) > max_results:
            _log(f"  WARN — {site['name']}: capping results {len(results)} → {max_results} (batch refresh likely)")
            results = results[:max_results]

        return results, skipped_excl, skipped_err, newest_seen
    finally:
        await search_page.close()
        await detail_page.close()


# ---------------------------------------------------------------------------
# iCIMS ATS
# ---------------------------------------------------------------------------

_ICIMS_JOB_LINK_JS = r"""() => {
    const seen = new Set();
    const results = [];

    // Newer iCIMS portals (e.g. Ascension) use iCIMS_JobCardItem with date + location in listing
    document.querySelectorAll('.iCIMS_JobCardItem').forEach(card => {
        const link = card.querySelector('a[href*="/jobs/"]');
        if (!link || !/\/jobs\/\d+/.test(link.href)) return;
        if (seen.has(link.href)) return;
        seen.add(link.href);
        const title = (link.innerText || link.textContent || '').trim()
            .replace(/^Job Posting Title\s*/i, '').trim();
        if (title.length <= 3) return;

        const cardText = card.innerText || '';
        // Date: "(M/D/YYYY H:MM AM/PM)" — extract M/D/YYYY part
        const dm = cardText.match(/\((\d{1,2}\/\d{1,2}\/\d{4})\s/);
        const dateStr = dm ? dm[1] : null;

        // Location: "Job Locations\nUS-STATE-City[optional more]"
        let location = '';
        const lm = cardText.match(/Job Locations\s+([\s\S]+?)(?:\n\nPosted|\nPosted|$)/);
        if (lm) {
            const firstLoc = lm[1].trim().split('\n')[0].trim();  // first location only
            const parts = firstLoc.split('-').slice(1);            // drop leading "US"
            if (parts.length >= 2) {
                const state = parts[0];
                const city = parts.slice(1).join(' ');
                location = city + ', ' + state;
            } else {
                location = firstLoc;
            }
        }
        results.push({ title, url: link.href, dateStr, location });
    });
    if (results.length > 0) return results;

    // Older iCIMS portals — plain link extraction, date comes from detail page JSON-LD
    document.querySelectorAll('a[href*="/jobs/"]').forEach(a => {
        if (!/\/jobs\/\d+/.test(a.href)) return;
        if (seen.has(a.href)) return;
        seen.add(a.href);
        const title = (a.innerText || a.textContent || '').trim()
            .replace(/^Job Posting Title\s*/i, '').trim();
        if (title.length > 3) results.push({ title, url: a.href, dateStr: null, location: '' });
    });
    return results;
}"""

# iCIMS_JobsTable = older portal version; iCIMS_ListingsPage = newer version (e.g. Ascension)
_ICIMS_TABLE_SEL = (
    'div.iCIMS_JobsTable, div[class*="iCIMS_Jobs"], '
    'div.iCIMS_ListingsPage, [id*="icims-jobs"]'
)


async def _icims_content_frame(page, site_hostname: str = ""):
    """Return the frame that holds iCIMS job listings — main frame or first child frame."""
    # Quick check: main frame already has the table (most iCIMS portals)
    try:
        await page.wait_for_selector(_ICIMS_TABLE_SEL, timeout=5_000)
        return page
    except PlaywrightTimeoutError:
        pass
    # Some portals (e.g. Ascension, Novant) render content inside a child iframe.
    # Wait for an iframe to appear in the DOM first, then probe its content.
    try:
        await page.wait_for_selector("iframe", timeout=20_000)
    except PlaywrightTimeoutError:
        return None
    for frame in page.frames[1:]:
        try:
            await frame.wait_for_selector(_ICIMS_TABLE_SEL, timeout=25_000)
            return frame
        except PlaywrightTimeoutError:
            continue
    # Fallback: find a child frame that shares the site's hostname (e.g. easyapply-org.icims.com).
    # Uses netloc comparison — NOT substring match — to avoid matching LivePerson frames that
    # embed the site hostname as a URL-encoded query parameter (?loc=https://easyapply-...).
    if site_hostname:
        await page.wait_for_timeout(3_000)
        for frame in page.frames[1:]:
            try:
                if urlparse(frame.url).netloc == site_hostname:
                    await frame.wait_for_load_state("domcontentloaded", timeout=15_000)
                    return frame
            except (PlaywrightTimeoutError, Exception):
                pass
    return None


async def _get_icims_job_links(page, site: dict) -> tuple[list[dict], object]:
    sep = "&" if "?" in site["url"] else "?"
    url = site["url"] + sep + "ss=1&sortby=date&in_jsch=1"
    _log(f"  Loading {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    site_hostname = urlparse(site["url"]).netloc
    frame = await _icims_content_frame(page, site_hostname)
    if frame is None:
        _log(
            f"  {site['name']}: WARN — iCIMS job table not found in any frame"
            " (selectors: .iCIMS_JobsTable, [class*=iCIMS_Jobs])"
        )
        return [], None
    _log(f"  Frame: {frame.url[:80]}")
    # Job cards render via XHR after the frame's domcontentloaded — wait for links.
    try:
        await frame.wait_for_selector('a[href*="/jobs/"]', timeout=20_000)
    except PlaywrightTimeoutError:
        _log(f"  {site['name']}: WARN — no job links appeared in frame after 20s")
    return await frame.evaluate(_ICIMS_JOB_LINK_JS), frame


async def _get_icims_next_page(frame) -> list[dict]:
    next_link = frame.locator('a[title="Next Page"], a[aria-label="Next"]').last
    if not await next_link.is_visible():
        next_link = frame.locator('a.glyph').filter(has_text=re.compile(r'next', re.IGNORECASE)).last
    if not await next_link.is_visible():
        return []
    first_href = await frame.evaluate(
        r"""() => ([...document.querySelectorAll('a[href*="/jobs/"]')]
            .find(a => /\/jobs\/\d+/.test(a.href)) || {}).href || ''"""
    )
    await next_link.click()
    try:
        await frame.wait_for_function(
            f"""() => {{
                const links = [...document.querySelectorAll('a[href*="/jobs/"]')]
                    .filter(a => /\\/jobs\\/\\d+/.test(a.href));
                return links.length > 0 && links[0].href !== {repr(first_href)};
            }}""",
            timeout=20_000,
        )
        await frame.wait_for_selector(_ICIMS_TABLE_SEL, timeout=15_000)
    except PlaywrightTimeoutError:
        _log("  WARN — iCIMS next page did not load new content")
        return []
    return await frame.evaluate(_ICIMS_JOB_LINK_JS)


async def scrape_icims_site(browser, site: dict, since_date: date) -> tuple[list[dict], int, date | None]:
    _log(f"Scraping {site['name']} (iCIMS) ...")
    search_page = await browser.new_page()
    detail_page = await _make_detail_page(browser)
    try:
        category_urls = site.get("urls") or [site["url"]]
        location_keywords = site.get("location_keywords")
        max_pages = site.get("max_pages")

        all_results: list[dict] = []
        total_skipped_excl = 0
        total_skipped_err = 0
        newest_seen: date | None = None

        for cat_url in category_urls:
            links, icims_frame = await _get_icims_job_links(search_page, {**site, "url": cat_url})
            if icims_frame is None:
                continue
            page_num = 1
            consecutive_empty = 0
            # True if the first page returned card-embedded dates (newer iCIMS like Ascension)
            has_card_dates = links and links[0].get("dateStr") is not None

            while links:
                page_matches = 0
                for i, job in enumerate(links, 1):
                    if _is_excluded_title(job["title"]):
                        total_skipped_excl += 1
                        continue

                    # Newer iCIMS portals embed date + location in the listing card
                    if has_card_dates:
                        raw_date_str = job.get("dateStr") or ""
                        location = job.get("location", "")
                        if not raw_date_str:
                            total_skipped_err += 1
                            continue
                        try:
                            dp = datetime.strptime(raw_date_str, "%m/%d/%Y").date()
                        except ValueError:
                            total_skipped_err += 1
                            continue
                        employment_type = ""
                        occupational_category = ""
                        work_hours = ""
                    else:
                        details = await _get_job_details(detail_page, job["url"])
                        if details is None:
                            _log(f"  [p{page_num}/{i}] No JSON-LD — {job['title'][:60]}")
                            total_skipped_err += 1
                            continue
                        dp = details["date_posted"]
                        location = details["location"]
                        employment_type = details.get("employment_type", "")
                        occupational_category = details.get("occupational_category", "")
                        work_hours = details.get("work_hours", "")

                    if newest_seen is None or dp > newest_seen:
                        newest_seen = dp
                    if dp >= since_date:
                        loc_lower = location.lower()
                        if location_keywords:
                            qualifies = any(kw in loc_lower for kw in location_keywords)
                        else:
                            qualifies = True
                        if qualifies:
                            all_results.append({
                                "title": job["title"], "url": job["url"],
                                "date_posted": dp, "location": location,
                                "employment_type": employment_type,
                                "occupational_category": occupational_category,
                                "work_hours": work_hours,
                            })
                    if dp >= since_date:
                        page_matches += 1

                freshness = _page_freshness(newest_seen)
                if page_matches == 0:
                    consecutive_empty += 1
                    if consecutive_empty >= 5:
                        _log(f"  {site['name']}: consecutive stop — page {page_num}, {freshness}")
                        break
                    _log(f"  Page {page_num}: 0 recent ({consecutive_empty}/5), {freshness} — next")
                else:
                    _log(f"  Page {page_num}: {page_matches} recent, {freshness}")
                    consecutive_empty = 0

                page_num += 1
                if max_pages and page_num > max_pages:
                    _log(f"  {site['name']}: page limit ({max_pages}) reached — stopping at page {page_num}")
                    break
                links = await _get_icims_next_page(icims_frame)
            else:
                _log("  No more pages")

        return all_results, total_skipped_excl, total_skipped_err, newest_seen
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


async def scrape_emory_site(browser, site: dict, since_date: date) -> tuple[list[dict], int, int, date | None]:
    _log(f"Scraping {site['name']} (Jobsyn/DirectEmployers) ...")
    page = await browser.new_page()
    try:
        loc_kw = site.get("location_keywords", set())
        max_pages = site.get("max_pages")
        results: list[dict] = []
        skipped_excl = 0
        skipped_err = 0
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
                    skipped_err += 1
                    continue

                if _is_excluded_title(raw_title):
                    skipped_excl += 1
                    continue

                try:
                    dp = date.fromisoformat(raw_date)
                except ValueError:
                    skipped_err += 1
                    continue

                if newest_seen is None or dp > newest_seen:
                    newest_seen = dp

                if dp < since_date:
                    continue

                page_had_fresh = True

                city_slug = f"{city.lower().replace(' ', '-')}-{state.lower()}" if city and state else "remote"
                job_url = f"https://emory.jobs/{city_slug}/{title_slug}/{guid}/job/"
                location = loc_exact or (f"{city}, {state}" if city and state else city or state)
                loc_str = location.lower()
                if loc_kw and not any(k in loc_str for k in loc_kw):
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

            freshness = _page_freshness(newest_seen)
            if not page_had_fresh:
                consecutive_old += 1
                if consecutive_old >= 3:
                    _log(f"  {site['name']}: consecutive stop — page {page_num}, {freshness}")
                    break
                _log(f"  Page {page_num}: all old ({consecutive_old}/3), {freshness} — checking next")
            else:
                consecutive_old = 0
                _log(f"  Page {page_num}: fresh jobs, {freshness}")

            if not has_more:
                _log("  No more pages (API)")
                break

            page_num += 1
            if max_pages and page_num > max_pages:
                _log(f"  {site['name']}: page limit ({max_pages}) reached — stopping at page {page_num}")
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

        return results, skipped_excl, skipped_err, newest_seen
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# Title exclusion filter
# ---------------------------------------------------------------------------

def _priority_score(job: dict, site_name: str) -> str:
    t = job["title"].lower()
    score = 0
    for pat in PRIORITY_CONFIG["strong_title_patterns"]:
        if re.search(pat, t, re.IGNORECASE):
            score += 3
    for pat in PRIORITY_CONFIG["supporting_title_patterns"]:
        if re.search(pat, t, re.IGNORECASE):
            score += 1
    for pat in PRIORITY_CONFIG["downgrade_patterns"]:
        if re.search(pat, t, re.IGNORECASE):
            score -= 2
    if site_name in PRIORITY_CONFIG["preferred_orgs"]:
        score += 1
    return "primary" if score >= PRIORITY_CONFIG["score_thresholds"]["primary"] else "secondary"


def _build_top_matches(richmond: list[tuple[str, dict]], others: list[tuple[str, dict]]) -> str:
    if not richmond and not others:
        return ""

    def _item(site_name: str, job: dict) -> str:
        loc = job.get("location") or "Location not listed"
        return (
            f'<li style="margin:4px 0;font-size:13px;">'
            f'<a href="{job["url"]}" style="color:#1a4a7a;font-weight:bold;">{job["title"]}</a>'
            f' &mdash; {site_name} &mdash; {loc}'
            f'</li>'
        )

    blocks = []
    if richmond:
        items = "".join(_item(s, j) for s, j in richmond)
        blocks.append(
            f'<div style="background:#f0fdfa;border-left:3px solid #0d9488;padding:10px 14px;margin-bottom:12px;">'
            f'<p style="color:#134e4a;font-weight:bold;margin:0 0 8px 0;font-size:14px;">★ Top Matches &mdash; Richmond ({len(richmond)})</p>'
            f'<ul style="margin:0;padding-left:18px;line-height:1.7;">{items}</ul>'
            f'</div>'
        )
    if others:
        items = "".join(_item(s, j) for s, j in others)
        blocks.append(
            f'<div style="background:#fffbeb;border-left:3px solid #d97706;padding:10px 14px;margin-bottom:12px;">'
            f'<p style="color:#92400e;font-weight:bold;margin:0 0 8px 0;font-size:14px;">★ Top Matches ({len(others)})</p>'
            f'<ul style="margin:0;padding-left:18px;line-height:1.7;">{items}</ul>'
            f'</div>'
        )
    return "".join(blocks)


def _is_excluded_title(title: str, extra_words: frozenset[str] = frozenset()) -> bool:
    t = title.lower()
    if any(phrase in t for phrase in TITLE_EXCLUDE_PHRASES):
        return True
    words = TITLE_EXCLUDE_WORDS | extra_words
    return any(re.search(rf'\b{re.escape(w)}\b', t) for w in words)


# ---------------------------------------------------------------------------
# Email builders
# ---------------------------------------------------------------------------

def _sort_collapsed(results: list[dict], newest_seen: date | None, since_date: date) -> bool:
    """True when scraper hit its threshold with 0 matches and newest date seen is
    suspiciously stale — strong signal that the server returned a broken sort order."""
    if results or newest_seen is None:
        return False
    return newest_seen < since_date - timedelta(days=2)


def _build_site_section(site_name: str, jobs: list[dict], skipped_excl: int, skipped_err: int, sort_warning: bool = False, newest_seen: date | None = None, extra_words: frozenset[str] = frozenset()) -> str:
    shown_jobs = [j for j in jobs if not _is_excluded_title(j["title"], extra_words)]
    filtered = len(jobs) - len(shown_jobs)
    count = len(shown_jobs)
    is_richmond = site_name in PRIORITY_CONFIG["richmond_orgs"]
    scored = sorted(
        [(j, _priority_score(j, site_name)) for j in shown_jobs],
        key=lambda x: 0 if x[1] == "primary" else 1,
    )
    job_items_html = []
    for job, priority in scored:
        if priority == "primary":
            badge_color = "#0d9488" if is_richmond else "#d97706"
            badge = f'<span style="color:{badge_color};font-weight:bold;margin-right:4px;">★</span>'
        else:
            badge = ""
        subtext_parts = [
            job.get("location") or "Location not listed",
            job.get("occupational_category", ""),
            job.get("employment_type", ""),
            job.get("work_hours", ""),
        ]
        subtext = " · ".join(p for p in subtext_parts if p)
        job_items_html.append(
            f'<li style="margin-bottom:10px;">'
            f'{badge}<a href="{job["url"]}" style="color:#1a4a7a;font-weight:bold;">{job["title"]}</a><br>'
            f'<span style="color:#666;font-size:13px;">{subtext}</span>'
            f'</li>'
        )
    body = (
        f'<ul style="padding-left:20px;line-height:1.9;">{"".join(job_items_html)}</ul>'
        if job_items_html else
        '<p style="color:#aaa;font-size:13px;">No new listings today.</p>'
    )
    excl_note = (
        f'<p style="color:#bbb;font-size:12px;margin-top:4px;">{skipped_excl} skipped (clinical/excluded title)</p>'
        if skipped_excl else ""
    )
    err_note = (
        f'<p style="color:#bbb;font-size:12px;margin-top:2px;">{skipped_err} skipped (no structured data)</p>'
        if skipped_err else ""
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
        f'{warning_note}{body}{excl_note}{err_note}{filter_note}'
    )


def build_html_email(results: list[tuple[str, list[dict], int, bool, date | None]], today: date, extra_words: frozenset[str] = frozenset()) -> str:
    total = sum(1 for _, jobs, _, _, _ in results for j in jobs if not _is_excluded_title(j["title"], extra_words))
    date_str = today.strftime('%b %d, %Y')

    richmond_primaries: list[tuple[str, dict]] = []
    other_primaries: list[tuple[str, dict]] = []
    for site_name, jobs, _, _, _ in results:
        for job in jobs:
            if _is_excluded_title(job["title"], extra_words):
                continue
            if _priority_score(job, site_name) == "primary":
                if site_name in PRIORITY_CONFIG["richmond_orgs"]:
                    richmond_primaries.append((site_name, job))
                else:
                    other_primaries.append((site_name, job))
    top_matches_html = _build_top_matches(richmond_primaries, other_primaries)

    sections = '<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">'.join(
        _build_site_section(site_name, jobs, skipped_excl, skipped_err, sort_warning, newest_seen, extra_words)
        for site_name, jobs, skipped_excl, skipped_err, sort_warning, newest_seen in results
        if jobs
    )
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;padding:24px;color:#222;">
  <h2 style="color:#1a4a7a;margin-bottom:4px;">Health Job Alert</h2>
  <p style="color:#888;font-size:13px;margin-top:0;">{date_str} · {total} new posting{"s" if total != 1 else ""}</p>
  {top_matches_html}
  {sections}
  <hr style="border:none;border-top:1px solid #eee;margin-top:32px;">
  <p style="color:#bbb;font-size:12px;">Scraped automatically</p>
</body>
</html>"""


def send_email(results: list[tuple[str, list[dict], int, bool, date | None]], today: date, label: str = "", extra_words: frozenset[str] = frozenset()) -> None:
    total = sum(1 for _, jobs, _, _, _ in results for j in jobs if not _is_excluded_title(j["title"], extra_words))
    tag = f" [{label}]" if label else ""
    subject = f"[Job Alert{tag}] {total} new posting{'s' if total != 1 else ''} — {today.strftime('%Y-%m-%d')}"
    html = build_html_email(results, today, extra_words)

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
        _t0 = time.perf_counter()
        try:
            jobs, skipped_excl, skipped_err, newest_seen = await scraper_fn(browser, site, since_date)
            sort_warning = _sort_collapsed(jobs, newest_seen, since_date)
            if sort_warning:
                _log(f"  {site['name']}: sort collapsed — flagging in email")
            _elapsed = int(time.perf_counter() - _t0)
            _log(f"{site['name']}: {len(jobs)} qualifying job(s), {skipped_excl} excl, {skipped_err} no-data, {_elapsed}s — {_page_freshness(newest_seen)}")
            return (site["name"], jobs, skipped_excl, skipped_err, sort_warning, newest_seen, site.get("email_bucket", "main"))
        except Exception as e:
            _elapsed = int(time.perf_counter() - _t0)
            _log(f"  {site['name']}: ERROR after {_elapsed}s — {e}")
            return (site["name"], [], 0, False, None, site.get("email_bucket", "main"))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Health job scraper")
    parser.add_argument("--since", metavar="YYYY-MM-DD", help="Override since-date (default: yesterday)")
    parser.add_argument("--no-email", action="store_true", help="Skip email; write HTML previews to disk instead")
    parser.add_argument("--weekly", action="store_true", help="Weekly recap: 7-day lookback, primary-only, updates seen_jobs")
    args = parser.parse_args()

    weekly = args.weekly
    since_date = (
        (date.today() - timedelta(days=7)) if weekly
        else date.fromisoformat(args.since) if args.since
        else TODAY
    )
    no_email = args.no_email

    _validate_env()
    _rotate_log()
    _log(f"Run started (since={since_date})")

    try:
        async with async_playwright() as pw:
            _log("Launching browser ...")
            browser = await pw.chromium.launch(headless=True)
            _log("Browser ready")
            seen_record: dict[str, str] = {}
            try:
                sem = asyncio.Semaphore(4)

                # For weekly runs, scale up page caps (the consecutive_empty stop is the
                # real terminator; max_pages is just a daily safety net). Also drop any
                # max_results caps (batch-refresh guards not needed for 7-day lookback).
                # Phenom gets 5× (serial per-job fetches are slow so UVA/Duke run deep);
                # Workday + iCIMS get 3× (batch page loads, faster to paginate).
                def _weekly_site(s: dict, multiplier: int = 3) -> dict:
                    s = dict(s)
                    if "max_pages" in s:
                        s["max_pages"] = s["max_pages"] * multiplier
                    s.pop("max_results", None)
                    return s

                _sites        = [_weekly_site(s, multiplier=5) for s in SITES]        if weekly else SITES
                _workday      = [_weekly_site(s) for s in WORKDAY_SITES]               if weekly else WORKDAY_SITES
                _icims        = [_weekly_site(s) for s in ICIMS_SITES]                 if weekly else ICIMS_SITES
                _emory        = [_weekly_site(s) for s in EMORY_SITES]                 if weekly else list(EMORY_SITES)

                # LPT ordering — slowest sites first so they claim the first 3 slots.
                # Phenom (serial per-job detail fetches, minutes each) >
                # iCIMS/Emory (API intercept, very fast) >
                # Workday (jobFamily-filtered CXS, quick date-exhaustion stop).
                ordered = (
                    [(scrape_site, s) for s in _sites]
                    + [(scrape_icims_site, s) for s in _icims]
                    + [(scrape_emory_site, s) for s in _emory]
                    + [(scrape_workday_site, s) for s in _workday]
                )
                tasks = [_run_site(sem, fn, browser, site, since_date) for fn, site in ordered]
                results = list(await asyncio.gather(*tasks))

                seen_record = (
                    json.loads(SEEN_JOBS_FILE.read_text(encoding="utf-8"))
                    if SEEN_JOBS_FILE.exists() else {}
                )
                today_str = date.today().isoformat()

                if weekly:
                    week_cutoff = (date.today() - timedelta(days=7)).isoformat()

                    # All main results — no dedup filter, include seen jobs
                    all_week = [(n, j, sk_e, sk_p, sw, ns)
                                for n, j, sk_e, sk_p, sw, ns, bkt in results
                                if bkt == "main"]

                    # Add any new URLs found by rescrape to seen_record
                    rescrape_urls: set[str] = set()
                    for name, jobs, _, _, _, _ in all_week:
                        for j in jobs:
                            url = j.get("url", "")
                            rescrape_urls.add(url)
                            if url not in seen_record:
                                if _priority_score(j, name) == "primary":
                                    seen_record[url] = {"first_seen": today_str, "title": j["title"], "site": name}
                                else:
                                    seen_record[url] = today_str

                    # Recover primaries from seen_record this week that the rescrape missed
                    missed_by_site: dict[str, list[dict]] = {}
                    for url, v in seen_record.items():
                        if (
                            isinstance(v, dict)
                            and v["first_seen"] >= week_cutoff
                            and url not in rescrape_urls
                        ):
                            missed_by_site.setdefault(v["site"], []).append({
                                "url": url,
                                "title": v["title"],
                                "date": date.fromisoformat(v["first_seen"]),
                            })

                    # Merge missed jobs back into their site slot (or append a new slot)
                    for site_name, missed_jobs in missed_by_site.items():
                        for i, (n, j, sk_e, sk_p, sw, ns) in enumerate(all_week):
                            if n == site_name:
                                all_week[i] = (n, j + missed_jobs, sk_e, sk_p, sw, ns)
                                break
                        else:
                            all_week.append((site_name, missed_jobs, 0, 0, False, None))

                    def _filter_primary(bucket_results):
                        return [
                            (name,
                             [j for j in jobs
                              if not _is_excluded_title(j["title"])
                              and _priority_score(j, name) == "primary"],
                             sk_e, sk_p, sort_warn, newest)
                            for name, jobs, sk_e, sk_p, sort_warn, newest in bucket_results
                        ]

                    def _has_content(bucket_results, extra_words=frozenset()):
                        visible = sum(1 for _, jobs, _, _, _, _ in bucket_results
                                      for j in jobs if not _is_excluded_title(j["title"], extra_words))
                        return visible > 0 or any(sk_e or sk_p or sw for _, _, sk_e, sk_p, sw, _ in bucket_results)

                    primary_results = _filter_primary(all_week)
                    for _name, _jobs, _, _, _, _ in primary_results:
                        for _j in _jobs:
                            if not _is_excluded_title(_j["title"]):
                                _log(f"  WEEKLY PRIMARY ({_name}) — {_j['title'][:70]}")
                    if no_email:
                        if _has_content(primary_results):
                            html = build_html_email(primary_results, since_date)
                            out = BASE_DIR / "preview_weekly.html"
                            out.write_text(html, encoding="utf-8")
                            _log(f"  --no-email: wrote {out.name}")
                    else:
                        if _has_content(primary_results):
                            send_email(primary_results, since_date, label="Recap")
                        else:
                            _log("  Weekly recap: no primary matches this week, skipping email")

                else:
                    seen_urls = _load_seen_jobs()

                    def _dedup(bucket_results):
                        deduped = []
                        for name, jobs, sk_e, sk_p, sort_warn, newest in bucket_results:
                            fresh = []
                            for j in jobs:
                                url = j.get("url", "")
                                if url in seen_urls:
                                    _log(f"  SEEN — skipping {j.get('title','')[:60]}")
                                else:
                                    fresh.append(j)
                                    if _priority_score(j, name) == "primary":
                                        seen_record[url] = {"first_seen": today_str, "title": j["title"], "site": name}
                                    else:
                                        seen_record[url] = today_str
                            deduped.append((name, fresh, sk_e, sk_p, sort_warn, newest))
                        return deduped

                    main_results  = _dedup([(n, j, sk_e, sk_p, sw, ns) for n, j, sk_e, sk_p, sw, ns, bkt in results if bkt == "main"])
                    payer_results = _dedup([(n, j, sk_e, sk_p, sw, ns) for n, j, sk_e, sk_p, sw, ns, bkt in results if bkt == "payer"])

                    def _has_content(bucket_results, extra_words=frozenset()):
                        visible = sum(1 for _, jobs, _, _, _, _ in bucket_results
                                      for j in jobs if not _is_excluded_title(j["title"], extra_words))
                        return visible > 0 or any(sk_e or sk_p or sw for _, _, sk_e, sk_p, sw, _ in bucket_results)

                    if no_email:
                        for label, bucket, extra in [
                            ("Main",  main_results,  frozenset()),
                            ("Payer", payer_results, frozenset(PAYER_EXCLUDE_WORDS)),
                        ]:
                            if _has_content(bucket, extra):
                                html = build_html_email(bucket, since_date, extra)
                                out = BASE_DIR / f"preview_{label.lower()}.html"
                                out.write_text(html, encoding="utf-8")
                                _log(f"  --no-email: wrote {out.name}")
                    else:
                        if _has_content(main_results):
                            send_email(main_results, TODAY)
                        if _has_content(payer_results, frozenset(PAYER_EXCLUDE_WORDS)):
                            send_email(payer_results, TODAY, label="Payer", extra_words=frozenset(PAYER_EXCLUDE_WORDS))

            finally:
                _save_seen_jobs(seen_record)
                await browser.close()
    except Exception as e:
        _log(f"ERROR: {e}")
        raise

    _log("Run complete")


if __name__ == "__main__":
    asyncio.run(main())
