"""Crash-resilient diagnostic capture for live decoder sessions."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

import mne
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / "recordings" / "diagnostic-sessions"
_ACTIVE = None


class _Tee:
    def __init__(self, original, log):
        self.original, self.log = original, log
        self.lock = threading.Lock()

    def write(self, value):
        with self.lock:
            self.original.write(value)
            self.log.write(value)
            self.log.flush()
        return len(value)

    def flush(self):
        with self.lock:
            self.original.flush()
            self.log.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DiagnosticSession:
    """Persist console, events, decisions, and raw EEG as they happen."""

    def __init__(self, mode: str):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = SESSIONS / f"{stamp}_{mode}"
        self.path.mkdir(parents=True, exist_ok=False)
        self.mode = mode
        self.console = (self.path / "console.log").open("a", encoding="utf-8", buffering=1)
        self.events = (self.path / "events.jsonl").open("a", encoding="utf-8", buffering=1)
        self.decisions_file = (self.path / "decisions.csv").open(
            "a", newline="", encoding="utf-8", buffering=1
        )
        self.decisions = csv.writer(self.decisions_file)
        self.decisions.writerow(["time", "log_odds", "center", "margin", "prediction",
                                 "confidence", "committed"])
        self.old_stdout = self.old_stderr = None
        self.raw_handle = None
        self.raw_channels = None
        self.raw_sfreq = None
        self.raw_samples = 0
        self.raw_lock = threading.Lock()
        self.paper_result_dir = None

    def __enter__(self):
        global _ACTIVE
        _ACTIVE = self
        self.old_stdout, self.old_stderr = sys.stdout, sys.stderr
        sys.stdout = _Tee(sys.stdout, self.console)
        sys.stderr = _Tee(sys.stderr, self.console)
        self._write_manifest("running")
        self.event("session_started", mode=self.mode, directory=str(self.path))
        print(f"\nDiagnostic session recording started: {self.path}")
        return self

    def event(self, name: str, **values):
        payload = {"time_utc": datetime.now(timezone.utc).isoformat(), "event": name, **values}
        self.events.write(json.dumps(payload, default=str) + "\n")
        self.events.flush()

    def set_checkpoint(self, path):
        if not path:
            self.event("checkpoint", path=None)
            return
        p = Path(path).resolve()
        self.event("checkpoint", path=str(p), size=p.stat().st_size, sha256=_sha256(p))

    def start_eeg(self, channels: int, sfreq: float):
        with self.raw_lock:
            if self.raw_handle is None:
                self.raw_channels = int(channels)
                self.raw_sfreq = float(sfreq)
                self.raw_handle = (self.path / "raw_eeg_microvolts.f32").open("ab", buffering=0)
                self.event("eeg_journal_started", channels=channels, sample_rate=sfreq)

    def append_eeg(self, eeg_uv: np.ndarray):
        data = np.asarray(eeg_uv, dtype="<f4")
        if data.ndim != 2:
            return
        self.start_eeg(data.shape[0], self.raw_sfreq or 0.0)
        with self.raw_lock:
            # Sample-major layout makes recovery possible from every complete sample.
            self.raw_handle.write(np.ascontiguousarray(data.T).tobytes())
            self.raw_samples += data.shape[1]
            (self.path / "raw_eeg_journal.json").write_text(json.dumps({
                "format": "float32-little-endian-sample-major",
                "channels": self.raw_channels,
                "sample_rate_hz": self.raw_sfreq,
                "complete_samples": self.raw_samples,
                "units": "microvolts",
            }, indent=2), encoding="utf-8")

    def decision(self, row):
        self.decisions.writerow(row)
        self.decisions_file.flush()

    def set_paper_result_dir(self, path):
        """Mirror the completed diagnostic bundle into a paper-session folder."""
        self.paper_result_dir = Path(path).resolve()
        self.event("paper_result_directory", path=str(self.paper_result_dir))

    def _recover_fif(self):
        if not self.raw_channels or not self.raw_samples or not self.raw_sfreq:
            return None
        binary = self.path / "raw_eeg_microvolts.f32"
        usable = binary.stat().st_size // (4 * self.raw_channels)
        data = np.memmap(binary, dtype="<f4", mode="r", shape=(usable, self.raw_channels)).T
        from config import EEG_CHANNELS_MAPPING
        names = list(EEG_CHANNELS_MAPPING[:self.raw_channels])
        types = ["misc" if name == "DEAD" else "eeg" for name in names]
        raw = mne.io.RawArray(np.asarray(data, dtype=np.float64) / 1e6,
                              mne.create_info(names, self.raw_sfreq, types), verbose=False)
        raw.set_montage("standard_1020", on_missing="ignore")
        output = self.path / "raw_eeg_recovered_raw.fif"
        raw.save(output, overwrite=True, verbose=False)
        return output

    def _write_manifest(self, status: str, error: str | None = None):
        try:
            git_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                timeout=5, check=False,
            ).stdout.strip()
        except Exception:
            git_commit = "unavailable"
        payload = {
            "format": "eegthingy-live-diagnostic-v1",
            "status": status,
            "mode": self.mode,
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "working_directory": str(Path.cwd()),
            "command": sys.argv,
            "git_commit": git_commit,
            "free_disk_bytes": shutil.disk_usage(self.path).free,
            "raw_complete_samples": self.raw_samples,
            "error": error,
        }
        (self.path / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def __exit__(self, exc_type, exc, tb):
        global _ACTIVE
        if exc is not None:
            self.event("uncaught_exception", error=repr(exc), traceback="".join(
                traceback.format_exception(exc_type, exc, tb)
            ))
        if self.raw_handle is not None:
            self.raw_handle.close()
            self.raw_handle = None
        try:
            recovered = self._recover_fif()
            if recovered:
                self.event("eeg_recovered", path=str(recovered), samples=self.raw_samples)
        except Exception as recovery_error:
            self.event("eeg_recovery_failed", error=repr(recovery_error))
        status = "failed" if exc is not None else "complete"
        self._write_manifest(status, repr(exc) if exc is not None else None)
        self.event("session_finished", status=status)
        sys.stdout, sys.stderr = self.old_stdout, self.old_stderr
        self.decisions_file.close()
        self.events.close()
        self.console.close()
        if self.paper_result_dir is not None:
            destination = self.paper_result_dir / "diagnostic"
            try:
                destination.mkdir(parents=True, exist_ok=True)
                for source in self.path.iterdir():
                    if source.is_file():
                        shutil.copy2(source, destination / source.name)
            except Exception as mirror_error:
                # The original diagnostic session remains authoritative even if
                # an external/synced paper-results folder is temporarily locked.
                self.old_stderr.write(
                    f"Could not mirror diagnostic bundle to {destination}: {mirror_error}\n"
                )
        _ACTIVE = None
        return False


def active_session() -> DiagnosticSession | None:
    return _ACTIVE
