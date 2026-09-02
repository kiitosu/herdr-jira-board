"""Board priority: an ordered rule list ranks cards by epic and Jira priority."""

import pytest

import board
from board import BoardPriorityRule, Config, PhaseLabel


BASE = 'site = "https://example.atlassian.net/"\nemail = "you@example.com"\n'


def issue(key, epic_key="", epic_name="", priority="通常", labels=()):
    return board.Issue(key=key, summary="", status="進行中", category="indeterminate",
                       issuetype="Task", labels=list(labels),
                       priority=priority, epic_key=epic_key, epic_name=epic_name)


def cfg_with(rules, phase_labels=()):
    return Config(site="s", email="e", api_token="t", jql="q",
                  phase_labels=list(phase_labels), board_priority=rules)


# The ladder this feature was built for: an ST blocker beats a UAT blocker
# beats any other ST issue beats a severe UAT issue beats the rest of UAT.
LADDER = [
    BoardPriorityRule(epic="STテスト", priority="ブロッカー"),
    BoardPriorityRule(epic="UATテスト", priority="ブロッカー"),
    BoardPriorityRule(epic="STテスト"),
    BoardPriorityRule(epic="UATテスト", priority="重度"),
    BoardPriorityRule(epic="UATテスト"),
]

ST = dict(epic_key="PROJ-101", epic_name="STテスト（不具合起票）")
UAT = dict(epic_key="PROJ-102", epic_name="UATテスト（不具合起票）")


# ---- config

