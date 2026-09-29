from tiago_maze.scoring import annotate_corner_attempts, summarize_corner_score


def test_duplicate_corner_attempts_are_unscored_recovery_rows():
    rows = [
        {"turn": 1, "result": "correct", "source": "model"},
        {"turn": 2, "result": "wrong", "source": "model"},
        {"turn": 2, "result": "wrong", "source": "model"},
        {"turn": 2, "result": "correct", "source": "model"},
        {"turn": 3, "result": "correct", "source": "model"},
    ]
    score = summarize_corner_score(rows)
    assert score["decisions"] == 3
    assert score["correct"] == 2
    assert score["wrong"] == 1
    assert score["accuracy"] == 2 / 3
    assert score["recovery_attempts"] == 2
    assert [row["attempt_role"] for row in score["records"]] == [
        "initial decision", "initial decision", "subject correction",
        "subject correction", "initial decision",
    ]


def test_explicit_automatic_recovery_is_never_scored():
    rows = [
        {"turn": 1, "result": "wrong", "source": "model"},
        {"turn": 1, "result": "recovery", "source": "recovery"},
        {"turn": 2, "result": "correct", "source": "model"},
    ]
    annotated = annotate_corner_attempts(rows)
    assert [row["scored"] for row in annotated] == [True, False, True]
    assert annotated[1]["attempt_role"] == "automatic recovery"
