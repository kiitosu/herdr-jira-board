import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep every test off the real plugin state directory."""
    import board

    state = tmp_path / "state"
    monkeypatch.setattr(board, "STATE_DIR", state)
    monkeypatch.setattr(board, "SESSIONS_PATH", state / "sessions.json")
    monkeypatch.setattr(board, "CLAUDE_SESSIONS_PATH", state / "claude_sessions.json")
    monkeypatch.setattr(board, "COMPANION_PATH", state / "companion.json")


@pytest.fixture(autouse=True)
def isolated_herdr(monkeypatch):
    """Keep every test off the real herdr server.

    On a developer machine herdr actually runs, and an app test's badge tick
    would reach it — the tab-label sync would rename the developer's own tabs.
    Pointing the CLI at a binary that does not exist turns every real call
    into the OSError the code already treats as "herdr unreachable". Tests
    that fake herdr themselves (via board.herdr or subprocess.run) never get
    as far as the binary, so they are unaffected.
    """
    monkeypatch.setenv("HERDR_BIN_PATH", "/nonexistent/herdr")
    monkeypatch.delenv("HERDR_TAB_ID", raising=False)
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
