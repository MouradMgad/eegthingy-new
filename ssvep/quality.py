"""Cap and electrode signal-quality checks for the SSVEP headset."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import numpy as np
from scipy.signal import welch

from .acquisition import OpenBCIRecorder
from .config import EEG_CHANNEL_NAMES


@dataclass(frozen=True)
class ChannelQuality:
    channel: str
    status: str
    reason: str
    rms_uv: float
    peak_to_peak_uv: float
    line_noise_ratio: float


def assess_channels(eeg_uv: np.ndarray, sfreq: float, *, mains_hz: float = 60.0) -> list[ChannelQuality]:
    """Classify every channel as GOOD, CHECK, or BAD using transparent rules."""
    eeg_uv = np.asarray(eeg_uv, dtype=np.float64)
    if eeg_uv.ndim != 2 or eeg_uv.shape[0] != len(EEG_CHANNEL_NAMES):
        raise ValueError(f"expected ({len(EEG_CHANNEL_NAMES)}, samples) EEG array")
    baseline = np.median(eeg_uv, axis=1)
    centered = eeg_uv - baseline[:, np.newaxis]
    rms = np.sqrt(np.mean(centered ** 2, axis=1))
    p2p = np.ptp(eeg_uv, axis=1)
    nperseg = min(eeg_uv.shape[1], max(64, int(round(sfreq * 2))))
    freqs, psd = welch(centered, fs=sfreq, nperseg=nperseg, axis=1)
    brain_band = (freqs >= 1.0) & (freqs <= min(40.0, sfreq / 2 - 1))
    line_band = np.abs(freqs - mains_hz) <= 1.0
    if not np.any(line_band):
        # At 125 Hz, 60 Hz is representable; this fallback also supports lower-rate tests.
        line_band = np.abs(freqs - min(mains_hz, sfreq / 2 - 1)) <= 1.0
    broad = np.trapezoid(psd[:, brain_band], freqs[brain_band], axis=1) + 1e-12
    line = np.trapezoid(psd[:, line_band], freqs[line_band], axis=1) if np.any(line_band) else np.zeros(len(rms))
    ratio = line / broad

    results: list[ChannelQuality] = []
    for index, name in enumerate(EEG_CHANNEL_NAMES):
        if not np.isfinite(rms[index]) or rms[index] < 0.25 or p2p[index] < 1.0:
            status, reason = "BAD", "flat or disconnected"
        elif abs(baseline[index]) > 150000 or rms[index] > 5000 or p2p[index] > 20000:
            status, reason = "BAD", "railed or saturated"
        elif rms[index] > 150 or p2p[index] > 1000:
            status, reason = "CHECK", "large artifacts / poor contact"
        elif ratio[index] > 0.35:
            status, reason = "CHECK", f"strong {mains_hz:g} Hz mains noise"
        else:
            status, reason = "GOOD", "usable signal"
        results.append(ChannelQuality(name, status, reason, float(rms[index]),
                                      float(p2p[index]), float(ratio[index])))
    return results


def print_report(results: list[ChannelQuality]) -> None:
    print("\nChannel  Status    RMS uV    P-P uV   Line ratio  Assessment")
    print("-" * 75)
    for row in results:
        print(f"{row.channel:<7}  {row.status:<6}  {row.rms_uv:8.2f}  "
              f"{row.peak_to_peak_uv:8.2f}  {row.line_noise_ratio:10.3f}  {row.reason}")
    bad = [row.channel for row in results if row.status == "BAD"]
    check = [row.channel for row in results if row.status == "CHECK"]
    print()
    if bad:
        print("BAD channels:", ", ".join(bad))
    if check:
        print("Channels to check:", ", ".join(check))
    if not bad and not check:
        print("All 15 connected channels have plausible signal quality.")
    print("This is a signal sanity check, not an electrode-impedance measurement.")


def run_cap_check(*, seconds: float = 10.0, synthetic: bool = False, serial_port: str = "") -> list[ChannelQuality]:
    recorder = OpenBCIRecorder(synthetic=synthetic, serial_port=serial_port).connect()
    try:
        recorder.begin()
        print(f"Recording {seconds:g} seconds. Sit still, relax your jaw, and keep eyes open...")
        time.sleep(seconds)
        captured = recorder.finish()
    finally:
        recorder.close()
    results = assess_channels(captured.eeg_uv, captured.sample_rate)
    print_report(results)
    return results
