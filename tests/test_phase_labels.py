"""Phase labels: a Jira label the board shows, sorts by, and toggles with `l`."""

import pytest
from textual.widgets import OptionList

import board


VERIFY = board.PhaseLabel("jb_効果確認中", "効果確認中")
WAITING = board.PhaseLabel("jb_先方確認待ち", "先方確認待ち")


def issue(key, labels=(), status="進行中"):
    return board.Issue(key=key, summary="", status=status, category="indeterminate",
                       issuetype="Task", labels=list(labels))


def sort_by_phase_label(issues, phase_labels):
    """The in-group sort with only phase labels configured."""
    cfg = board.Config(site="s", email="e", api_token="t", jql="q",
                       phase_labels=phase_labels)
    return board.sort_cards(issues, cfg)


# ---- config


def test_parses_a_table_entry():
    assert board.PhaseLabel.parse({"label": "jb_x", "display": "X"}) == board.PhaseLabel("jb_x", "X")


def test_a_bare_string_shows_the_label_as_it_is():
    assert board.PhaseLabel.parse("jb_x") == board.PhaseLabel("jb_x", "jb_x")


def test_an_entry_without_a_label_is_dropped():
    assert board.PhaseLabel.parse({"display": "X"}) is None
    assert board.PhaseLabel.parse("") is None
    assert board.PhaseLabel.parse(42) is None


