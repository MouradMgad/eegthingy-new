"""Interactive SSVEP training, testing, cap-check, and live-maze workbench."""

from __future__ import annotations

import re
from pathlib import Path

import mne
import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix

from .app import run as run_collector
from .config import COMMANDS, DEFAULT_RECORDINGS_DIR, Settings
from .decoder import SSVEPDecoder, load_action_epochs
from .live import run_live_maze
from .quality import run_cap_check


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "ssvep"


def _choose(items: list[Path], prompt: str, *, multiple: bool) -> list[Path]:
    if not items:
        return []
    for index, item in enumerate(items, 1):
        print(f"  {index}) {item.name}")
    raw = input(prompt).strip()
    if not raw:
        return []
    try:
        indices = [int(part.strip()) for part in raw.split(",")]
    except ValueError:
        print("Enter comma-separated file numbers.")
        return []
    if not multiple and len(indices) != 1:
        print("Choose exactly one item.")
        return []
    if any(index < 1 or index > len(items) for index in indices):
        print("Selection is outside the displayed range.")
        return []
    return [items[index - 1] for index in indices]


def recordings() -> list[Path]:
    return sorted(DEFAULT_RECORDINGS_DIR.glob("*_ssvep_raw.fif"))


def models() -> list[Path]:
    return sorted(MODEL_DIR.glob("*.json"))


def _frequencies(paths: list[Path]) -> dict[str, float]:
    found: dict[str, set[float]] = {name: set() for name in COMMANDS}
    pattern = re.compile(r"^ssvep/([^/]+)/([0-9.]+)hz/action/", re.I)
    for path in paths:
        raw = mne.io.read_raw_fif(path, preload=False, verbose="ERROR")
        for description in raw.annotations.description:
            match = pattern.match(str(description))
            if match and match.group(1).lower() in found:
                found[match.group(1).lower()].add(float(match.group(2)))
    result = Settings().frequencies_hz.copy()
    for command, values in found.items():
        if len(values) > 1:
            raise ValueError(f"{command} uses inconsistent frequencies across selected files: {values}")
        if values:
            result[command] = next(iter(values))
    return result


def train_model(paths: list[Path], output: Path | None = None) -> Path:
    X, y, sfreq, channels = load_action_epochs(paths)
    decoder = SSVEPDecoder(_frequencies(paths), channel_names=channels).fit(X, y, sfreq)
    predictions = decoder.predict(X)
    print(f"Training-set check: {accuracy_score(y, predictions) * 100:.2f}% on {len(y)} trials")
    print("Highest-weight channels:")
    ranked = np.argsort(decoder.channel_weights)[::-1]
    for index in ranked[:5]:
        print(f"  {channels[index]}: {decoder.channel_weights[index]:.3f}")
    if output is None:
        subjects = "-".join(sorted({re.search(r"sub-([^_]+)", p.name).group(1)
                                    if re.search(r"sub-([^_]+)", p.name) else "unknown"
                                    for p in paths}))
        output = MODEL_DIR / f"ssvep-{subjects}.json"
    saved = decoder.save(output)
    print(f"Model saved: {saved}")
    return saved


def test_model(model_path: Path, paths: list[Path]) -> float:
    decoder = SSVEPDecoder.load(model_path)
    X, y, sfreq, channels = load_action_epochs(paths, window_s=decoder.window_s)
    if channels != decoder.channel_names:
        raise ValueError("test file channel order does not match the saved model")
    if abs(sfreq - decoder.sample_rate) > 1e-6:
        raise ValueError("test sample rate does not match the saved model")
    predicted = decoder.predict(X)
    accuracy = float(accuracy_score(y, predicted))
    matrix = confusion_matrix(y, predicted, labels=list(COMMANDS))
    print(f"Held-out test accuracy: {accuracy * 100:.2f}% on {len(y)} trials")
    print("rows=true, columns=predicted")
    print("          " + " ".join(f"{name[:4]:>5}" for name in COMMANDS))
    for name, row in zip(COMMANDS, matrix):
        print(f"{name[:8]:>8}  " + " ".join(f"{value:5d}" for value in row))
    return accuracy


def _hardware_options() -> tuple[bool, str]:
    synthetic = input("Use synthetic board? [y/N] ").strip().lower() in {"y", "yes"}
    port = "" if synthetic else input("COM port (blank = auto-detect): ").strip()
    return synthetic, port


def menu() -> None:
    while True:
        print("\nSSVEP workbench")
        print("  1) Collect labeled SSVEP training data")
        print("  2) Check cap and channel signal quality")
        print("  3) Train a four-command SSVEP model")
        print("  4) Test a saved model on held-out recordings")
        print("  5) Live cap control of the TIAGo maze")
        print("  b) Back")
        choice = input("> ").strip().lower()
        try:
            if choice in {"1", "collect"}:
                run_collector()
            elif choice in {"2", "cap", "quality", "check"}:
                synthetic, port = _hardware_options()
                seconds = float(input("Check duration in seconds [10]: ").strip() or "10")
                run_cap_check(seconds=seconds, synthetic=synthetic, serial_port=port)
            elif choice in {"3", "train"}:
                selected = _choose(recordings(), "Training files (comma-separated): ", multiple=True)
                if selected:
                    train_model(selected)
            elif choice in {"4", "test"}:
                model = _choose(models(), "Model: ", multiple=False)
                selected = _choose(recordings(), "Held-out test files (comma-separated): ", multiple=True)
                if model and selected:
                    test_model(model[0], selected)
            elif choice in {"5", "maze", "live"}:
                model = _choose(models(), "Model: ", multiple=False)
                if not model:
                    continue
                subject = input("Subject ID: ").strip() or "unknown"
                maze = int(input("Standard maze [1-4, default 1]: ").strip() or "1")
                if maze not in (1, 2, 3, 4):
                    raise ValueError("maze must be 1, 2, 3, or 4")
                synthetic, port = _hardware_options()
                if not synthetic:
                    warning = input(
                        "Flicker safety screening complete and participant ready? Type YES: "
                    ).strip()
                    if warning != "YES":
                        print("Live SSVEP maze cancelled.")
                        continue
                run_live_maze(model[0], subject=subject, maze_number=maze,
                              synthetic=synthetic, serial_port=port)
            elif choice in {"b", "back", "q", "quit"}:
                return
            else:
                print("Enter 1-5 or b.")
        except (OSError, RuntimeError, ValueError) as error:
            print(f"SSVEP operation failed: {error}")
