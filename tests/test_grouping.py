import pytest

import board


def issue(key, status, category="indeterminate"):
    return board.Issue(key=key, summary="", status=status,
                       category=category, issuetype="Task")


IN_PROGRESS = [
    issue("X-1", "進行中"),
    issue("X-2", "レビュー中"),
    issue("X-3", "進行中"),
    issue("X-4", "顧客確認中"),
]


def test_groups_keep_appearance_order_without_config():
    groups = board.group_by_status(IN_PROGRESS, [])
    assert [(s, [i.key for i in g]) for s, g in groups] == [
        ("進行中", ["X-1", "X-3"]),
        ("レビュー中", ["X-2"]),
        ("顧客確認中", ["X-4"]),
    ]


def test_status_order_puts_listed_statuses_first():
    groups = board.group_by_status(IN_PROGRESS, ["レビュー中", "顧客確認中"])
    assert [s for s, _ in groups] == ["レビュー中", "顧客確認中", "進行中"]


def test_status_order_matches_case_insensitively():
    issues = [issue("X-1", "In Review"), issue("X-2", "Doing")]
    groups = board.group_by_status(issues, ["in review"])
    assert [s for s, _ in groups] == ["In Review", "Doing"]


def test_single_status_is_one_group():
    issues = [issue("X-1", "To Do", "new"), issue("X-2", "To Do", "new")]
    assert board.group_by_status(issues, []) == [("To Do", issues)]


def test_config_loads_status_order(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('site = "https://example.atlassian.net"\nemail = "you@example.com"\n'
                 'api_token = "t"\nstatus_order = ["レビュー中", "進行中"]\n')
    cfg = board.Config.load(p)
    assert cfg.status_order == ["レビュー中", "進行中"]


MIXED = [
    issue("KAN-1", "To Do", "new"),
    issue("KAN-2", "進行中"),
    issue("KAN-3", "レビュー中"),
    issue("KAN-4", "進行中"),
]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(board.Config, "load",
                        classmethod(lambda cls, path=None: board.Config(
                            site="https://example.atlassian.net", email="you@example.com",
                            api_token="t", jql="jql",
                            status_order=["レビュー中"])))
    monkeypatch.setattr(board.Jira, "search", lambda self: list(MIXED))
    monkeypatch.setattr(board, "agent_statuses", lambda: {})
    return board.BoardApp()


async def wait_for_cards(app, pilot):
    for _ in range(50):
        if list(app.query(board.Card)):
            return
        await pilot.pause(0.05)
    raise AssertionError("cards never appeared")


@pytest.mark.asyncio
async def test_column_shows_dividers_in_status_order(app):
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        in_progress = next(c for c in app.query(board.Column)
                           if c.category == "indeterminate")
        rows = [(type(w).__name__,
                 w.issue.key if isinstance(w, board.Card) else w.status)
                for w in in_progress.children]
        assert rows == [
            ("StatusDivider", "レビュー中"),
            ("Card", "KAN-3"),
            ("StatusDivider", "進行中"),
            ("Card", "KAN-2"),
            ("Card", "KAN-4"),
        ]


@pytest.mark.asyncio
async def test_single_status_column_has_no_divider(app):
    async with app.run_test() as pilot:
        await wait_for_cards(app, pilot)
        todo = next(c for c in app.query(board.Column) if c.category == "new")
        assert not list(todo.query(board.StatusDivider))
        assert [c.issue.key for c in todo.query(board.Card)] == ["KAN-1"]
