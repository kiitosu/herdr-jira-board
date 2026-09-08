import subprocess

import pytest

import board


@pytest.fixture(autouse=True)
def isolated_herdr(monkeypatch):
    """Keep the app tests off the real herdr server (and its real tabs).

    The badge tick calls herdr for the tab-label sync; on a machine where
    herdr actually runs, an unstubbed call would touch the developer's own
    tabs. Tests that need herdr replace this stub with their own.
    """

    def unreachable(*args):
        raise subprocess.CalledProcessError(1, ["herdr", *args])

    monkeypatch.setattr(board, "herdr", unreachable)
    monkeypatch.delenv("HERDR_TAB_ID", raising=False)


ISSUES = [
    board.Issue(key="KAN-1", summary="first", status="To Do", category="new", issuetype="Task"),
    board.Issue(key="KAN-2", summary="second", status="To Do", category="new", issuetype="Task"),
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


def column_of(card):
    node = card.parent
    while node is not None and not isinstance(node, board.Column):
        node = node.parent
    return node


async def wait_for_cards(app, pilot):
    for _ in range(50):
        if list(app.query(board.Card)):
            return
        await pilot.pause(0.05)
    raise AssertionError("cards never appeared")


async def stage(app, pilot, key, presses=1):
    """Focus the card and stage a move that many columns to the right."""
    card = next(c for c in app.query(board.Card) if c.issue.key == key)
    card.focus()
    await pilot.pause()
    for _ in range(presses):
        await pilot.press("right")
    await pilot.pause()
    return card


async def wait_for(pilot, predicate):
    for _ in range(50):
        if predicate():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition never became true")


@pytest.mark.asyncio
async def test_initial_focus_and_columns(app):
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        cards = list(app.query(board.Card))
        assert [c.issue.key for c in cards] == ["KAN-1", "KAN-2"]
        # The initial focus lands one refresh after the cards mount.
        await wait_for(pilot, lambda: isinstance(app.focused, board.Card))
        assert app.focused.issue.key == "KAN-1"
        assert all(column_of(c).category == "new" for c in cards)


@pytest.mark.asyncio
async def test_stage_move_and_cancel(app):
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("right")
        await pilot.pause()
        card = next(c for c in app.query(board.Card) if c.issue.key == "KAN-1")
        assert card.pending_target == "indeterminate"
        assert column_of(card).category == "indeterminate"

        await pilot.press("escape")
        await pilot.pause()
        assert card.pending_target is None
        assert column_of(card).category == "new"


@pytest.mark.asyncio
async def test_confirm_runs_single_transition(app, monkeypatch):
    transitions = [{"id": "41", "name": "Done",
                    "to": {"name": "Done", "statusCategory": {"key": "done"}}}]
    executed = []
    monkeypatch.setattr(board.Jira, "transitions", lambda self, key: transitions)
    monkeypatch.setattr(board.Jira, "do_transition",
                        lambda self, key, tid: executed.append((key, tid)))
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("right")
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: executed)
        assert executed == [("KAN-1", "41")]


def status_label_app(app, monkeypatch, to_name):
    """Point every transition at `to_name` and record label updates."""
    cfg = board.Config(
        site="https://example.atlassian.net", email="you@example.com",
        api_token="t", jql="jql",
        status_labels=[board.StatusLabelRule(to_name, add=["jb_verifying"])])
    # The refresh's config reload must hand back the same rules.
    monkeypatch.setattr(board.Config, "load", classmethod(lambda cls, path=None: cfg))
    app.cfg = cfg
    transitions = [{"id": "41", "name": to_name,
                    "to": {"name": to_name, "statusCategory": {"key": "done"}}}]
    updates = []
    monkeypatch.setattr(board.Jira, "transitions", lambda self, key: transitions)
    monkeypatch.setattr(board.Jira, "do_transition", lambda self, key, tid: None)
    monkeypatch.setattr(board.Jira, "update_labels",
                        lambda self, key, add, remove: updates.append((key, add, remove)))
    return updates


