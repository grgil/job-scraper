"""
Parse scraper.log and print a per-site performance table for a given run.

Usage:
  python perf_report.py            # most recent complete run
  python perf_report.py --run 2    # second-most-recent complete run
"""
import argparse
import re
import sys
from pathlib import Path

LOG_FILE = Path(__file__).parent / "scraper.log"

# Matches: [2026-05-20 06:01:23] message
_LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.+)$")

# Matches summary lines emitted by _run_site:
# "Site Name: N qualifying job(s), M skipped, Xs — newest_seen=..."
_SUMMARY_RE = re.compile(
    r"^(.+?): (\d+) qualifying job\(s\), (\d+) skipped, (\d+)s — (.+)$"
)

# Matches platform announcement lines emitted at scrape function entry:
# "Scraping Site Name (Platform) ..."
_PLATFORM_RE = re.compile(r"^Scraping (.+?) \((.+?)\)")

# Stop-reason patterns (may or may not appear, concurrent logs can interleave)
_STOP_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("batch_refresh",   re.compile(r"WARN — batch refresh suspected \((.+?)\)")),
    ("max_pages",       re.compile(r"Page limit \(\d+\) reached")),
    ("no_more_pages",   re.compile(r"No more pages|no more pages|no_more_pages")),
    ("all_old",         re.compile(r"all_old")),
    ("sort_collapsed",  re.compile(r"sort still collapsed")),
]


def _parse_runs(lines: list[str]) -> list[list[str]]:
    """Split log into complete runs (between 'Run started' markers)."""
    runs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        m = _LINE_RE.match(line)
        if not m:
            continue
        msg = m.group(2)
        if msg.startswith("Run started"):
            if current:
                runs.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        runs.append(current)
    return runs


def _extract_messages(run_lines: list[str]) -> list[str]:
    return [_LINE_RE.match(l).group(2) for l in run_lines if _LINE_RE.match(l)]


def _build_report(messages: list[str]) -> list[dict]:
    platform_map: dict[str, str] = {}
    for msg in messages:
        pm = _PLATFORM_RE.match(msg)
        if pm:
            platform_map[pm.group(1)] = pm.group(2)

    # Collect stop reasons per site (batch_refresh names the site; others are positional)
    stop_map: dict[str, str] = {}
    for msg in messages:
        for reason, pat in _STOP_PATTERNS:
            m = pat.search(msg)
            if m:
                site_name = m.group(1) if pat.groups else None
                if site_name:
                    stop_map[site_name] = reason
                # positional stops are logged inline — hard to attribute without name; skip

    rows = []
    for msg in messages:
        sm = _SUMMARY_RE.match(msg)
        if not sm:
            continue
        name, qualifying, skipped, elapsed, freshness = sm.groups()
        rows.append({
            "site":       name,
            "platform":   platform_map.get(name, "?"),
            "qualifying": int(qualifying),
            "skipped":    int(skipped),
            "elapsed_s":  int(elapsed),
            "freshness":  freshness,
            "stop":       stop_map.get(name, "—"),
        })
    return rows


def _print_table(rows: list[dict], run_header: str) -> None:
    if not rows:
        print("No site summary lines found in this run.")
        print("(perf_report requires runs produced after elapsed timing was added to scraper.py)")
        return

    cols = [
        ("Site",       "site",       max(len(r["site"])       for r in rows)),
        ("Platform",   "platform",   max(len(r["platform"])   for r in rows)),
        ("Elapsed",    "elapsed_s",  7),
        ("Qualifying", "qualifying", 9),
        ("Skipped",    "skipped",    7),
        ("Stop",       "stop",       max(len(r["stop"])       for r in rows)),
        ("Freshness",  "freshness",  max(len(r["freshness"])  for r in rows)),
    ]
    # enforce header widths
    cols = [(h, k, max(w, len(h))) for h, k, w in cols]

    sep  = "| " + " | ".join("-" * w for _, _, w in cols) + " |"
    head = "| " + " | ".join(h.ljust(w) for h, _, w in cols) + " |"

    print(f"\n{run_header}")
    print(head)
    print(sep)
    for r in rows:
        def _fmt(key, w):
            v = r[key]
            return (f"{v}s" if key == "elapsed_s" else str(v)).ljust(w)
        print("| " + " | ".join(_fmt(k, w) for _, k, w in cols) + " |")

    total_s = sum(r["elapsed_s"] for r in rows)
    total_q = sum(r["qualifying"] for r in rows)
    total_sk = sum(r["skipped"] for r in rows)
    print(f"\nTotals: {total_q} qualifying, {total_sk} skipped, {total_s}s wall-clock sum")
    print("(wall-clock sum > actual run time because sites execute concurrently)\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=int, default=1,
                        help="Which run to show (1 = most recent, 2 = second-most-recent, …)")
    args = parser.parse_args()

    if not LOG_FILE.exists():
        sys.exit(f"Log file not found: {LOG_FILE}")

    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    runs = _parse_runs(lines)

    if not runs:
        sys.exit("No runs found in log file.")

    idx = -args.run
    try:
        run = runs[idx]
    except IndexError:
        sys.exit(f"Only {len(runs)} run(s) in log; --run {args.run} is out of range.")

    first_line = _LINE_RE.match(run[0])
    header = f"Run {len(runs) - (len(runs) + idx)} of {len(runs)}"
    if first_line:
        header += f"  |  started {first_line.group(1)}"

    messages = _extract_messages(run)
    rows = _build_report(messages)
    _print_table(rows, header)


if __name__ == "__main__":
    main()
