"""Corner-level scoring for live and replayed maze sessions.

The raw decision log intentionally keeps every physical turn.  Accuracy,
however, is based on one EEG/model decision per planned corner.  Any later
turn at an already-seen corner is a recovery movement and must not become a
second trial.
"""

from __future__ import annotations

from collections import defaultdict


def annotate_corner_attempts(records: list[dict]) -> list[dict]:
    """Return copied rows with explicit audit/scoring annotations.

    The first non-recovery attempt at each numbered corner is scored.  Later
    attempts at that corner are retained, labelled as recovery, and excluded
    from the accuracy denominator.  This also repairs legacy live logs where
    subject correction movements were incorrectly recorded as ``model``.
    """
    seen: set[int | str] = set()
    attempt_counts: defaultdict[int | str, int] = defaultdict(int)
    annotated: list[dict] = []

    for original in records:
        row = dict(original)
        raw_turn = row.get("turn", "")
        try:
            turn: int | str = int(float(raw_turn))
        except (TypeError, ValueError):
            turn = str(raw_turn).strip()

        if turn != "":
            attempt_counts[turn] += 1
        attempt = attempt_counts[turn] if turn != "" else 1
        explicit_recovery = str(row.get("source", "")).lower() == "recovery"
        scored = turn != "" and turn not in seen and not explicit_recovery

        if scored:
            seen.add(turn)
            role = "initial decision"
        elif explicit_recovery:
            role = "automatic recovery"
        else:
            role = "subject correction"

        row["corner_attempt"] = attempt
        row["attempt_role"] = role
        row["scored"] = scored
        annotated.append(row)

    return annotated


def summarize_corner_score(records: list[dict]) -> dict:
    """Compute paper metrics from unique initial corner decisions."""
    annotated = annotate_corner_attempts(records)
    scored = [row for row in annotated if row["scored"]]
    correct = sum(str(row.get("result", "")).lower() == "correct" for row in scored)
    wrong = len(scored) - correct
    return {
        "records": annotated,
        "scored_records": scored,
        "decisions": len(scored),
        "correct": correct,
        "wrong": wrong,
        "accuracy": correct / len(scored) if scored else 0.0,
        "recovery_attempts": len(annotated) - len(scored),
    }
