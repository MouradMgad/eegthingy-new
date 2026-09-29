"""Leakage-safe comparison of every live decoder on unseen Subject 10.

The default protocol is intentionally fixed:

* training: every validated recording from S01,S03,S04,S05,S06,S07,S08;
* unlabeled live-style calibration: S10 run 5;
* final evaluation: S10 runs 6, 7, and 8.

Subject 10 labels are never used for fitting, validation, checkpoint selection,
alignment, or decision-boundary calibration.  Both strict zero-shot and the
project's live-style unlabeled calibration result are reported on the same test
epochs.
"""

from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import joblib
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "classifier"), str(ROOT)]

import deep  # noqa: E402
import mirepnet  # noqa: E402
import run as live  # noqa: E402
from config import (DEEP_DEVICE, DEEP_EPOCHS, DEEP_SEED, LOCAL_EXP4_VALID_RUNS,
                    MIREPNET_DEVICE, MIREPNET_EPOCHS, MIREPNET_SEED,
                    MIREPNET_WINDOW_MODE, TARGET_MAPPINGS)  # noqa: E402


MODELS = ("ea", "riemann", "deep_compactdyn", "deep_sinc", "mirepnet")
TARGET_SUBJECT = 10
CALIBRATION_RUN = 5
TEST_RUNS = (6, 7, 8)


def subject_run(path: Path) -> tuple[int, int]:
    match = re.search(r"subject(\d+)_training_(\d+)_mi_raw", path.name)
    if not match:
        raise ValueError(f"cannot identify subject/run from {path.name}")
    return int(match.group(1)), int(match.group(2))


def metric_block(y_true, prediction, probabilities, classes):
    labels = [int(value) for value in classes]
    prediction = np.asarray(prediction)
    probabilities = np.asarray(probabilities)
    return {
        "n_trials": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, prediction)),
        "confusion_matrix": confusion_matrix(y_true, prediction, labels=labels).tolist(),
        "labels": labels,
        "mean_confidence": float(np.max(probabilities, axis=1).mean()),
    }


def load_parts(kind, files):
    loader = (deep.process_data if kind.startswith("deep_") else
              mirepnet.process_data if kind == "mirepnet" else live.process_data)
    parts = []
    started = time.perf_counter()
    for path in files:
        if kind == "mirepnet":
            parts.append(loader(str(path), TARGET_MAPPINGS, MIREPNET_WINDOW_MODE))
        else:
            parts.append(loader(str(path), TARGET_MAPPINGS))
    return parts, time.perf_counter() - started


def concatenate(parts):
    return (np.concatenate([part[0] for part in parts]),
            np.concatenate([part[1] for part in parts]))


def make_model(kind, args):
    if kind == "ea":
        return live.EAFilterBankCSP(n_components=2)
    if kind == "riemann":
        return live.FilterBankTangentSpace()
    if kind.startswith("deep_"):
        return deep.DeepDecoder(kind, epochs=args.deep_epochs, seed=DEEP_SEED,
                                device=args.device)
    return mirepnet.MIRepNetDecoder(
        epochs=args.mirepnet_epochs, seed=MIREPNET_SEED, device=args.device,
        window_mode=MIREPNET_WINDOW_MODE,
    )


def training_tag(files):
    names = sorted(path.name for path in files)
    digest = hashlib.sha1("|".join(names).encode()).hexdigest()[:8]
    subjects = sorted({subject_run(path)[0] for path in files})
    return f"{len(subjects)}subjects_{len(files)}runs_{digest}"


