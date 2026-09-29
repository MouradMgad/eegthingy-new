from __future__ import annotations

from datetime import datetime, timedelta

from utils.paper_results import build_summary_payload, create_session_folder, read_maze_report


def test_paper_session_folder_is_named_and_never_overwritten(tmp_path):
    first = create_session_folder("10", "run A", root=tmp_path)
    second = create_session_folder(
        "10", "run A", root=tmp_path, when=datetime(2026, 9, 26, 15, 0, 0)
    )
    assert first.name == "subject_10_test_run-A"
    assert second.name == "subject_10_test_run-A_20260926_150000"
    assert first != second


def test_report_becomes_typed_corner_summary(tmp_path):
    report = tmp_path / "SUBJECT10_TEST1_SEED11_20260926_150000.csv"
    report.write_text(
        "field,value\n"
        "seed,11\ncompleted,True\ndecisions,2\ncorrect,1\naccuracy,0.5\n"
        "wrong_turns,1\ntotal_time_s,31.5\n\n"
        "decision,turn,corner_x,corner_y,intended_dir,chosen_dir,result,wait_s,"
        "sim_time_s,robot_x,robot_y,robot_yaw_deg,front_m,source,epoch,file,"
        "epoch_in_file,true_label,predicted_label,confidence\n"
        "1,1,6.4,0,LEFT,LEFT,correct,2.1,4.0,6,0,0,0.7,model,1,a.fif,2,1,1,0.9\n"
        "2,2,6.4,3.2,RIGHT,LEFT,wrong,3.2,8.0,6,3,90,0.6,model,2,b.fif,3,2,1,0.6\n",
        encoding="utf-8",
    )
    meta, corners = read_maze_report(report)
    assert meta["accuracy"] == "0.5"
    assert [row["result"] for row in corners] == ["correct", "wrong"]

    started = datetime(2026, 9, 26, 15, 0, 0)
    payload = build_summary_payload(
        tmp_path, "10", "1", 1, 11, "MIRepNet", started,
        started + timedelta(seconds=40), report_path=report,
    )
    assert payload["session"]["accuracy"] == 0.5
    assert payload["session"]["wall_duration_s"] == 40.0
    assert payload["corners"][0]["corner_x"] == 6.4
    assert payload["corners"][1]["turn"] == 2
    assert payload["corners"][0]["scored"] is True
    assert payload["session"]["decisions"] == 2


def test_recovery_attempts_are_retained_but_not_scored(tmp_path):
    report = tmp_path / "legacy_live.csv"
    report.write_text(
        "field,value\ncompleted,True\ndecisions,5\ncorrect,3\naccuracy,0.6\n"
        "wrong_turns,2\ntotal_time_s,20\n\n"
        "decision,turn,intended_dir,chosen_dir,result,source\n"
        "1,1,LEFT,LEFT,correct,model\n"
        "2,2,RIGHT,LEFT,wrong,model\n"
        "3,2,RIGHT,LEFT,wrong,model\n"
        "4,2,RIGHT,RIGHT,correct,model\n"
        "5,3,LEFT,LEFT,correct,model\n",
        encoding="utf-8",
    )
    started = datetime(2026, 9, 26, 15, 0, 0)
    payload = build_summary_payload(
        tmp_path, "10", "1", 1, 11, "MIRepNet", started,
        started + timedelta(seconds=20), report_path=report,
    )
    assert payload["session"]["decisions"] == 3
    assert payload["session"]["correct"] == 2
    assert payload["session"]["wrong"] == 1
    assert payload["session"]["accuracy"] == 0.666667
    assert payload["session"]["recovery_attempts"] == 2
    assert [row["scored"] for row in payload["corners"]] == [True, True, False, False, True]
