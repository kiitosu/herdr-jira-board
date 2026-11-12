"""Due dates order the cards inside a status group, nearest first."""

import board
from board import BoardPriorityRule, PhaseLabel


def issue(key, duedate=None, labels=(), epic_name=None, status="To Do"):
    return board.Issue(key=key, summary="", status=status, category="new",
                       issuetype="Task", duedate=duedate, labels=list(labels),
                       epic_name=epic_name)


def cfg_with(board_priority=(), phase_labels=()):
    return board.Config(site="s", email="e", api_token="t", jql="q",
                        board_priority=list(board_priority),
                        phase_labels=list(phase_labels))


def test_the_nearest_due_date_comes_first():
    issues = [issue("X-1", "2026-12-01"), issue("X-2", "2026-11-14"),
              issue("X-3", "2026-11-30")]
    assert [i.key for i in board.sort_cards(issues, cfg_with())] == [
        "X-2", "X-3", "X-1"]


def test_cards_without_a_due_date_go_last_and_keep_the_jql_order():
    issues = [issue("X-1"), issue("X-2", "2026-11-30"), issue("X-3")]
    assert [i.key for i in board.sort_cards(issues, cfg_with())] == [
        "X-2", "X-1", "X-3"]


def test_board_priority_outranks_the_due_date():
    cfg = cfg_with([BoardPriorityRule(epic="STテスト")])
    issues = [issue("X-1", "2026-11-14"), issue("X-2", "2026-12-01", epic_name="STテスト")]
    assert [i.key for i in board.sort_cards(issues, cfg)] == ["X-2", "X-1"]


def test_phase_labels_outrank_the_due_date():
    verify = PhaseLabel("jb_効果確認中", "効果確認中")
    cfg = cfg_with(phase_labels=[verify])
    issues = [issue("X-1", "2026-11-14"),
              issue("X-2", "2026-12-01", labels=["jb_効果確認中"])]
    assert [i.key for i in board.sort_cards(issues, cfg)] == ["X-2", "X-1"]
