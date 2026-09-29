"""CSV run report.

Writes one file per run named by timestamp and seed:

    tiago_maze_<YYYYmmdd_HHMMSS>_seed<seed>.csv

The file has two sections separated by a blank line:
  1. a summary block (field,value) — seed, timing, decision stats;
  2. the full per-decision table (one row per decision the robot made).

Load the decision table with e.g. pandas:
    pandas.read_csv(path, skiprows=<n_summary_lines>+2)
or just split the file on the blank line.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path


def _slug(s) -> str:
    """Filename-safe token; empty/None becomes 'na'."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(s or "").strip()).strip("-_.")
    return s or "na"

# Column order for the per-decision table.
DECISION_FIELDS = [
    "decision",        # 1-based decision index across the whole run
    "turn",            # corner number (1-based) this decision was made at
    "corner_x",        # corner world position
    "corner_y",
    "intended_dir",    # the correct direction for this corner (LEFT/RIGHT)
    "chosen_dir",      # what was actually chosen
    "result",          # correct / wrong (did the robot end up facing the path?)
    "scored",          # True only for the first EEG/model choice at this corner
    "attempt_role",    # initial decision / subject correction / automatic recovery
    "corner_attempt",  # 1-based physical attempt number at this corner
    "wait_s",          # wall-clock time spent waiting for this decision
    "sim_time_s",      # elapsed run time when the decision was made
    "robot_x",         # robot pose at the moment of decision
    "robot_y",
    "robot_yaw_deg",
    "front_m",         # front laser distance at the stop
    "source",          # model / recovery / manual_or_websocket
    "epoch",           # recorded EEG test-epoch index
    "file",            # source FIF recording
    "epoch_in_file",
    "true_label",      # 1=LEFT, 2=RIGHT (builds the open corridor)
    "predicted_label", # 1=LEFT, 2=RIGHT (steers the robot)
    "confidence",
]


def read_report(path) -> tuple[dict, list[dict]]:
    """Read a two-section simulator CSV into summary and decision rows."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    try:
        divider = rows.index([])
    except ValueError as error:
        raise ValueError(f"maze report has no summary/detail divider: {path}") from error
    summary = {
        row[0]: row[1] if len(row) > 1 else ""
        for row in rows[1:divider] if row
    }
    headers = rows[divider + 1]
    decisions = [dict(zip(headers, row)) for row in rows[divider + 2:] if row]
    return summary, decisions


def report_filename(seed, when: datetime | None = None,
                     subject=None, test=None) -> str:
    when = when or datetime.now()
    ts = when.strftime("%Y%m%d_%H%M%S")
    if subject or test:
        return f"SUBJECT{_slug(subject)}_TEST{_slug(test)}_SEED{seed}_{ts}.csv"
    return f"tiago_maze_{ts}_seed{seed}.csv"


def write_report(
    out_dir,
    meta: dict,
    records: list[dict],
    when: datetime | None = None,
    subject=None,
    test=None,
) -> Path:
    """Write the summary + per-decision CSV, returning its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / report_filename(meta.get("seed"), when, subject, test)

    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["field", "value"])
        for k, v in meta.items():
            w.writerow([k, v])
        w.writerow([])  # blank separator line
        w.writerow(DECISION_FIELDS)
        for r in records:
            w.writerow([r.get(k, "") for k in DECISION_FIELDS])

    return path
