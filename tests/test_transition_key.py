import pytest

import board


ISSUES = [
    board.Issue(key="KAN-1", summary="first", status="進行中", category="indeterminate",
                issuetype="Task"),
]

TRANSITIONS = [
    {"id": "31", "name": "Review",
     "to": {"name": "レビュー中", "statusCategory": {"key": "indeterminate"}}},
    {"id": "41", "name": "Done", "to": {"name": "完了", "statusCategory": {"key": "done"}}},
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


async def wait_for_cards(app, pilot):
    for _ in range(50):
        if list(app.query(board.Card)):
            return
        await pilot.pause(0.05)
    raise AssertionError("cards never appeared")


async def wait_for(pilot, predicate):
    for _ in range(50):
        if predicate():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition never became true")


@pytest.mark.asyncio
async def test_t_opens_the_picker_and_runs_the_transition(app, monkeypatch):
    executed = []
    monkeypatch.setattr(board.Jira, "transitions", lambda self, key: list(TRANSITIONS))
    monkeypatch.setattr(board.Jira, "do_transition",
                        lambda self, key, tid: executed.append((key, tid)))
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("t")
        await wait_for(pilot, lambda: isinstance(app.screen, board.TransitionPicker))
        # Every transition is offered, same-category ones included.
        assert len(app.screen.transitions) == 2
        await pilot.press("enter")  # take the first candidate (レビュー中)
        await wait_for(pilot, lambda: executed)
        assert executed == [("KAN-1", "31")]


@pytest.mark.asyncio
async def test_t_asks_even_for_a_single_transition(app, monkeypatch):
    monkeypatch.setattr(board.Jira, "transitions", lambda self, key: TRANSITIONS[:1])
    monkeypatch.setattr(board.Jira, "do_transition",
                        lambda self, key, tid: pytest.fail("must not run before picking"))
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("t")
        await wait_for(pilot, lambda: isinstance(app.screen, board.TransitionPicker))
        await pilot.press("escape")  # cancelling runs nothing
        await pilot.pause()


@pytest.mark.asyncio
async def test_t_without_transitions_only_notifies(app, monkeypatch):
    monkeypatch.setattr(board.Jira, "transitions", lambda self, key: [])
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        assert not isinstance(app.screen, board.TransitionPicker)
