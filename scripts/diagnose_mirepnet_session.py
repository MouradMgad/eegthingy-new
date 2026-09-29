"""Summarize or recover a recorded MIRepNet diagnostic session."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import mne
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import EEG_CHANNELS_MAPPING, EEG_CHANNELS_TARGETS  # noqa: E402
from ssvep.quality import assess_channels  # noqa: E402


def recover_raw(session: Path) -> Path | None:
    journal_path = session / "raw_eeg_journal.json"
    binary_path = session / "raw_eeg_microvolts.f32"
    if not journal_path.exists() or not binary_path.exists():
        return None
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    channels = int(journal["channels"])
    sfreq = float(journal["sample_rate_hz"])
    samples = binary_path.stat().st_size // (4 * channels)
    if not samples:
        return None
    data = np.memmap(binary_path, dtype="<f4", mode="r", shape=(samples, channels)).T
    names = list(EEG_CHANNELS_MAPPING[:channels])
    types = ["misc" if name == "DEAD" else "eeg" for name in names]
    raw = mne.io.RawArray(np.asarray(data, dtype=np.float64) / 1e6,
                          mne.create_info(names, sfreq, types), verbose=False)
    raw.set_montage("standard_1020", on_missing="ignore")
    output = session / "raw_eeg_recovered_raw.fif"
    raw.save(output, overwrite=True, verbose=False)
    return output


def diagnose(session: Path) -> dict:
    manifest_path = session / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    event_rows = []
    events_path = session / "events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event_rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    decisions = []
    decision_path = session / "decisions.csv"
    if decision_path.exists():
        with decision_path.open(newline="", encoding="utf-8") as handle:
            decisions = list(csv.DictReader(handle))

    raw_path = session / "raw_eeg_recovered_raw.fif"
    if not raw_path.exists():
        recovered = recover_raw(session)
        if recovered:
            raw_path = recovered

    findings = []
    if manifest.get("status") == "failed":
        findings.append({"severity": "error", "message": manifest.get("error") or "session failed"})
    event_names = {row.get("event") for row in event_rows}
    if "board_connected" not in event_names:
        findings.append({"severity": "error", "message": "OpenBCI never reached the connected state"})
    if not decisions:
        findings.append({"severity": "warning", "message": "No classified decision windows were recorded"})
    else:
        confidences = np.asarray([float(row["confidence"]) for row in decisions])
        commits = sum(int(row["committed"]) in (1, 2) for row in decisions)
        if commits == 0:
            findings.append({"severity": "warning", "message": "No decision passed the confidence/vote/dwell gates"})
        if float(np.median(confidences)) < 0.60:
            findings.append({"severity": "warning", "message": "Median confidence was below 60%"})

    quality = []
    if raw_path.exists():
        raw = mne.io.read_raw_fif(raw_path, preload=True, verbose="ERROR")
        if all(name in raw.ch_names for name in EEG_CHANNELS_TARGETS):
            eeg_uv = raw.get_data(picks=EEG_CHANNELS_TARGETS) * 1e6
            quality = [row.__dict__ for row in assess_channels(eeg_uv, raw.info["sfreq"])]
            bad = [row["channel"] for row in quality if row["status"] == "BAD"]
            caution = [row["channel"] for row in quality if row["status"] == "CHECK"]
            if bad:
                findings.append({"severity": "error", "message": "Bad EEG channels: " + ", ".join(bad)})
            if caution:
                findings.append({"severity": "warning", "message": "Noisy EEG channels: " + ", ".join(caution)})
    else:
        findings.append({"severity": "error", "message": "No recoverable raw EEG journal was found"})

    result = {
        "session": str(session.resolve()),
        "status": manifest.get("status", "unknown"),
        "events": len(event_rows),
        "decision_windows": len(decisions),
        "committed_decisions": sum(int(row["committed"]) in (1, 2) for row in decisions),
        "raw_file": str(raw_path) if raw_path.exists() else None,
        "findings": findings,
        "channel_quality": quality,
    }
    (session / "diagnosis.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", type=Path, nargs="?")
    args = ap.parse_args(argv)
    session = args.session
    if session is None:
        candidates = sorted((ROOT / "recordings" / "diagnostic-sessions").glob("*_mirepnet_*"))
        if not candidates:
            raise SystemExit("No MIRepNet diagnostic sessions found")
        session = candidates[-1]
    result = diagnose(session.resolve())
    print(json.dumps({key: value for key, value in result.items() if key != "channel_quality"}, indent=2))
    print(f"\nDetailed diagnosis: {session / 'diagnosis.json'}")


if __name__ == "__main__":
    main()
