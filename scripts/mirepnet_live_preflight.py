"""Pre-session readiness check for MIRepNet live subject testing."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "classifier"), str(ROOT)]

from config import EEG_CHANNELS_MAPPING, EEG_CHANNELS_TARGETS, MIREPNET_DEVICE  # noqa: E402
import mirepnet  # noqa: E402
from ssvep.quality import assess_channels  # noqa: E402
from utils.devices import OpenBCI  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_checkpoint() -> Path:
    paths = [Path(path) for path in mirepnet.list_checkpoints()]
    if not paths:
        raise RuntimeError("No local MIRepNet .pt checkpoints were found")
    print("\nLocal MIRepNet checkpoints:")
    for index, path in enumerate(paths, 1):
        print(f"  {index}) {mirepnet.describe_checkpoint(str(path))}")
    while True:
        raw = input("Checkpoint for tomorrow's live run> ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(paths):
            return paths[int(raw) - 1]
        print(f"Enter 1-{len(paths)}")


def run(args) -> tuple[dict, bool]:
    report = {
        "format": "eegthingy-mirepnet-live-preflight-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "checks": [],
    }

    def check(name, passed, **details):
        report["checks"].append({"name": name, "passed": bool(passed), **details})
        print(f"  {'PASS' if passed else 'FAIL'}  {name}" +
              (f" — {details.get('message')}" if details.get("message") else ""))
        return bool(passed)

    print("\nMIRepNet live-session preflight")
    print("=" * 42)
    checkpoint = args.checkpoint or choose_checkpoint()
    checkpoint = checkpoint.resolve()
    check("checkpoint exists", checkpoint.is_file(), path=str(checkpoint))
    if not checkpoint.is_file():
        return report, False
    check("checkpoint is non-empty", checkpoint.stat().st_size > 1_000_000,
          bytes=checkpoint.stat().st_size)
    report["checkpoint"] = {"path": str(checkpoint), "sha256": sha256(checkpoint)}

    try:
        model = mirepnet.MIRepNetDecoder.load(str(checkpoint), device=args.device)
        check("checkpoint loads", True, device=str(model.device_), classes=model.classes_.tolist())
        training_subjects = sorted({
            int(match.group(1))
            for name in model.meta_.get("trained_on", [])
            if (match := re.search(r"subject(\d+)", Path(name).name))
        })
        report["checkpoint"]["training_subjects"] = training_subjects
        if args.subject is not None:
            check("planned subject absent from checkpoint training",
                  args.subject not in training_subjects,
                  subject=args.subject, training_subjects=training_subjects,
                  message=(f"S{args.subject:02d} is in checkpoint training data"
                           if args.subject in training_subjects else "no subject leakage detected"))
        probe = np.random.default_rng(7).normal(0, 1e-6, (15, 750))
        window = model.prepare_window(probe, 125.0)
        probabilities = model.predict_proba(window)[0]
        valid = probabilities.shape == (2,) and np.isfinite(probabilities).all() and abs(probabilities.sum() - 1) < 1e-5
        check("end-to-end inference", valid, probabilities=probabilities.tolist(),
              input_shape=list(window.shape))
    except Exception as error:
        check("checkpoint loads and infers", False, message=repr(error))

    required = ["brainflow", "mne", "numpy", "safetensors", "torch", "websockets"]
    versions = {}
    for package in required:
        try:
            versions[package] = importlib.metadata.version(package)
            check(f"dependency {package}", True, version=versions[package])
        except importlib.metadata.PackageNotFoundError:
            check(f"dependency {package}", False, message="not installed")
    report["versions"] = versions
    free = shutil.disk_usage(ROOT / "recordings").free
    check("recording disk space", free >= 5 * 1024**3, free_bytes=free,
          message=f"{free / 1024**3:.1f} GiB free")
    check("15-channel mapping", len(EEG_CHANNELS_TARGETS) == 15 and
          all(name in EEG_CHANNELS_MAPPING for name in EEG_CHANNELS_TARGETS))

    if args.hardware:
        bci = OpenBCI(interval=0.2, synthetic=args.synthetic, serial_port=args.serial).open()
        if bci.board is None:
            check("OpenBCI connection", False, message=repr(bci.last_error))
        else:
            check("OpenBCI connection", True, serial_port=bci.serial_port,
                  board_id=bci.board.board_id)
            try:
                bci.start()
                print(f"  Recording {args.seconds:g} seconds for cap/channel checks...")
                time.sleep(args.seconds)
                captured = bci.stop()
                eeg = captured["eeg"]
                sfreq = float(bci.board.get_sampling_rate(bci.board.board_id))
                expected = int(args.seconds * sfreq)
                check("EEG samples received", eeg.shape[1] >= expected * 0.70,
                      received=eeg.shape[1], expected=expected)
                indices = [EEG_CHANNELS_MAPPING.index(name) for name in EEG_CHANNELS_TARGETS]
                quality = assess_channels(eeg[indices], sfreq)
                report["channel_quality"] = [row.__dict__ for row in quality]
                bad = [row.channel for row in quality if row.status == "BAD"]
                caution = [row.channel for row in quality if row.status == "CHECK"]
                check("no bad connected channels", not bad, bad_channels=bad,
                      check_channels=caution,
                      message=("bad=" + ",".join(bad)) if bad else
                              (("review=" + ",".join(caution)) if caution else "all usable"))
                check("no channels needing contact/noise review", not caution,
                      check_channels=caution,
                      message=("review=" + ",".join(caution)) if caution else "all clear")
            finally:
                bci.close()
    else:
        check("hardware/cap stream", False,
              message="not run; repeat with --hardware before the subject session")

    passed = all(item["passed"] for item in report["checks"])
    return report, passed


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint", type=Path)
    ap.add_argument("--device", default=MIREPNET_DEVICE)
    ap.add_argument("--subject", type=int, help="planned live subject ID for leakage checking")
    ap.add_argument("--hardware", action="store_true", help="connect and test the cap/board")
    ap.add_argument("--synthetic", action="store_true", help="software dry-run board")
    ap.add_argument("--serial", default="", help="OpenBCI COM port; blank auto-detects")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--output", type=Path)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    report, passed = run(args)
    output = args.output or ROOT / "recordings" / "diagnostic-sessions" / (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_preflight.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nPreflight report: {output}")
    print("READY" if passed else "NOT READY — resolve failed checks before subject testing")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
