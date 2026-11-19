"""The card you were on keeps the focus when the board is laid out again."""

import pytest

import board


ISSUES = [
    board.Issue(key="KAN-1", summary="first", status="To Do", category="new",
                issuetype="Task"),
    board.Issue(key="KAN-2", summary="second", status="To Do", category="new",
                issuetype="Task"),
]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(board.Config, "load",
                        classmethod(lambda cls, path=None: board.Config(
                            site="https://example.atlassian.net", email="you@example.com",
                            api_token="t", jql="jql")))
    monkeypatch.setattr(board.Jira, "search", lambda self: list(ISSUES))
    monkeypatch.setattr(board, "agent_statuses", lambda: {})
    return board.BoardApp()


async def wait_for(pilot, predicate):
    for _ in range(50):
        if predicate():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition never became true")


def focused_key(app):
    return app.focused.issue.key if isinstance(app.focused, board.Card) else None


@pytest.mark.asyncio
async def test_a_confirmed_move_leaves_the_focus_on_the_card_that_moved(app, monkeypatch):
    transitions = [{"id": "21", "name": "In Progress",
                    "to": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}]
    monkeypatch.setattr(board.Jira, "transitions", lambda self, key: transitions)
    monkeypatch.setattr(board.Jira, "do_transition", lambda self, key, tid: None)
    # The refresh that follows the move finds KAN-2 in its new column.
    moved = board.Issue(key="KAN-2", summary="second", status="In Progress",
                        category="indeterminate", issuetype="Task")
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: focused_key(app) == "KAN-1")
        second = next(c for c in app.query(board.Card) if c.issue.key == "KAN-2")
        second.focus()
        await pilot.pause()

        await pilot.press("right")
        await pilot.pause()
        monkeypatch.setattr(board.Jira, "search", lambda self: [ISSUES[0], moved])
        await pilot.press("enter")

        # Not the staged card but the one the refresh built in its place, so
        # this is the focus surviving the board being laid out again.
        await wait_for(pilot, lambda: isinstance(app.focused, board.Card)
                       and app.focused is not second
                       and app.focused.issue.key == "KAN-2")
        assert app.focused.parent.category == "indeterminate"


@pytest.mark.asyncio
async def test_a_refresh_leaves_the_focus_where_it_was(app, monkeypatch):
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: focused_key(app) == "KAN-1")
        second = next(c for c in app.query(board.Card) if c.issue.key == "KAN-2")
        second.focus()
        await pilot.pause()

        await pilot.press("r")
        await pilot.pause()
        await wait_for(pilot, lambda: focused_key(app) == "KAN-2")


@pytest.mark.asyncio
async def test_the_first_card_takes_over_when_the_issue_has_left(app, monkeypatch):
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: focused_key(app) == "KAN-1")
        second = next(c for c in app.query(board.Card) if c.issue.key == "KAN-2")
        second.focus()
        await pilot.pause()

        monkeypatch.setattr(board.Jira, "search", lambda self: [ISSUES[0]])
        await pilot.press("r")
        await wait_for(pilot, lambda: focused_key(app) == "KAN-1")
