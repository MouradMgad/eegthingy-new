"""Re-run recorded live decisions and verify the recovered maze videos."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SIMULATION_SRC = ROOT / "simulation" / "src"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_report(path: Path) -> tuple[dict, list[dict]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    divider = rows.index([])
    summary = {row[0]: row[1] for row in rows[1:divider] if len(row) > 1}
    headers = rows[divider + 1]
    decisions = [dict(zip(headers, row)) for row in rows[divider + 2:] if row]
    return summary, decisions


def _session_sources(session_dir: Path) -> tuple[Path, Path, Path, int]:
    traces = sorted(session_dir.glob("SUBJECT*.npz"))
    if len(traces) != 1:
        raise RuntimeError(f"expected one pose trace in {session_dir}, found {len(traces)}")
    reports = sorted(session_dir.glob("SUBJECT*.csv"))
    if len(reports) != 1:
        raise RuntimeError(f"expected one maze report in {session_dir}, found {len(reports)}")
    summary = session_dir / "results summary.xlsx"
    payload_path = session_dir / "results summary.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    return traces[0], reports[0], summary, int(payload["session"]["maze_number"])


def reconstruct(session_dir: Path, fps: float) -> Path:
    _, report, _, _ = _session_sources(session_dir)
    output = session_dir / "maze_session_replay.mp4"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing reconstruction: {output}")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(SIMULATION_SRC), env.get("PYTHONPATH", "")) if value
    )
    subprocess.run(
        [
            sys.executable, "-m", "tiago_maze", "--session-report", str(report),
            "--offscreen", "--no-report", "--no-trace",
            "--record-maze-video", str(output), "--video-fps", str(fps), "--quiet",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )
    return output


def verify(session_dir: Path) -> dict:
    trace, report, workbook, maze_number = _session_sources(session_dir)
    video = session_dir / "maze_session_replay.mp4"
    source_summary, source_decisions = _read_report(report)
    trace_data = np.load(trace, allow_pickle=False)
    trace_duration = float(trace_data["t"][-1])
    source_duration = float(source_summary.get("total_time_s", trace_duration))

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV cannot open {video}")
    declared_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    decoded_frames = 0
    while True:
        ok, _ = capture.read()
        if not ok:
            break
        decoded_frames += 1
    capture.release()

    video_duration = decoded_frames / fps if fps else 0.0
    duration_delta = video_duration - source_duration
    valid = (
        decoded_frames == declared_frames
        and width > 0 and height > 0 and fps > 0
        and abs(duration_delta) <= 6.0
    )
    manifest = {
        "format": "eegthingy-maze-video-session-replay-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "session": session_dir.name,
        "standard_maze": maze_number,
        "method": "deterministic re-simulation of every recorded left/right decision and wait time",
        "source_decision_report": report.name,
        "source_results_workbook": workbook.name,
        "validation_pose_trace": trace.name,
        "source_decisions": len(source_decisions),
        "original_video_preserved": (session_dir / "maze.mp4").name,
        "output": video.name,
        "sha256": _sha256(video),
        "trace_duration_s": round(trace_duration, 3),
        "source_report_duration_s": round(source_duration, 3),
        "video_duration_s": round(video_duration, 3),
        "duration_delta_s": round(duration_delta, 3),
        "duration_tolerance_s": 6.0,
        "fps": fps,
        "width": width,
        "height": height,
        "declared_frames": declared_frames,
        "decoded_frames": decoded_frames,
        "full_decode_verified": valid,
    }
    manifest_path = session_dir / "maze_reconstruction_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if not valid:
        raise RuntimeError(f"reconstructed video failed verification: {manifest}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    for raw_session in args.sessions:
        session = raw_session.resolve()
        if not args.verify_only:
            reconstruct(session, args.fps)
        result = verify(session)
        print(
            f"{result['session']}: {result['decoded_frames']} frames, "
            f"{result['video_duration_s']:.2f}s, sha256={result['sha256']}"
        )


if __name__ == "__main__":
    main()
