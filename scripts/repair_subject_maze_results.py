"""Rebuild existing paper-result workbooks with corner-level scoring."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.paper_results import build_summary_payload, write_summary_workbook


def repair_session(session_dir: Path, output_dir: Path) -> tuple[Path, dict]:
    session_dir = session_dir.resolve()
    old_payload = json.loads((session_dir / "results summary.json").read_text(encoding="utf-8"))
    session = old_payload["session"]
    report_path = session_dir / session["report_file"]
    payload = build_summary_payload(
        session_dir=session_dir,
        subject=session["subject"],
        test=session["test"],
        maze_number=session["maze_number"],
        maze_seed=session["maze_seed"],
        decoder=session["decoder"],
        started_at=datetime.fromisoformat(session["start_time"]),
        ended_at=datetime.fromisoformat(session["end_time"]),
        report_path=report_path,
        checkpoint=session.get("checkpoint"),
        calibration=session.get("calibration"),
    )
    workbook = write_summary_workbook(payload, session_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    final_copy = output_dir / f"{session_dir.name}_results_summary.xlsx"
    shutil.copy2(workbook, final_copy)
    return final_copy, payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    for session_dir in args.sessions:
        workbook, payload = repair_session(session_dir, args.output_dir)
        result = payload["session"]
        print(
            f"{session_dir.name}: {result['correct']}/{result['decisions']} "
            f"({100 * result['accuracy']:.1f}%), "
            f"{result['recovery_attempts']} correction turns excluded -> {workbook}"
        )


if __name__ == "__main__":
    main()
