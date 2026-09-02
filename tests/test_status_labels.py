import board
from board import Config, PhaseLabel, StatusLabelRule


BASE = 'site = "https://example.atlassian.net/"\nemail = "you@example.com"\n'


def cfg_with(rules, phase=("jb_a", "jb_b", "jb_c")):
    return Config(site="s", email="e", api_token="t", jql="q",
                  phase_labels=[PhaseLabel(n, n) for n in phase],
                  status_labels=rules)


# ---- parsing

def test_load_parses_both_rule_forms(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(BASE + 'api_token = "t"\n'
                 '[[status_labels]]\nstatus = "レビュー中"\nadd = ["jb_x"]\n'
                 '[[status_labels]]\nstatus = "完了"\nremove_except = ["jb_x"]\n')
    cfg = board.Config.load(p)
    assert cfg.status_labels == [
        StatusLabelRule("レビュー中", ["jb_x"], [], None),
        StatusLabelRule("完了", [], [], ["jb_x"]),
    ]


def test_parse_drops_unusable_rules():
    assert StatusLabelRule.parse("just a string") is None
    assert StatusLabelRule.parse({"add": ["jb_a"]}) is None  # no status
    # both removal forms at once is contradictory
    assert StatusLabelRule.parse(
        {"status": "x", "remove": ["jb_a"], "remove_except": ["jb_b"]}) is None
    # adding and removing the same label is contradictory
    assert StatusLabelRule.parse({"status": "x", "add": ["jb_a"], "remove": ["jb_a"]}) is None


def test_parse_accepts_an_empty_except_list():
    """remove_except = [] means: take off every managed label."""
    rule = StatusLabelRule.parse({"status": "x", "remove_except": []})
    assert rule == StatusLabelRule("x", [], [], [])


# ---- the managed universe

def test_managed_labels_is_everything_the_config_mentions():
    cfg = cfg_with([StatusLabelRule("s1", add=["jb_d"], remove=["jb_e"]),
                    StatusLabelRule("s2", remove_except=["jb_f"])])
    assert board.managed_labels(cfg) == {"jb_a", "jb_b", "jb_c", "jb_d", "jb_e", "jb_f"}


# ---- the changes a transition triggers

def test_add_rule_adds_only_whats_missing():
    cfg = cfg_with([StatusLabelRule("レビュー中", add=["jb_a"])])
    assert board.status_label_changes(cfg, [], "レビュー中") == (["jb_a"], [])
    assert board.status_label_changes(cfg, ["jb_a"], "レビュー中") == ([], [])


def test_statuses_match_case_insensitively():
    cfg = cfg_with([StatusLabelRule("In Review", add=["jb_a"])])
    assert board.status_label_changes(cfg, [], "in review") == (["jb_a"], [])


def test_no_rule_for_the_status_changes_nothing():
    cfg = cfg_with([StatusLabelRule("完了", remove_except=[])])
    assert board.status_label_changes(cfg, ["jb_a"], "進行中") == ([], [])


def test_remove_takes_off_only_whats_present():
    cfg = cfg_with([StatusLabelRule("進行中", remove=["jb_a", "jb_b"])])
    assert board.status_label_changes(cfg, ["jb_b", "other"], "進行中") == ([], ["jb_b"])


def test_remove_except_keeps_the_listed_and_the_unmanaged():
    cfg = cfg_with([StatusLabelRule("完了", remove_except=["jb_a"])])
    add, remove = board.status_label_changes(
        cfg, ["jb_a", "jb_b", "jb_c", "someone_elses"], "完了")
    assert add == []
    assert remove == ["jb_b", "jb_c"]  # jb_a kept, someone_elses not ours to touch


def test_a_rule_never_removes_what_it_adds():
    cfg = cfg_with([StatusLabelRule("完了", add=["jb_b"], remove_except=[])])
    add, remove = board.status_label_changes(cfg, ["jb_a", "jb_c"], "完了")
    assert add == ["jb_b"]
    assert remove == ["jb_a", "jb_c"]


def test_an_added_label_wins_across_matching_rules():
    cfg = cfg_with([StatusLabelRule("完了", add=["jb_a"]),
                    StatusLabelRule("完了", remove=["jb_a", "jb_b"])])
    assert board.status_label_changes(cfg, ["jb_b"], "完了") == (["jb_a"], ["jb_b"])