@pytest.mark.asyncio
async def test_a_move_applies_the_status_label_rules(app, monkeypatch):
    updates = status_label_app(app, monkeypatch, "In Review")
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("right")
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: updates)
        assert updates == [("KAN-1", ["jb_verifying"], [])]


@pytest.mark.asyncio
async def test_t_applies_the_status_label_rules(app, monkeypatch):
    updates = status_label_app(app, monkeypatch, "In Review")
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("t")
        await wait_for(pilot, lambda: isinstance(app.screen, board.TransitionPicker))
        await pilot.press("enter")
        await wait_for(pilot, lambda: updates)
        assert updates == [("KAN-1", ["jb_verifying"], [])]


@pytest.mark.asyncio
async def test_staged_moves_accumulate(app):
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        first = await stage(app, pilot, "KAN-1")
        second = await stage(app, pilot, "KAN-2", presses=2)
        assert (first.pending_target, second.pending_target) == ("indeterminate", "done")
        assert column_of(first).category == "indeterminate"
        assert column_of(second).category == "done"


@pytest.mark.asyncio
async def test_confirm_runs_every_staged_move(app, monkeypatch):
    transitions = [
        {"id": "21", "name": "In Progress",
         "to": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}},
        {"id": "41", "name": "Done", "to": {"name": "Done", "statusCategory": {"key": "done"}}},
    ]
    executed = []
    monkeypatch.setattr(board.Jira, "transitions", lambda self, key: transitions)
    monkeypatch.setattr(board.Jira, "do_transition",
                        lambda self, key, tid: executed.append((key, tid)))
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await stage(app, pilot, "KAN-1")
        await stage(app, pilot, "KAN-2", presses=2)
        await pilot.press("enter")
        await wait_for(pilot, lambda: len(executed) == 2)
        assert sorted(executed) == [("KAN-1", "21"), ("KAN-2", "41")]


@pytest.mark.asyncio
async def test_escape_cancels_every_staged_move(app):
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        first = await stage(app, pilot, "KAN-1")
        second = await stage(app, pilot, "KAN-2", presses=2)
        assert (first.pending_target, second.pending_target) == ("indeterminate", "done")
        await pilot.press("escape")
        await pilot.pause()
        assert (first.pending_target, second.pending_target) == (None, None)
        assert all(column_of(c).category == "new" for c in (first, second))


@pytest.mark.asyncio
async def test_picker_runs_once_per_staged_card(app, monkeypatch):
    transitions = [
        {"id": "21", "name": "Start",
         "to": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}},
        {"id": "31", "name": "Review",
         "to": {"name": "In Review", "statusCategory": {"key": "indeterminate"}}},
    ]
    executed = []
    monkeypatch.setattr(board.Jira, "transitions", lambda self, key: transitions)
    monkeypatch.setattr(board.Jira, "do_transition",
                        lambda self, key, tid: executed.append((key, tid)))
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await stage(app, pilot, "KAN-1")
        await stage(app, pilot, "KAN-2")
        await pilot.press("enter")
        for _ in range(2):
            await wait_for(pilot, lambda: isinstance(app.screen, board.TransitionPicker))
            await pilot.press("enter")  # take the first candidate
            await pilot.pause()
        await wait_for(pilot, lambda: len(executed) == 2)
        assert sorted(executed) == [("KAN-1", "21"), ("KAN-2", "21")]