def test_config_loads_phase_labels(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('site = "https://example.atlassian.net"\nemail = "you@example.com"\n'
                 'api_token = "t"\n'
                 '[[phase_labels]]\nlabel = "jb_効果確認中"\ndisplay = "効果確認中"\n'
                 '[[phase_labels]]\nlabel = "jb_先方確認待ち"\ndisplay = "先方確認待ち"\n')
    assert board.Config.load(p).phase_labels == [VERIFY, WAITING]


def test_phase_labels_default_to_empty(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('site = "https://example.atlassian.net"\nemail = "you@example.com"\n'
                 'api_token = "t"\n')
    assert board.Config.load(p).phase_labels == []


# ---- sorting and lookup


def test_labelled_issues_come_first_in_the_configured_order():
    issues = [issue("X-1"), issue("X-2", ["jb_先方確認待ち"]), issue("X-3", ["jb_効果確認中"])]
    order = [i.key for i in sort_by_phase_label(issues, [VERIFY, WAITING])]
    assert order == ["X-3", "X-2", "X-1"]


def test_unlabelled_issues_keep_the_jql_order():
    issues = [issue("X-1"), issue("X-2"), issue("X-3")]
    assert sort_by_phase_label(issues, [VERIFY]) == issues


def test_other_labels_do_not_move_a_card():
    """Labels the board knows nothing about (someone_elses etc.) must not reorder."""
    issues = [issue("X-1", ["someone_elses"]), issue("X-2", ["jb_効果確認中"])]
    assert [i.key for i in sort_by_phase_label(issues, [VERIFY])] == ["X-2", "X-1"]


def test_an_issue_with_two_phase_labels_takes_the_best_rank():
    issues = [issue("X-1", ["jb_先方確認待ち"]),
              issue("X-2", ["jb_先方確認待ち", "jb_効果確認中"])]
    assert [i.key for i in sort_by_phase_label(issues, [VERIFY, WAITING])] == ["X-2", "X-1"]


def test_phase_labels_of_returns_configured_ones_in_order():
    it = issue("X-1", ["someone_elses", "jb_先方確認待ち", "jb_効果確認中"])
    assert board.phase_labels_of(it, [VERIFY, WAITING]) == [VERIFY, WAITING]
    assert board.phase_labels_of(issue("X-2"), [VERIFY]) == []


# ---- the Jira call


class FakeResponse:
    def raise_for_status(self):
        pass


def test_update_labels_sends_the_changes_not_the_whole_list(monkeypatch):
    """Other people's labels must survive, so the `update` verb is required."""
    sent = {}

    def fake_put(self, url, json):
        sent.update(url=url, body=json)
        return FakeResponse()

    monkeypatch.setattr(board.httpx.Client, "put", fake_put)
    cfg = board.Config(site="https://example.atlassian.net", email="e", api_token="t", jql="j")
    board.Jira(cfg).update_labels("KAN-1", ["jb_効果確認中"], [])
    assert sent["url"] == "/rest/api/3/issue/KAN-1"
    assert sent["body"] == {"update": {"labels": [{"add": "jb_効果確認中"}]}}


def test_update_labels_puts_adds_and_removes_in_one_request(monkeypatch):
    sent = {}
    monkeypatch.setattr(board.httpx.Client, "put",
                        lambda self, url, json: (sent.update(body=json), FakeResponse())[1])
    cfg = board.Config(site="https://example.atlassian.net", email="e", api_token="t", jql="j")
    board.Jira(cfg).update_labels("KAN-1", ["jb_a", "jb_b"], ["jb_c"])
    assert sent["body"] == {"update": {"labels": [
        {"add": "jb_a"}, {"add": "jb_b"}, {"remove": "jb_c"}]}}


def test_update_labels_skips_the_request_when_nothing_changed(monkeypatch):
    monkeypatch.setattr(board.httpx.Client, "put",
                        lambda *a, **k: pytest.fail("must not call Jira for an empty change"))
    cfg = board.Config(site="https://example.atlassian.net", email="e", api_token="t", jql="j")
    board.Jira(cfg).update_labels("KAN-1", [], [])


def test_describe_label_changes_uses_the_display_names():
    assert board.describe_label_changes(
        ["jb_効果確認中"], ["jb_先方確認待ち"], [VERIFY, WAITING]) == "+効果確認中 -先方確認待ち"


def test_describe_label_changes_falls_back_to_the_raw_name():
    assert board.describe_label_changes(["jb_unknown"], [], [VERIFY]) == "+jb_unknown"


# ---- the card and the key


ISSUES = [issue("KAN-1", ["jb_効果確認中"]), issue("KAN-2")]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(board.Config, "load",
                        classmethod(lambda cls, path=None: board.Config(
                            site="https://example.atlassian.net", email="you@example.com",
                            api_token="t", jql="jql", phase_labels=[VERIFY])))
    monkeypatch.setattr(board.Jira, "search", lambda self: [issue(i.key, i.labels)
                                                            for i in ISSUES])
    monkeypatch.setattr(board, "agent_statuses", lambda: {})
    return board.BoardApp()


@pytest.fixture
def multi_app(monkeypatch):
    """A board with two phase labels configured, for the multi-select cases."""
    monkeypatch.setattr(board.Config, "load",
                        classmethod(lambda cls, path=None: board.Config(
                            site="https://example.atlassian.net", email="you@example.com",
                            api_token="t", jql="jql", phase_labels=[VERIFY, WAITING])))
    monkeypatch.setattr(board.Jira, "search",
                        lambda self: [issue("KAN-1", ["jb_効果確認中"])])
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
async def test_the_card_shows_the_display_name_only(app):
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        card = next(c for c in app.query(board.Card) if c.issue.key == "KAN-1")
        text = str(card.render())
        assert "効果確認中" in text
        assert "jb_" not in text


@pytest.mark.asyncio
async def test_space_unticks_the_label_the_card_has_and_enter_removes_it(app, monkeypatch):
    calls = []
    monkeypatch.setattr(board.Jira, "update_labels",
                        lambda self, key, add, remove: calls.append((key, add, remove)))
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("l")
        await wait_for(pilot, lambda: isinstance(app.screen, board.PhaseLabelPicker))
        # KAN-1 sorts first because it carries the label, so it starts ticked.
        assert app.screen.ticked == {"jb_効果確認中"}
        await pilot.press("space")
        assert app.screen.ticked == set()
        await pilot.press("enter")
        await wait_for(pilot, lambda: calls)
        assert calls == [("KAN-1", [], ["jb_効果確認中"])]


@pytest.mark.asyncio
async def test_space_ticks_the_label_the_card_lacks_and_enter_adds_it(app, monkeypatch):
    calls = []
    monkeypatch.setattr(board.Jira, "update_labels",
                        lambda self, key, add, remove: calls.append((key, add, remove)))
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("down")  # move to KAN-2, which has no phase label
        await pilot.press("l")
        await wait_for(pilot, lambda: isinstance(app.screen, board.PhaseLabelPicker))
        await pilot.press("space")
        await pilot.press("enter")
        await wait_for(pilot, lambda: calls)
        assert calls == [("KAN-2", ["jb_効果確認中"], [])]


@pytest.mark.asyncio
async def test_enter_applies_every_tick_in_one_call(multi_app, monkeypatch):
    """The point of the multi-select: several labels, one trip to Jira."""
    calls = []
    monkeypatch.setattr(board.Jira, "update_labels",
                        lambda self, key, add, remove: calls.append((key, add, remove)))
    async with multi_app.run_test() as pilot:
        await wait_for_cards(multi_app, pilot)
        await pilot.press("l")
        await wait_for(pilot, lambda: isinstance(multi_app.screen, board.PhaseLabelPicker))
        await pilot.press("space")  # untick 効果確認中, which KAN-1 carries
        await pilot.press("down")
        await pilot.press("space")  # tick 先方確認待ち
        await pilot.press("enter")
        await wait_for(pilot, lambda: calls)
        assert calls == [("KAN-1", ["jb_先方確認待ち"], ["jb_効果確認中"])]


@pytest.mark.asyncio
async def test_enter_without_ticking_anything_calls_nothing(app, monkeypatch):
    monkeypatch.setattr(board.Jira, "update_labels",
                        lambda *a: pytest.fail("must not run when nothing changed"))
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("l")
        await wait_for(pilot, lambda: isinstance(app.screen, board.PhaseLabelPicker))
        await pilot.press("enter")
        await pilot.pause()


@pytest.mark.asyncio
async def test_the_tick_survives_moving_the_cursor(multi_app):
    """Rebuilding the rows must not lose the ticks or the cursor position."""
    async with multi_app.run_test() as pilot:
        await wait_for_cards(multi_app, pilot)
        await pilot.press("l")
        await wait_for(pilot, lambda: isinstance(multi_app.screen, board.PhaseLabelPicker))
        await pilot.press("down")
        await pilot.press("space")
        assert multi_app.screen.query_one(OptionList).highlighted == 1
        await pilot.press("up")
        await pilot.press("down")
        assert multi_app.screen.ticked == {"jb_効果確認中", "jb_先方確認待ち"}


@pytest.mark.asyncio
async def test_cancelling_the_picker_changes_nothing(app, monkeypatch):
    monkeypatch.setattr(board.Jira, "update_labels",
                        lambda *a: pytest.fail("must not run when cancelled"))
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("l")
        await wait_for(pilot, lambda: isinstance(app.screen, board.PhaseLabelPicker))
        await pilot.press("space")  # even an edited tick is discarded
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_l_without_configured_labels_only_notifies(monkeypatch):
    monkeypatch.setattr(board.Config, "load",
                        classmethod(lambda cls, path=None: board.Config(
                            site="https://example.atlassian.net", email="you@example.com",
                            api_token="t", jql="jql")))
    monkeypatch.setattr(board.Jira, "search", lambda self: [issue("KAN-1")])
    monkeypatch.setattr(board, "agent_statuses", lambda: {})
    app = board.BoardApp()
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        await pilot.press("l")
        await pilot.pause()
        assert not isinstance(app.screen, board.PhaseLabelPicker)
