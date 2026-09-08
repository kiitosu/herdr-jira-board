"""The open-issue tab action: find the issue behind a tab and open it."""

import json
import os
import subprocess
import time

import pytest

import board


def herdr_stub(monkeypatch, panes=(), process_info=None):
    def fake(*args):
        if args[:2] == ("pane", "list"):
            return {"panes": list(panes)}
        if args[:2] == ("pane", "process-info"):
            return process_info or {}
        raise subprocess.CalledProcessError(1, ["herdr", *args])

    monkeypatch.setattr(board, "herdr", fake)


# ---- the three-step resolution

def test_the_boards_own_record_wins(monkeypatch):
    herdr_stub(monkeypatch, panes=[{"pane_id": "w1:p2", "tab_id": "w1:t2"}])
    key = board.issue_key_for_tab("w1:t2", "renamed by hand", {"PROJ-1": "w1:p2"})
    assert key == "PROJ-1"


def test_a_record_for_another_tab_does_not_win(monkeypatch):
    herdr_stub(monkeypatch, panes=[{"pane_id": "w1:p9", "tab_id": "w1:t9"}])
    key = board.issue_key_for_tab("w1:t2", "PROJ-3", {"PROJ-1": "w1:p9"})
    assert key == "PROJ-3"  # falls through to the label


def test_the_tabs_environment_comes_second(monkeypatch):
    """A live shell started with JIRA_ISSUE_KEY gives the key back via /proc."""
    child = subprocess.Popen(["sleep", "30"],
                             env={**os.environ, "JIRA_ISSUE_KEY": "PROJ-7"})
    try:
        time.sleep(0.1)
        herdr_stub(monkeypatch,
                   panes=[{"pane_id": "w1:p2", "tab_id": "w1:t2"}],
                   process_info={"process_info": {"shell_pid": child.pid}})
        assert board.issue_key_for_tab("w1:t2", "renamed by hand", {}) == "PROJ-7"
    finally:
        child.kill()
        child.wait()


def test_the_label_is_the_last_resort_with_the_icon_stripped(monkeypatch):
    herdr_stub(monkeypatch)
    assert board.issue_key_for_tab("w1:t2", "● PROJ-12", {}) == "PROJ-12"
    assert board.issue_key_for_tab("", "PROJ-12", {}) == "PROJ-12"
    assert board.issue_key_for_tab("w1:t2", "scratch tab", {}) == ""


def test_an_unreadable_pane_env_falls_back_quietly(monkeypatch):
    herdr_stub(monkeypatch,
               panes=[{"pane_id": "w1:p2", "tab_id": "w1:t2"}],
               process_info={"process_info": {"shell_pid": None}})
    assert board.issue_key_for_tab("w1:t2", "PROJ-3", {}) == "PROJ-3"


# ---- the action entry point

@pytest.fixture
def action_env(monkeypatch):
    monkeypatch.setattr(board.Config, "load",
                        classmethod(lambda cls, path=None: board.Config(
                            site="https://example.atlassian.net", email="e",
                            api_token="t", jql="j")))
    opened = []
    monkeypatch.setattr(board, "open_url", lambda url: opened.append(url))
    return opened


def test_open_tab_issue_opens_the_url(monkeypatch, action_env, capsys):
    monkeypatch.setenv("HERDR_PLUGIN_CONTEXT_JSON",
                       json.dumps({"tab_id": "w1:t2", "tab_label": "✔ PROJ-5"}))
    assert board.open_tab_issue() == 0
    assert action_env == ["https://example.atlassian.net/browse/PROJ-5"]
    assert "PROJ-5" in capsys.readouterr().out


def test_open_tab_issue_says_so_when_there_is_no_issue(monkeypatch, action_env, capsys):
    monkeypatch.setenv("HERDR_PLUGIN_CONTEXT_JSON",
                       json.dumps({"tab_id": "w1:t2", "tab_label": "scratch"}))
    assert board.open_tab_issue() == 1
    assert action_env == []
    assert "scratch" in capsys.readouterr().err


def test_open_tab_issue_survives_a_missing_context(monkeypatch, action_env):
    monkeypatch.delenv("HERDR_PLUGIN_CONTEXT_JSON", raising=False)
    assert board.open_tab_issue() == 1
    assert action_env == []
