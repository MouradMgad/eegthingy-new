"""Paper-ready folder, report parsing, and summary-workbook helpers."""

from __future__ import annotations

import csv
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAPER_RESULTS = ROOT / "paper results"
SIMULATION_SRC = ROOT / "simulation" / "src"
if str(SIMULATION_SRC) not in sys.path:
    sys.path.insert(0, str(SIMULATION_SRC))

from tiago_maze.scoring import summarize_corner_score


def slug(value) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-_.")
    return value or "na"


def create_session_folder(subject, test, root=PAPER_RESULTS, when=None) -> Path:
    """Create an overwrite-safe ``subject_<id>_test_<id>`` result folder."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    base = root / f"subject_{slug(subject)}_test_{slug(test)}"
    path = base
    if path.exists():
        stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S")
        path = root / f"{base.name}_{stamp}"
        suffix = 2
        while path.exists():
            path = root / f"{base.name}_{stamp}_{suffix}"
            suffix += 1
    path.mkdir(parents=True, exist_ok=False)
    return path.resolve()


def copy_artifact(source, session_dir, name=None):
    source = Path(source)
    if not source.is_file():
        return None
    target = Path(session_dir) / (name or source.name)
    shutil.copy2(source, target)
    return target


def read_maze_report(path):
    """Read the simulator's two-section CSV without changing raw observations."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    try:
        divider = rows.index([])
    except ValueError as error:
        raise ValueError(f"maze report has no summary/detail divider: {path}") from error
    summary = {row[0]: row[1] if len(row) > 1 else "" for row in rows[1:divider] if row}
    headers = rows[divider + 1]
    details = [dict(zip(headers, row)) for row in rows[divider + 2:] if row]
    return summary, details


def _number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return value


def build_summary_payload(session_dir, subject, test, maze_number, maze_seed,
                          decoder, started_at, ended_at=None, report_path=None,
                          checkpoint=None, calibration=None):
    session_dir = Path(session_dir).resolve()
    ended_at = ended_at or datetime.now()
    summary, corners = ({}, [])
    if report_path and Path(report_path).is_file():
        summary, corners = read_maze_report(report_path)
    typed_corners = []
    for row in corners:
        typed_corners.append({key: _number(value) for key, value in row.items()})
    score = summarize_corner_score(typed_corners)
    typed_corners = score["records"]
    completed = str(summary.get("completed", "")).lower() == "true"
    corrected_summary = {key: _number(value) for key, value in summary.items()}
    corrected_summary.update({
        "raw_physical_turns": len(typed_corners),
        "raw_reported_decisions": _number(summary.get("decisions")),
        "raw_reported_correct": _number(summary.get("correct")),
        "raw_reported_accuracy": _number(summary.get("accuracy")),
        "decisions": score["decisions"],
        "correct": score["correct"],
        "wrong_turns": score["wrong"],
        "accuracy": round(score["accuracy"], 6),
        "recovery_turns": score["recovery_attempts"],
        "scoring_method": "first decision per planned corner; correction turns excluded",
    })
    payload = {
        "format": "eegthingy-paper-maze-result-v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": {
            "subject": str(subject),
            "test": str(test),
            "maze_number": int(maze_number),
            "maze_seed": int(maze_seed),
            "decoder": str(decoder),
            "checkpoint": str(checkpoint or "session model"),
            "calibration": str(calibration or "none"),
            "start_time": started_at.isoformat(timespec="seconds"),
            "end_time": ended_at.isoformat(timespec="seconds"),
            "wall_duration_s": round((ended_at - started_at).total_seconds(), 3),
            "completed": completed,
            "simulation_time_s": _number(summary.get("total_time_s")),
            "decisions": score["decisions"],
            "correct": score["correct"],
            "wrong": score["wrong"],
            "accuracy": round(score["accuracy"], 6),
            "raw_physical_turns": len(typed_corners),
            "recovery_attempts": score["recovery_attempts"],
            "scoring_method": "first decision per planned corner; correction turns excluded",
            "report_file": Path(report_path).name if report_path else "",
        },
        "maze_summary": corrected_summary,
        "corners": typed_corners,
        "files": sorted(
            [
                {"name": item.name, "size_bytes": item.stat().st_size}
                for item in session_dir.iterdir() if item.is_file()
            ],
            key=lambda item: item["name"].lower(),
        ),
    }
    return payload


def write_summary_workbook(payload, session_dir):
    from scripts.mirepnet_excel import export_paper_results_workbook

    session_dir = Path(session_dir).resolve()
    json_path = session_dir / "results summary.json"
    xlsx_path = session_dir / "results summary.xlsx"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    export_paper_results_workbook(json_path, xlsx_path)
    return xlsx_path


def write_session_manifest(session_dir, **values):
    path = Path(session_dir) / "session_manifest.json"
    payload = {
        "format": "eegthingy-paper-session-v1",
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        **values,
    }
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path

