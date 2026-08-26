"""Daily run for cron.

    python daily.py                    every active site
    python daily.py --site 1           just site 1
    python daily.py --dry-run          discover + score, write no drafts
    python daily.py --window week      only look at the last week
    python daily.py --max-probes 20    cap the searches (faster test run)

Windows Task Scheduler:
    Program:   C:\\Path\\To\\python.exe
    Arguments: daily.py
    Start in:  C:\\Path\\To\\authority-agent

Linux cron (VPS), 09:15 daily:
    15 9 * * * cd /opt/authority-agent && /usr/bin/python3 daily.py >> logs/daily.log 2>&1
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aa.schedule import run_all

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", type=int, default=None)
    ap.add_argument("--window", default="month",
                    choices=["day", "week", "month", "year", "all"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-probes", type=int, default=None)
    a = ap.parse_args()

    results = run_all(site_id=a.site, window=a.window,
                      dry_run=a.dry_run, max_probes=a.max_probes)
    sys.exit(1 if any(r.get("error") for r in results) else 0)