@pytest.mark.asyncio
async def test_failed_card_does_not_stop_the_others(app, monkeypatch):
    transitions = [{"id": "21", "name": "In Progress",
                    "to": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}]
    executed = []

    def do_transition(self, key, tid):
        if key == "KAN-1":
            raise RuntimeError("boom")
        executed.append((key, tid))

    monkeypatch.setattr(board.Jira, "transitions", lambda self, key: transitions)
    monkeypatch.setattr(board.Jira, "do_transition", do_transition)
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        failing = await stage(app, pilot, "KAN-1")
        other = await stage(app, pilot, "KAN-2")
        assert (failing.pending_target, other.pending_target) == ("indeterminate",) * 2
        await pilot.press("enter")
        await wait_for(pilot, lambda: executed)
        assert executed == [("KAN-2", "21")]
        # The failing card gives up its staged move; the other one still went through.
        assert failing.pending_target is None


def running_session(app, monkeypatch, status, pane_payload=None):
    """Give KAN-1 a live session in the given agent status, and record herdr calls."""
    calls = []
    payload = pane_payload or {"result": {"pane": {"tab_id": "w1:t9"}}}
    # On the file too: the badge tick re-reads the state files and would wipe
    # a mapping that only lives in this board's memory.
    app.sessions = board.update_map(board.SESSIONS_PATH, {"KAN-1": "w1:p5"})
    monkeypatch.setattr(board, "agent_statuses", lambda: {"w1:p5": status})
    monkeypatch.setattr(board, "find_session_pane", lambda key: None)

    def fake_herdr(*args):
        calls.append(list(args))
        return payload

    monkeypatch.setattr(board, "herdr", fake_herdr)
    monkeypatch.setattr(board, "send_prompt",
                        lambda pane, prompt: calls.append(["agent", "prompt", pane, prompt]))
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["idle", "working"])
async def test_enter_goes_to_the_session_without_sending_anything(app, monkeypatch, status):
    calls = running_session(app, monkeypatch, status)
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        card = next(c for c in app.query(board.Card) if c.issue.key == "KAN-1")
        card.focus()
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: ["tab", "focus", "w1:t9"] in calls)
        for _ in range(10):
            await pilot.pause(0.05)
        assert not any(c[:2] == ["agent", "prompt"] for c in calls)


@pytest.mark.asyncio
async def test_enter_keeps_the_mapping_when_agent_list_fails(app, monkeypatch):
    """A failed `herdr agent list` says nothing about the pane: don't drop it."""
    calls = running_session(app, monkeypatch, "idle")
    monkeypatch.setattr(board, "agent_statuses", lambda: None)
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        card = next(c for c in app.query(board.Card) if c.issue.key == "KAN-1")
        card.focus()
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: ["tab", "focus", "w1:t9"] in calls)
        assert app.sessions == {"KAN-1": "w1:p5"}
        assert board.load_sessions() == {"KAN-1": "w1:p5"}


@pytest.mark.asyncio
async def test_badge_tick_picks_up_what_another_board_saved(app, monkeypatch):
    monkeypatch.setattr(board, "agent_statuses", lambda: {"w1:p9": "working"})
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        # another board records a session for KAN-2 straight on the file
        board.update_map(board.SESSIONS_PATH, {"KAN-2": "w1:p9"})
        app.update_badges()
        card = next(c for c in app.query(board.Card) if c.issue.key == "KAN-2")
        await wait_for(pilot, lambda: card.agent_status == "working")
        assert app.sessions == {"KAN-2": "w1:p9"}


@pytest.mark.asyncio
async def test_enter_resumes_the_recorded_claude_session(app, monkeypatch):
    launched = []

    def fake_launch(issue, cfg, description="", resume_session=""):
        launched.append((issue.key, resume_session))
        return "w1:p7", "sess-new", True

    app.claude_sessions = board.update_map(board.CLAUDE_SESSIONS_PATH, {"KAN-1": "sess-old"})
    monkeypatch.setattr(board, "find_session_pane", lambda key: None)
    monkeypatch.setattr(board.Jira, "description", lambda self, key: "")
    monkeypatch.setattr(board, "launch_claude", fake_launch)
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("enter")  # KAN-1 has the initial focus, no session pane
        await wait_for(pilot, lambda: launched)
        assert launched == [("KAN-1", "sess-old")]
        await wait_for(pilot, lambda: app.claude_sessions.get("KAN-1") == "sess-new")
        assert board.load_claude_sessions() == {"KAN-1": "sess-new"}
        assert app.sessions["KAN-1"] == "w1:p7"


