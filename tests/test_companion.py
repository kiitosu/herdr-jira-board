import json
import subprocess

import pytest

import board


PANES = {"result": {"panes": [
    {"pane_id": "w1:p8", "tab_id": "w1:t5", "label": "Jira Board"},
    {"pane_id": "w1:pB", "tab_id": "w1:t9", "agent": "claude", "cwd": "/data/repos/x",
     "agent_session": {"value": "sess-b"}},
]}}


class FakeHerdr:
    """Record every herdr call and answer with a plausible payload."""

    def __init__(self, resume_fails=False):
        self.calls = []
        self.resume_fails = resume_fails

    def __call__(self, cmd, capture_output=True, text=True, check=True):
        args = list(cmd[1:])
        self.calls.append(args)
        if args[:2] == ["pane", "split"]:
            payload = {"result": {"pane": {"pane_id": "w1:pZ", "tab_id": "w1:t5"}}}
        elif args[:2] == ["agent", "start"]:
            if self.resume_fails and "--resume" in args:
                raise subprocess.CalledProcessError(1, cmd, output="", stderr="no such session")
            payload = {"result": {"agent": {"pane_id": "w1:pZ",
                                            "agent_session": {"value": "sess-new"}}}}
        else:
            payload = {"result": {"panes": PANES["result"]["panes"]}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    def agent_starts(self):
        return [c for c in self.calls if c[:2] == ["agent", "start"]]


@pytest.fixture
def in_board_pane(monkeypatch, tmp_path):
    monkeypatch.setenv("HERDR_PANE_ID", "w1:p8")
    monkeypatch.setenv("HERDR_TAB_ID", "w1:t5")
    monkeypatch.setenv("HERDR_PLUGIN_CONTEXT_JSON", json.dumps({"workspace_cwd": str(tmp_path)}))


def test_companion_state_roundtrip():
    assert board.load_companion() == ""
    board.save_companion("sess-1")
    assert board.load_companion() == "sess-1"


def test_companion_pane_found_and_gone(monkeypatch):
    monkeypatch.setattr(board.subprocess, "run", FakeHerdr())
    board.save_companion("sess-b")
    assert board.companion_pane() == {"pane_id": "w1:pB", "tab_id": "w1:t9",
                                      "session_id": "sess-b", "cwd": "/data/repos/x"}
    board.save_companion("sess-closed")
    assert board.companion_pane() is None


def test_companion_cwd_falls_back_when_the_directory_is_gone(monkeypatch, tmp_path):
    monkeypatch.setenv("HERDR_PLUGIN_CONTEXT_JSON", json.dumps({"workspace_cwd": str(tmp_path)}))
    assert board.companion_cwd() == str(tmp_path)
    monkeypatch.setenv("HERDR_PLUGIN_CONTEXT_JSON",
                       json.dumps({"workspace_cwd": str(tmp_path / "removed")}))
    assert board.companion_cwd() == str(board.Path.home())


def test_open_companion_starts_a_fresh_session(monkeypatch, in_board_pane, tmp_path):
    fake = FakeHerdr()
    monkeypatch.setattr(board.subprocess, "run", fake)
    assert board.open_companion() is False
    split = next(c for c in fake.calls if c[:2] == ["pane", "split"])
    assert split[2] == "w1:p8"
    assert "--resume" not in fake.agent_starts()[0]
    assert board.load_companion() == "sess-new"


def test_open_companion_resumes_the_recorded_session(monkeypatch, in_board_pane):
    fake = FakeHerdr()
    monkeypatch.setattr(board.subprocess, "run", fake)
    board.save_companion("sess-old")
    assert board.open_companion() is True
    start = fake.agent_starts()[0]
    assert start[start.index("--"):start.index("--append-system-prompt")] == [
        "--", "--resume", "sess-old"]


def test_open_companion_tells_the_session_what_it_is():
    """The companion is never sent a prompt, so its role rides the system prompt."""
    for starter in (lambda f: f.agent_starts()[0],):
        fake = FakeHerdr()
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("HERDR_PANE_ID", "w1:p8")
            mp.setattr(board.subprocess, "run", fake)
            board.open_companion()
        start = starter(fake)
        role = start[start.index("--append-system-prompt") + 1]
        assert "jira-board" in role
        assert str(board.SESSIONS_PATH) in role


def test_the_role_names_the_command_that_reads_a_session():
    """Naming the path alone would leave the session to guess how to read it."""
    role = board.t("companion_role", sessions_path=board.SESSIONS_PATH)
    assert "herdr agent read" in role
    assert "herdr agent list" in role


def test_open_companion_starts_fresh_when_the_resume_fails(monkeypatch, in_board_pane):
    fake = FakeHerdr(resume_fails=True)
    monkeypatch.setattr(board.subprocess, "run", fake)
    board.save_companion("sess-old")
    assert board.open_companion() is False
    starts = fake.agent_starts()
    assert "--resume" in starts[0]
    assert "--resume" not in starts[1]
    assert board.load_companion() == "sess-new"


def test_open_companion_without_pane_env(monkeypatch):
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    with pytest.raises(RuntimeError):
        board.open_companion()