def score_model(kind, model, X_cal, X_test, y_test):
    training_reference = copy.deepcopy(getattr(model, "ref_white_", None))

    strict_started = time.perf_counter()
    strict_prob = model.predict_proba(X_test)
    strict_prediction = model.classes_[strict_prob.argmax(axis=1)]
    strict_seconds = time.perf_counter() - strict_started

    # Recreate online startup without reading a single S10 label: covariance
    # alignment where supported, followed by the median log-odds boundary seed.
    if training_reference is not None:
        model.ref_white_ = copy.deepcopy(training_reference)
    if kind in {"ea", "riemann", "mirepnet"}:
        model.set_reference(X_cal)
    cal_prob = model.predict_proba(X_cal)
    cal_score = np.log(np.clip(cal_prob[:, 1], 1e-12, 1.0)
                       / np.clip(cal_prob[:, 0], 1e-12, 1.0))
    center = float(np.median(cal_score))

    adapted_started = time.perf_counter()
    adapted_prob = model.predict_proba(X_test)
    adapted_score = np.log(np.clip(adapted_prob[:, 1], 1e-12, 1.0)
                           / np.clip(adapted_prob[:, 0], 1e-12, 1.0)) - center
    adapted_prediction = np.where(adapted_score >= 0.0,
                                  model.classes_[1], model.classes_[0])
    adapted_seconds = time.perf_counter() - adapted_started

    return {
        "strict_zero_shot": metric_block(
            y_test, strict_prediction, strict_prob, model.classes_),
        "unlabeled_live_calibration": metric_block(
            y_test, adapted_prediction, adapted_prob, model.classes_),
        "calibration_boundary_log_odds": center,
        "strict_inference_seconds": strict_seconds,
        "adapted_inference_seconds": adapted_seconds,
    }


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--device", default=DEEP_DEVICE if DEEP_DEVICE != "auto" else MIREPNET_DEVICE)
    ap.add_argument("--deep-epochs", type=int, default=DEEP_EPOCHS)
    ap.add_argument("--mirepnet-epochs", type=int, default=MIREPNET_EPOCHS)
    ap.add_argument("--output", type=Path, default=None)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    all_files = sorted(args.data_dir.glob("exp4_subject*_training_*_mi_raw.fif"))
    indexed = {subject_run(path): path for path in all_files}
    train_subjects = sorted(subject for subject in LOCAL_EXP4_VALID_RUNS
                            if subject != TARGET_SUBJECT)
    train_files = [indexed[(subject, run)] for subject in train_subjects
                   for run in LOCAL_EXP4_VALID_RUNS[subject]]
    calibration_files = [indexed[(TARGET_SUBJECT, CALIBRATION_RUN)]]
    test_files = [indexed[(TARGET_SUBJECT, run)] for run in TEST_RUNS]

    assert all(subject_run(path)[0] != TARGET_SUBJECT for path in train_files)
    assert not set(train_files) & set(calibration_files + test_files)

    output = args.output or (
        ROOT / "results" / "unseen-subject10" /
        f"all-models_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "format": "unseen-subject-all-live-decoders-v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "target_subject": TARGET_SUBJECT,
        "training_subjects": train_subjects,
        "training_files": [path.name for path in train_files],
        "calibration_files": [path.name for path in calibration_files],
        "test_files": [path.name for path in test_files],
        "leakage_check": "passed: no Subject 10 file in training or validation",
        "calibration_contract": "unlabeled EEG only; labels never passed to model",
        "models": {},
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    for number, kind in enumerate(args.models, 1):
        print(f"\n{'=' * 78}\n[{number}/{len(args.models)}] {kind}\n{'=' * 78}")
        started = time.perf_counter()
        train_parts, train_preprocess = load_parts(kind, train_files)
        cal_parts, calibration_preprocess = load_parts(kind, calibration_files)
        test_parts, test_preprocess = load_parts(kind, test_files)
        X_train, y_train = concatenate(train_parts)
        X_cal, _ignored_calibration_labels = concatenate(cal_parts)
        X_test, y_test = concatenate(test_parts)
        groups = np.concatenate([
            np.full(len(labels), index, dtype=int)
            for index, (_, labels) in enumerate(train_parts)
        ])

        print(f"Training: {len(y_train)} trials from {len(train_files)} recordings; "
              f"S10 present: {any(subject_run(path)[0] == 10 for path in train_files)}")
        print(f"Unlabeled calibration: {len(X_cal)} trials; final test: {len(y_test)} trials")
        model = make_model(kind, args)
        fit_started = time.perf_counter()
        model.fit(X_train, y_train, groups=groups)
        fit_seconds = time.perf_counter() - fit_started
        checkpoint = None
        if kind.startswith("deep_") or kind == "mirepnet":
            checkpoint = str(Path(model.save(sources=[str(path) for path in train_files])).resolve())
        else:
            checkpoint_path = (ROOT / "models" / "classical" /
                               f"{kind}__{training_tag(train_files)}.joblib")
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump({
                "format": "eeg-online-classical-v1",
                "kind": kind,
                "model": model,
                "trained_on": [path.name for path in train_files],
                "n_times": int(X_train.shape[-1]),
                "sample_rate_hz": 125.0,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }, checkpoint_path)
            checkpoint = str(checkpoint_path.resolve())

        result = score_model(kind, model, X_cal, X_test, y_test)
        result.update({
            "parameters": int(getattr(model, "n_parameters_", 0)) or None,
            "checkpoint": checkpoint,
            "training_trials": int(len(y_train)),
            "calibration_trials": int(len(X_cal)),
            "test_trials": int(len(y_test)),
            "preprocessing_seconds": {
                "train": train_preprocess,
                "calibration": calibration_preprocess,
                "test": test_preprocess,
            },
            "fit_seconds": fit_seconds,
            "total_seconds": time.perf_counter() - started,
        })
        report["models"][kind] = result
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        strict = result["strict_zero_shot"]
        adapted = result["unlabeled_live_calibration"]
        print(f"RESULT {kind}: strict BA={strict['balanced_accuracy']:.4f}, "
              f"live-style BA={adapted['balanced_accuracy']:.4f}, fit={fit_seconds:.1f}s")

        del model, train_parts, cal_parts, test_parts, X_train, X_cal, X_test
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    print(f"\nSaved leakage-safe comparison: {output}")


if __name__ == "__main__":
    main()