@pytest.mark.asyncio
async def test_focusing_a_live_session_records_its_claude_session(app, monkeypatch):
    running_session(app, monkeypatch, "idle",
                    pane_payload={"result": {"pane": {
                        "tab_id": "w1:t9", "agent_session": {"value": "sess-live"}}}})
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        card = next(c for c in app.query(board.Card) if c.issue.key == "KAN-1")
        card.focus()
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: app.claude_sessions.get("KAN-1") == "sess-live")
        assert board.load_claude_sessions() == {"KAN-1": "sess-live"}


@pytest.mark.asyncio
async def test_preview_shows_the_last_reply_of_the_focused_card(app, monkeypatch):
    app.claude_sessions = board.update_map(board.CLAUDE_SESSIONS_PATH, {"KAN-1": "sess-1"})
    monkeypatch.setattr(board, "transcript_path", lambda sid: board.Path("/x/sess-1.jsonl"))
    monkeypatch.setattr(board, "last_assistant_text", lambda path: "did the thing")
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        # KAN-1 gets the initial focus, so its preview appears on its own
        await wait_for(pilot, lambda: app.query_one(board.Preview).display)
        text = app.query_one("#preview-text", board.Static)
        assert "did the thing" in str(text.render())
        # KAN-2 has no session: moving to it hides the preview
        await pilot.press("down")
        await wait_for(pilot, lambda: not app.query_one(board.Preview).display)


def with_preview(app, monkeypatch, text="did the thing"):
    """Give KAN-1 a session whose transcript holds a reply."""
    app.claude_sessions = board.update_map(board.CLAUDE_SESSIONS_PATH, {"KAN-1": "sess-1"})
    monkeypatch.setattr(board, "transcript_path", lambda sid: board.Path("/x/sess-1.jsonl"))
    monkeypatch.setattr(board, "last_assistant_text", lambda path: text)


@pytest.mark.asyncio
async def test_p_turns_the_preview_off_and_on(app, monkeypatch):
    with_preview(app, monkeypatch)
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await wait_for(pilot, lambda: app.query_one(board.Preview).display)
        await pilot.press("p")
        await wait_for(pilot, lambda: not app.query_one(board.Preview).display)
        await pilot.press("p")
        await wait_for(pilot, lambda: app.query_one(board.Preview).display)


@pytest.mark.asyncio
async def test_the_badge_tick_keeps_a_disabled_preview_hidden(app, monkeypatch):
    """The tick refreshes the preview; it must not bring back what `p` hid."""
    with_preview(app, monkeypatch)
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("p")
        await wait_for(pilot, lambda: not app.query_one(board.Preview).display)
        app.update_badges()
        for _ in range(10):
            await pilot.pause(0.05)
        assert not app.query_one(board.Preview).display


@pytest.mark.asyncio
async def test_preview_false_starts_hidden(app, monkeypatch):
    """`preview = false` in the config; `p` still brings it up."""
    with_preview(app, monkeypatch)
    app.cfg.preview = False
    app.preview_enabled = False
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        for _ in range(10):
            await pilot.pause(0.05)
        assert not app.query_one(board.Preview).display
        await pilot.press("p")
        await wait_for(pilot, lambda: app.query_one(board.Preview).display)


@pytest.mark.asyncio
async def test_preview_hides_when_the_transcript_has_no_reply_yet(app, monkeypatch):
    app.claude_sessions = board.update_map(board.CLAUDE_SESSIONS_PATH, {"KAN-1": "sess-1"})
    monkeypatch.setattr(board, "transcript_path", lambda sid: None)
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        for _ in range(10):
            await pilot.pause(0.05)
        assert not app.query_one(board.Preview).display
