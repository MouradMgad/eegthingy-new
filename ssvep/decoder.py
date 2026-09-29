"""Trainable frequency-domain decoder for the four-command SSVEP protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import mne
import numpy as np

from .config import COMMANDS, EEG_CHANNEL_NAMES, Settings


MODEL_FORMAT = "eegthingy-ssvep-spectral-v1"


def _action_command(description: str) -> str | None:
    parts = str(description).strip().lower().split("/")
    if len(parts) >= 4 and parts[0] == "ssvep" and parts[1] in COMMANDS and parts[3] == "action":
        return parts[1]
    return None


def load_action_epochs(
    paths: Iterable[str | Path], *, window_s: float = 2.0, offset_s: float = 0.5
) -> tuple[np.ndarray, np.ndarray, float, tuple[str, ...]]:
    """Load fixed action windows from one or more collector FIF files."""
    epochs: list[np.ndarray] = []
    labels: list[str] = []
    expected_rate: float | None = None
    expected_channels: tuple[str, ...] | None = None
    for raw_path in paths:
        raw = mne.io.read_raw_fif(raw_path, preload=True, verbose="ERROR")
        picks = [name for name in EEG_CHANNEL_NAMES if name in raw.ch_names]
        if len(picks) != len(EEG_CHANNEL_NAMES):
            missing = sorted(set(EEG_CHANNEL_NAMES) - set(picks))
            raise ValueError(f"{raw_path} is missing SSVEP channels: {missing}")
        raw.pick(picks)
        rate = float(raw.info["sfreq"])
        channels = tuple(raw.ch_names)
        if expected_rate is not None and abs(rate - expected_rate) > 1e-6:
            raise ValueError("All SSVEP files must have the same sample rate")
        if expected_channels is not None and channels != expected_channels:
            raise ValueError("All SSVEP files must use the same channel order")
        expected_rate, expected_channels = rate, channels
        n = int(round(window_s * rate))
        for annotation in raw.annotations:
            command = _action_command(annotation["description"])
            if command is None:
                continue
            start = int(round((float(annotation["onset"]) + offset_s) * rate))
            stop = start + n
            if stop <= raw.n_times:
                epochs.append(raw.get_data(start=start, stop=stop))
                labels.append(command)
    if not epochs:
        raise ValueError("No complete ssvep/<command>/<frequency>/action annotations were found")
    return (
        np.asarray(epochs, dtype=np.float64),
        np.asarray(labels),
        float(expected_rate),
        tuple(expected_channels or ()),
    )


def _spectral_scores(epoch: np.ndarray, sfreq: float, frequencies: np.ndarray) -> np.ndarray:
    """Return log spectral SNR for every channel and candidate frequency."""
    x = np.asarray(epoch, dtype=np.float64)
    x = x - x.mean(axis=-1, keepdims=True)
    n = x.shape[-1]
    window = np.hanning(n)
    spectrum = np.abs(np.fft.rfft(x * window, axis=-1)) ** 2
    bins = np.fft.rfftfreq(n, 1.0 / sfreq)
    out = np.zeros((x.shape[0], len(frequencies)), dtype=np.float64)
    for fi, fundamental in enumerate(frequencies):
        harmonic_scores = []
        harmonic_weights = []
        for harmonic in (1, 2, 3):
            target = float(fundamental) * harmonic
            if target >= sfreq / 2 - 1:
                continue
            center = int(np.argmin(np.abs(bins - target)))
            signal = spectrum[:, max(0, center - 1):center + 2].sum(axis=1)
            lo, hi = max(0, center - 5), min(spectrum.shape[1], center + 6)
            noise_idx = [i for i in range(lo, hi) if abs(i - center) > 1]
            noise = np.median(spectrum[:, noise_idx], axis=1) * 3.0 if noise_idx else 1.0
            harmonic_scores.append(np.log((signal + 1e-18) / (noise + 1e-18)))
            harmonic_weights.append(1.0 / harmonic)
        out[:, fi] = (np.average(harmonic_scores, axis=0, weights=harmonic_weights)
                      if harmonic_scores else -100.0)
    return out


@dataclass
class SSVEPDecoder:
    frequencies_hz: dict[str, float]
    channel_names: tuple[str, ...] = EEG_CHANNEL_NAMES
    channel_weights: np.ndarray | None = None
    sample_rate: float = 125.0
    window_s: float = 2.0

    def fit(self, X: np.ndarray, y: np.ndarray, sfreq: float) -> "SSVEPDecoder":
        X, y = np.asarray(X), np.asarray(y)
        if X.ndim != 3 or X.shape[0] != len(y):
            raise ValueError("fit expects X=(epochs, channels, samples) and one label per epoch")
        if X.shape[1] != len(self.channel_names):
            raise ValueError("epoch channel count does not match channel_names")
        order = np.asarray([self.frequencies_hz[name] for name in COMMANDS], dtype=float)
        reliability = np.zeros(X.shape[1], dtype=np.float64)
        for epoch, label in zip(X, y):
            scores = _spectral_scores(epoch, sfreq, order)
            correct = COMMANDS.index(str(label))
            other = np.delete(scores, correct, axis=1).mean(axis=1)
            reliability += scores[:, correct] - other
        reliability /= max(1, len(X))
        reliability = np.clip(reliability - np.percentile(reliability, 20), 0.0, None)
        if not np.any(reliability > 0):
            reliability[:] = 1.0
        self.channel_weights = reliability / reliability.sum()
        self.sample_rate = float(sfreq)
        self.window_s = X.shape[-1] / float(sfreq)
        return self

    def decision_function(self, X: np.ndarray, *, allowed: Iterable[str] | None = None) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 2:
            X = X[np.newaxis]
        candidates = tuple(allowed or COMMANDS)
        if any(name not in COMMANDS for name in candidates):
            raise ValueError("unknown SSVEP command in allowed set")
        weights = self.channel_weights
        if weights is None:
            weights = np.full(X.shape[1], 1.0 / X.shape[1])
        freqs = np.asarray([self.frequencies_hz[name] for name in candidates], dtype=float)
        return np.asarray([weights @ _spectral_scores(epoch, self.sample_rate, freqs) for epoch in X])

    def predict_proba(self, X: np.ndarray, *, allowed: Iterable[str] | None = None) -> np.ndarray:
        scores = self.decision_function(X, allowed=allowed)
        scores = scores - scores.max(axis=1, keepdims=True)
        exp = np.exp(scores)
        return exp / exp.sum(axis=1, keepdims=True)

    def predict(self, X: np.ndarray, *, allowed: Iterable[str] | None = None) -> np.ndarray:
        candidates = tuple(allowed or COMMANDS)
        return np.asarray(candidates)[np.argmax(self.decision_function(X, allowed=candidates), axis=1)]

    def save(self, path: str | Path) -> Path:
        path = Path(path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": MODEL_FORMAT,
            "frequencies_hz": self.frequencies_hz,
            "channel_names": list(self.channel_names),
            "channel_weights": (self.channel_weights.tolist() if self.channel_weights is not None else None),
            "sample_rate": self.sample_rate,
            "window_s": self.window_s,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "SSVEPDecoder":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("format") != MODEL_FORMAT:
            raise ValueError("not an eegthingy SSVEP model")
        weights = payload.get("channel_weights")
        return cls(
            frequencies_hz={k: float(v) for k, v in payload["frequencies_hz"].items()},
            channel_names=tuple(payload["channel_names"]),
            channel_weights=None if weights is None else np.asarray(weights, dtype=float),
            sample_rate=float(payload["sample_rate"]),
            window_s=float(payload["window_s"]),
        )


def default_decoder() -> SSVEPDecoder:
    return SSVEPDecoder(Settings().frequencies_hz.copy())