def test_load_parses_rules(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(BASE + 'api_token = "t"\n'
                 '[[board_priority]]\nepic = "ST試験"\npriority = "ブロッカー"\n'
                 '[[board_priority]]\nepic = "UAT試験"\ndisplay = "UAT"\n'
                 '[[board_priority]]\npriority = "ブロッカー"\n')
    assert board.Config.load(p).board_priority == [
        BoardPriorityRule("ST試験", "ブロッカー", ""),
        BoardPriorityRule("UAT試験", "", "UAT"),
        BoardPriorityRule("", "ブロッカー", ""),
    ]


def test_parse_drops_a_rule_that_would_match_everything():
    assert BoardPriorityRule.parse({}) is None
    assert BoardPriorityRule.parse({"display": "X"}) is None
    assert BoardPriorityRule.parse("ST試験") is None


# ---- matching

def test_epic_matches_the_key_exactly_or_the_name_by_substring():
    rule = BoardPriorityRule(epic="STテスト")
    assert rule.matches(issue("X-1", **ST))
    assert not rule.matches(issue("X-2", **UAT))
    by_key = BoardPriorityRule(epic="proj-101")
    assert by_key.matches(issue("X-1", **ST))


def test_a_key_like_epic_does_not_substring_match_other_keys():
    """"PROJ-10" must not catch the epic PROJ-101 by accident."""
    rule = BoardPriorityRule(epic="PROJ-10")
    assert not rule.matches(issue("X-1", **ST))


def test_priority_matches_case_insensitively_and_alone():
    rule = BoardPriorityRule(priority="ブロッカー")
    assert rule.matches(issue("X-1", **ST, priority="ブロッカー"))
    assert rule.matches(issue("X-2", priority="ブロッカー"))  # any epic
    assert not rule.matches(issue("X-3", **ST))


def test_the_users_ladder_ranks_as_specified():
    cfg = cfg_with(LADDER)
    cases = [
        (issue("X-1", **ST, priority="ブロッカー"), 0),
        (issue("X-2", **UAT, priority="ブロッカー"), 1),
        (issue("X-3", **ST, priority="通常"), 2),
        (issue("X-4", **ST, priority="重度"), 2),   # "regardless of priority"
        (issue("X-5", **UAT, priority="重度"), 3),
        (issue("X-6", **UAT, priority="通常"), 4),
        (issue("X-7", **UAT, priority="最低"), 4),  # "the rest of UAT"
        (issue("X-8", epic_name="別プロジェクト", priority="ブロッカー"), None),
        (issue("X-9"), None),
    ]
    for it, expected in cases:
        assert board.board_priority_rank(cfg, it) == expected, it.key


# ---- the tag the card shows

def test_tag_defaults_to_the_rank_and_escalates_like_due_dates():
    cfg = cfg_with(LADDER)
    assert board.board_priority_tag(cfg, issue("X-1", **ST, priority="ブロッカー")) == "[red]P1[/]"
    assert board.board_priority_tag(cfg, issue("X-2", **UAT, priority="重度")) == "[yellow]P4[/]"
    assert board.board_priority_tag(cfg, issue("X-3", **UAT)) == "[dim]P5[/]"
    assert board.board_priority_tag(cfg, issue("X-4")) == ""


def test_tag_prefers_the_rules_display_name():
    cfg = cfg_with([BoardPriorityRule(epic="STテスト", display="ST🔥")])
    assert board.board_priority_tag(cfg, issue("X-1", **ST)) == "[red]ST🔥[/]"


# ---- sorting

def test_cards_sort_by_the_ladder_then_keep_the_jql_order():
    cfg = cfg_with(LADDER)
    issues = [issue("X-1"), issue("X-2", **UAT), issue("X-3", **ST, priority="通常"),
              issue("X-4", **UAT, priority="ブロッカー"), issue("X-5")]
    assert [i.key for i in board.sort_cards(issues, cfg)] == [
        "X-4", "X-3", "X-2", "X-1", "X-5"]


def test_board_priority_outranks_the_phase_labels():
    verify = PhaseLabel("jb_効果確認中", "効果確認中")
    cfg = cfg_with([BoardPriorityRule(epic="STテスト")], phase_labels=[verify])
    issues = [issue("X-1", labels=["jb_効果確認中"]), issue("X-2", **ST)]
    assert [i.key for i in board.sort_cards(issues, cfg)] == ["X-2", "X-1"]


def test_phase_labels_break_ties_inside_one_rank():
    verify = PhaseLabel("jb_効果確認中", "効果確認中")
    cfg = cfg_with([BoardPriorityRule(epic="STテスト")], phase_labels=[verify])
    issues = [issue("X-1", **ST), issue("X-2", **ST, labels=["jb_効果確認中"])]
    assert [i.key for i in board.sort_cards(issues, cfg)] == ["X-2", "X-1"]


# ---- the card

@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(board.Config, "load",
                        classmethod(lambda cls, path=None: cfg_with(list(LADDER))))
    monkeypatch.setattr(board.Jira, "search",
                        lambda self: [issue("KAN-1", **ST, priority="ブロッカー"),
                                      issue("KAN-2", **UAT, priority="通常"),
                                      issue("KAN-3")])
    monkeypatch.setattr(board, "agent_statuses", lambda: {})
    return board.BoardApp()


@pytest.mark.asyncio
async def test_cards_wear_their_rank_and_sort_by_it(app):
    async with app.run_test() as pilot:
        for _ in range(50):
            if list(app.query(board.Card)):
                break
            await pilot.pause(0.05)
        cards = list(app.query(board.Card))
        assert [c.issue.key for c in cards] == ["KAN-1", "KAN-2", "KAN-3"]
        assert "P1" in str(cards[0].render())
        assert "P5" in str(cards[1].render())
        assert cards[2].priority_tag == ""


# ---- the dump

def test_dump_carries_the_board_priority():
    import json

    cfg = cfg_with(list(LADDER))
    issues = [issue("X-1", **ST, priority="ブロッカー"), issue("X-2")]
    text = board.dump_text(cfg, issues, {}, {})
    assert "X-1 P1 [進行中]" in text
    assert "X-2 [進行中]" in text
    data = json.loads(board.dump_json(cfg, issues, {}, {}))
    (it1, it2) = data["columns"][1]["issues"]
    assert (it1["board_priority"], it1["priority"]) == ("P1", "ブロッカー")
    assert it1["epic"] == {"key": "PROJ-101", "name": "STテスト（不具合起票）"}
    assert (it2["board_priority"], it2["epic"]) == (None, None)


# ---- the search brings the new fields home

class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_search_reads_priority_and_parent(monkeypatch):
    payload = {"issues": [{
        "key": "X-1",
        "fields": {"summary": "s", "status": {"name": "進行中",
                                              "statusCategory": {"key": "indeterminate"}},
                   "priority": {"name": "重度"},
                   "parent": {"key": "PROJ-101",
                              "fields": {"summary": "STテスト（不具合起票）"}}},
    }]}
    sent = {}

    def fake_post(self, url, json):
        sent.update(url=url, body=json)
        return FakeResponse(payload)

    monkeypatch.setattr(board.httpx.Client, "post", fake_post)
    cfg = Config(site="https://example.atlassian.net", email="e", api_token="t", jql="j")
    (it,) = board.Jira(cfg).search()
    assert "priority" in sent["body"]["fields"]
    assert "parent" in sent["body"]["fields"]
    assert (it.priority, it.epic_key, it.epic_name) == (
        "重度", "PROJ-101", "STテスト（不具合起票）")


def test_search_survives_an_issue_without_priority_or_parent(monkeypatch):
    payload = {"issues": [{
        "key": "X-1",
        "fields": {"summary": "s", "status": {"name": "To Do",
                                              "statusCategory": {"key": "new"}}},
    }]}
    monkeypatch.setattr(board.httpx.Client, "post",
                        lambda self, url, json: FakeResponse(payload))
    cfg = Config(site="https://example.atlassian.net", email="e", api_token="t", jql="j")
    (it,) = board.Jira(cfg).search()
    assert (it.priority, it.epic_key, it.epic_name) == ("", "", "")
