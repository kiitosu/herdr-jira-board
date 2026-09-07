"""exclude_labels: cards hidden by label, countable in the title, revealed by `h`."""

import json

import pytest

import board


BASE = 'site = "https://example.atlassian.net"\nemail = "you@example.com"\n'


def issue(key, labels=(), category="new", status="To Do"):
    return board.Issue(key=key, summary="", status=status, category=category,
                       issuetype="Task", labels=list(labels))


# ---- config

def test_config_loads_exclude_labels(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(BASE + 'api_token = "t"\nexclude_labels = ["jb_wont_do"]\n')
    assert board.Config.load(p).exclude_labels == ["jb_wont_do"]


def test_exclude_labels_defaults_to_empty(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(BASE + 'api_token = "t"\n')
    assert board.Config.load(p).exclude_labels == []


# ---- the predicate

def test_hidden_by_label_matches_case_insensitively():
    assert board.hidden_by_label(issue("X-1", ["JB_Wont_Do"]), ["jb_wont_do"])
    assert not board.hidden_by_label(issue("X-2", ["jb_other"]), ["jb_wont_do"])
    assert not board.hidden_by_label(issue("X-3"), ["jb_wont_do"])


# ---- the dump

CFG = board.Config(site="https://example.atlassian.net", email="e", api_token="t",
                   jql="jql", exclude_labels=["jb_wont_do"])
ISSUES = [issue("X-1"), issue("X-2", ["jb_wont_do"])]


def test_dump_text_counts_the_hidden_instead_of_listing_them():
    out = board.dump_text(CFG, ISSUES, {}, {})
    assert "exclude_labels: ['jb_wont_do']" in out
    assert "== To Do (1, hidden 1) ==" in out
    assert "X-1 " in out
    assert "X-2" not in out


def test_dump_json_keeps_the_hidden_with_a_flag():
    data = json.loads(board.dump_json(CFG, ISSUES, {}, {}))
    assert data["exclude_labels"] == ["jb_wont_do"]
    (it1, it2) = data["columns"][0]["issues"]
    assert (it1["key"], it1["hidden"]) == ("X-1", False)
    assert (it2["key"], it2["hidden"]) == ("X-2", True)


# ---- the board

@pytest.fixture
def app(monkeypatch):
    cfg = board.Config(site="https://example.atlassian.net", email="you@example.com",
                       api_token="t", jql="jql", exclude_labels=["jb_wont_do"])
    monkeypatch.setattr(board.Config, "load", classmethod(lambda cls, path=None: cfg))
    monkeypatch.setattr(board.Jira, "search", lambda self: list(ISSUES))
    monkeypatch.setattr(board, "agent_statuses", lambda: {})
    return board.BoardApp()


async def wait_for(pilot, predicate):
    for _ in range(50):
        if predicate():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition never became true")


def keys_on_board(app):
    return [c.issue.key for c in app.query(board.Card)]


@pytest.mark.asyncio
async def test_h_reveals_the_hidden_cards_dimmed_and_hides_them_again(app):
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: keys_on_board(app) == ["X-1"])
        column = next(c for c in app.query(board.Column) if c.category == "new")
        assert board.t("hidden_count", n=1) in str(column.border_title)

        await pilot.press("h")
        await wait_for(pilot, lambda: keys_on_board(app) == ["X-1", "X-2"])
        revealed = next(c for c in app.query(board.Card) if c.issue.key == "X-2")
        assert revealed.has_class("hidden-card")
        assert board.t("hidden_count", n=1) in str(column.border_title)

        await pilot.press("h")
        await wait_for(pilot, lambda: keys_on_board(app) == ["X-1"])


@pytest.mark.asyncio
async def test_h_warns_when_nothing_is_configured(app, monkeypatch):
    app.cfg.exclude_labels = []
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: keys_on_board(app) != [])
        await pilot.press("h")
        await pilot.pause()
        assert app.show_hidden is False
