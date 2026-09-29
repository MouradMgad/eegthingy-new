from __future__ import annotations

import json

import mne
import numpy as np

import utils.session_audit as session_audit


def test_diagnostic_session_recovers_incremental_eeg_and_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(session_audit, "SESSIONS", tmp_path)
    session = session_audit.DiagnosticSession("unit_test")
    output = session.path
    with session:
        session.start_eeg(16, 125.0)
        session.append_eeg(np.zeros((16, 25), dtype=np.float32))
        session.decision(("now", 0.1, 0.0, 0.1, 1, 0.9, 0))

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["raw_complete_samples"] == 25
    assert (output / "console.log").exists()
    assert (output / "events.jsonl").exists()
    assert (output / "decisions.csv").read_text(encoding="utf-8").count("\n") == 2
    raw = mne.io.read_raw_fif(output / "raw_eeg_recovered_raw.fif", preload=False, verbose="ERROR")
    assert raw.n_times == 25
    assert raw.info["sfreq"] == 125.0
