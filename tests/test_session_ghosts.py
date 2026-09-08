"""Session ghosts: issues that left the board stay visible while their tab lives."""

import pytest

import board


def issue(key, category="done", status="完了"):
    return board.Issue(key=key, summary="", status=status, category=category,
                       issuetype="Task")


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def jira_with(monkeypatch, issues_by_key):
    def fake_get(self, url, params=None):
        key = url.rsplit("/", 1)[-1]
        if key not in issues_by_key:
            return FakeResponse({}, status_code=404)
        return FakeResponse({"key": key, "fields": {
            "summary": "s",
            "status": {"name": "完了", "statusCategory": {"key": "done"}}}})

    monkeypatch.setattr(board.httpx.Client, "get", fake_get)
    return board.Jira(board.Config(site="https://example.atlassian.net", email="e",
                                   api_token="t", jql="j"))


def panes_stub(monkeypatch, pane_ids):
    def fake(*args):
        if args[:2] == ("pane", "list"):
            return {"panes": [{"pane_id": p, "tab_id": "t"} for p in pane_ids]}
        raise AssertionError(f"unexpected herdr call: {args}")

    monkeypatch.setattr(board, "herdr", fake)


# ---- fetching one issue

def test_issue_parses_like_a_search_hit(monkeypatch):
    jira = jira_with(monkeypatch, {"X-9"})
    it = jira.issue("X-9")
    assert (it.key, it.status, it.category) == ("X-9", "完了", "done")


def test_issue_returns_none_when_it_is_gone(monkeypatch):
    assert jira_with(monkeypatch, set()).issue("X-9") is None


# ---- which sessions become ghosts

def test_only_offboard_issues_with_a_live_pane_become_ghosts(monkeypatch):
    panes_stub(monkeypatch, ["w1:p2"])
    jira = jira_with(monkeypatch, {"X-2", "X-3"})
    ghosts = board.session_ghosts(
        [issue("X-1")],
        {"X-1": "w1:p2",   # still on the board: no ghost
         "X-2": "w1:p2",   # off the board, pane alive: ghost
         "X-3": "w1:p9"},  # off the board, pane gone: dropped
        jira)
    assert [g.key for g in ghosts] == ["X-2"]


def test_a_deleted_issue_is_skipped(monkeypatch):
    panes_stub(monkeypatch, ["w1:p2", "w1:p3"])
    jira = jira_with(monkeypatch, {"X-2"})
    ghosts = board.session_ghosts([], {"X-2": "w1:p2", "X-4": "w1:p3"}, jira)
    assert [g.key for g in ghosts] == ["X-2"]


def test_ghosts_are_empty_when_herdr_is_unreachable(monkeypatch):
    jira = jira_with(monkeypatch, {"X-2"})
    assert board.session_ghosts([], {"X-2": "w1:p2"}, jira) == []


# ---- the board

@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(board.Config, "load",
                        classmethod(lambda cls, path=None: board.Config(
                            site="https://example.atlassian.net", email="you@example.com",
                            api_token="t", jql="jql")))
    monkeypatch.setattr(board.Jira, "search",
                        lambda self: [board.Issue(key="KAN-1", summary="", status="To Do",
                                                  category="new", issuetype="Task")])
    monkeypatch.setattr(board, "session_ghosts",
                        lambda issues, sessions, jira: [issue("KAN-9")])
    monkeypatch.setattr(board, "agent_statuses", lambda: {})
    return board.BoardApp()


async def wait_for(pilot, predicate):
    for _ in range(50):
        if predicate():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition never became true")


@pytest.mark.asyncio
async def test_the_ghost_gets_a_dimmed_card_in_its_column(app):
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: len(list(app.query(board.Card))) == 2)
        ghost = next(c for c in app.query(board.Card) if c.issue.key == "KAN-9")
        assert ghost.has_class("ghost-card")
        column = ghost.parent
        while column is not None and not isinstance(column, board.Column):
            column = column.parent
        assert column.category == "done"


@pytest.mark.asyncio
async def test_the_hidden_toggle_keeps_the_ghosts(app):
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: len(list(app.query(board.Card))) == 2)
        app.cfg.exclude_labels = ["jb_wont_do"]
        await pilot.press("h")
        await wait_for(pilot, lambda: app.show_hidden)
        assert any(c.issue.key == "KAN-9" for c in app.query(board.Card))
