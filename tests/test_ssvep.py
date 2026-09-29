from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json

import mne
import numpy as np
import pytest

from ssvep.acquisition import CapturedData, PhaseLog, save_session
from ssvep.config import COMMANDS, ConfigurationError, EEG_CHANNEL_NAMES, Settings
from ssvep.protocol import decode_marker, encode_marker, make_trials, marker_description
from ssvep.decoder import SSVEPDecoder
from ssvep.quality import assess_channels


def test_trials_are_balanced_inside_every_randomized_block():
    settings = Settings(repetitions_per_command=8)
    trials = make_trials(8, settings.frequencies_hz, seed=31)

    assert len(trials) == 32
    assert Counter(trial.command for trial in trials) == Counter({name: 8 for name in COMMANDS})
    for start in range(0, len(trials), 4):
        assert {trial.command for trial in trials[start : start + 4]} == set(COMMANDS)
    assert [trial.number for trial in trials] == list(range(1, 33))


def test_marker_round_trip_and_human_readable_description():
    value = encode_marker("backward", "action", 42)
    assert decode_marker(value) == {
        "command": "backward",
        "phase": "action",
        "trial_number": 42,
    }
    assert marker_description(value, Settings().frequencies_hz) == "ssvep/backward/12Hz/action/t042"


def test_settings_reject_duplicate_frequencies():
    settings = Settings()
    settings.frequencies_hz["left"] = settings.frequencies_hz["right"]
    with pytest.raises(ConfigurationError, match="must be different"):
        settings.validate()


def test_save_session_writes_15_eeg_channels_and_labels(tmp_path):
    sample_rate = 125.0
    n_samples = 250
    markers = np.zeros(n_samples)
    markers[10] = encode_marker(None, "preparation", 0)
    markers[60] = encode_marker("forward", "planning", 1)
    markers[100] = encode_marker("forward", "action", 1)
    markers[220] = encode_marker("forward", "rest", 1)
    captured = CapturedData(
        eeg_uv=np.arange(15 * n_samples, dtype=float).reshape(15, n_samples) / 100,
        acceleration=np.zeros((3, n_samples)),
        markers=markers,
        timestamps=np.linspace(1_788_000_000, 1_788_000_002, n_samples),
        sample_rate=sample_rate,
        board_name="test board",
    )
    settings = Settings(
        subject="unit",
        session="01",
        repetitions_per_command=1,
        output_dir=str(tmp_path),
    )
    now = datetime.now(timezone.utc).isoformat()
    logs = [
        PhaseLog(0, 0, "preparation", None, None, 0.4, 0.0, now, 0.4),
        PhaseLog(1, 1, "planning", "forward", 8.0, 0.3, 0.4, now, 0.3),
        PhaseLog(
            1,
            1,
            "action",
            "forward",
            8.0,
            0.96,
            0.7,
            now,
            0.96,
            60,
            {name: settings.frequencies_hz[name] for name in COMMANDS},
        ),
        PhaseLog(1, 1, "rest", "forward", 8.0, 0.24, 1.66, now, 0.24),
    ]

    paths = save_session(captured, settings, logs, aborted=False)

    raw = mne.io.read_raw_fif(paths["fif"], preload=True, verbose="ERROR")
    assert raw.ch_names[:15] == list(EEG_CHANNEL_NAMES)
    assert raw.get_channel_types()[:15] == ["eeg"] * 15
    assert raw.ch_names[-1] == "STI 014"
    assert raw.get_channel_types()[-1] == "stim"
    assert "ssvep/forward/8Hz/action/t001" in set(raw.annotations.description)
    assert raw.get_data(picks=["Cz"])[0, 1] == pytest.approx(captured.eeg_uv[0, 1] / 1e6)

    metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    assert metadata["sample_rate_hz"] == 125.0
    assert metadata["eeg_channel_names"] == list(EEG_CHANNEL_NAMES)
    assert metadata["aborted"] is False
    assert len(metadata["markers"]) == 4
    assert paths["events"].read_text(encoding="utf-8").count("\n") == 5


def test_ssvep_decoder_learns_and_round_trips(tmp_path):
    settings = Settings()
    sfreq = 125.0
    samples = int(2 * sfreq)
    time = np.arange(samples) / sfreq
    rng = np.random.default_rng(14)
    X, y = [], []
    for command in COMMANDS:
        frequency = settings.frequencies_hz[command]
        for _ in range(6):
            epoch = rng.normal(0, 0.25, (len(EEG_CHANNEL_NAMES), samples))
            epoch += np.sin(2 * np.pi * frequency * time)[None, :]
            epoch += 0.4 * np.sin(2 * np.pi * 2 * frequency * time)[None, :]
            X.append(epoch)
            y.append(command)
    decoder = SSVEPDecoder(settings.frequencies_hz.copy()).fit(np.asarray(X), np.asarray(y), sfreq)
    assert np.mean(decoder.predict(np.asarray(X)) == np.asarray(y)) == pytest.approx(1.0)

    path = decoder.save(tmp_path / "model.json")
    loaded = SSVEPDecoder.load(path)
    assert np.array_equal(loaded.predict(np.asarray(X)), np.asarray(y))


def test_cap_quality_flags_flat_and_railed_channels():
    sfreq = 125.0
    time = np.arange(500) / sfreq
    data = np.vstack([
        12 * np.sin(2 * np.pi * (8 + index / 10) * time)
        for index in range(len(EEG_CHANNEL_NAMES))
    ])
    data[0] = 0
    data[1] += 200_000
    report = assess_channels(data, sfreq)
    assert report[0].status == "BAD"
    assert report[0].reason == "flat or disconnected"
    assert report[1].status == "BAD"
    assert report[1].reason == "railed or saturated"
    assert any(row.status == "GOOD" for row in report[2:])
