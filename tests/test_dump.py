import json
from datetime import date

import board


CFG = board.Config(site="https://example.atlassian.net", email="you@example.com",
                   api_token="t", jql="jql", exclude_statuses=["Resolved"])

ISSUES = [
    board.Issue(key="X-1", summary="first", status="To Do", category="new",
                issuetype="Task", created="2026-08-01", duedate="2026-08-20"),
    board.Issue(key="X-2", summary="second", status="進行中", category="indeterminate",
                issuetype="バグ", created="2026-08-02"),
    board.Issue(key="X-3", summary="third", status="完了", category="done",
                issuetype="Task", created="2026-08-03"),
]

# X-2 has a session; its pane is reported as working.
SESSIONS = {"X-2": "7"}
STATUSES = {"7": "working"}


def test_text_groups_issues_into_the_three_columns():
    out = board.dump_text(CFG, ISSUES, {}, {})
    todo, progress, done = out.index("== To Do"), out.index("== In Progress"), out.index("== Done")
    assert todo < progress < done
    assert todo < out.index("X-1") < progress
    assert progress < out.index("X-2") < done
    assert done < out.index("X-3")


def test_text_reports_counts_query_and_exclusions():
    out = board.dump_text(CFG, ISSUES, {}, {})
    assert "JQL: jql" in out
    assert "exclude_statuses: ['Resolved']" in out
    assert "== To Do (1) ==" in out


def test_text_includes_dates_type_summary_and_url():
    line = next(l for l in board.dump_text(CFG, ISSUES, {}, {}).splitlines() if "X-1 " in l)
    assert "[To Do]" in line
    assert "Task" in line
    assert "2026-08-01" in line and "2026-08-20" in line
    assert line.endswith("first")
    assert "https://example.atlassian.net/browse/X-1" in board.dump_text(CFG, ISSUES, {}, {})


def test_text_marks_missing_due_date():
    line = next(l for l in board.dump_text(CFG, ISSUES, {}, {}).splitlines() if "X-2 " in l)
    assert "-)" in line


def test_text_badges_only_issues_with_a_session():
    out = board.dump_text(CFG, ISSUES, STATUSES, SESSIONS)
    assert "<working>" in next(l for l in out.splitlines() if "X-2 " in l)
    assert "<" not in next(l for l in out.splitlines() if "X-1 " in l)


def test_text_omits_badges_when_herdr_is_unreachable():
    # Outside herdr the dump passes {} for statuses; sessions.json may still list panes.
    out = board.dump_text(CFG, ISSUES, {}, SESSIONS)
    assert "<" not in out


def test_json_is_valid_and_keeps_every_field():
    data = json.loads(board.dump_json(CFG, ISSUES, STATUSES, SESSIONS))
    assert data["jql"] == "jql"
    assert data["exclude_statuses"] == ["Resolved"]
    assert [c["category"] for c in data["columns"]] == ["new", "indeterminate", "done"]

    first = data["columns"][0]["issues"][0]
    assert first == {"key": "X-1", "summary": "first", "status": "To Do", "issuetype": "Task",
                     "created": "2026-08-01", "duedate": "2026-08-20", "agent_status": None,
                     "phase_labels": [], "priority": None, "epic": None,
                     "board_priority": None,
                     "url": "https://example.atlassian.net/browse/X-1"}

    second = data["columns"][1]["issues"][0]
    assert second["agent_status"] == "working"
    assert second["duedate"] is None


TODAY = date(2026, 8, 17)


def test_due_style_overdue_is_red():
    assert board.due_style("2026-08-16", TODAY) == "red"


def test_due_style_today_and_soon_are_yellow():
    assert board.due_style("2026-08-17", TODAY) == "yellow"
    assert board.due_style("2026-08-20", TODAY) == "yellow"


def test_due_style_further_out_is_dim():
    assert board.due_style("2026-08-21", TODAY) == "dim"


def test_due_style_handles_missing_and_malformed_dates():
    assert board.due_style(None, TODAY) == "dim"
    assert board.due_style("", TODAY) == "dim"
    assert board.due_style("not-a-date", TODAY) == "dim"


def test_dates_line_shows_created_and_due():
    line = board.dates_line(ISSUES[0], TODAY)
    assert "2026-08-01" in line and "2026-08-20" in line
    assert "[yellow]" in line


def test_dates_line_omits_an_unset_due_date():
    line = board.dates_line(ISSUES[1], TODAY)
    assert "2026-08-02" in line
    assert board.t("due_label") not in line


def test_dates_line_is_empty_without_dates():
    assert board.dates_line(board.Issue(key="X-9", summary="", status="To Do",
                                        category="new", issuetype="Task"), TODAY) == ""
