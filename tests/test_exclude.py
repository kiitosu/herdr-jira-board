import board


def issue(key, status, category, issuetype="Task"):
    return board.Issue(key=key, summary="", status=status,
                       category=category, issuetype=issuetype)


ISSUES = [
    issue("X-1", "To Do", "new"),
    issue("X-2", "解決済み", "indeterminate"),
    issue("X-3", "Resolved", "indeterminate"),
    issue("X-4", "完了", "done"),
]


def test_no_exclusions():
    assert board.exclude_by_status(ISSUES, []) == ISSUES


def test_drops_matching_status():
    got = board.exclude_by_status(ISSUES, ["解決済み"])
    assert [i.key for i in got] == ["X-1", "X-3", "X-4"]


def test_match_ignores_case():
    got = board.exclude_by_status(ISSUES, ["resolved"])
    assert [i.key for i in got] == ["X-1", "X-2", "X-4"]


def test_unknown_status_keeps_everything():
    got = board.exclude_by_status(ISSUES, ["Bogus"])
    assert [i.key for i in got] == ["X-1", "X-2", "X-3", "X-4"]


def test_multiple_exclusions():
    got = board.exclude_by_status(ISSUES, ["解決済み", "完了"])
    assert [i.key for i in got] == ["X-1", "X-3"]


TYPED = [
    issue("Y-1", "To Do", "new", issuetype="エピック"),
    issue("Y-2", "To Do", "new", issuetype="Task"),
    issue("Y-3", "To Do", "new", issuetype="Epic"),
]


def test_no_issuetype_exclusions():
    assert board.exclude_by_issuetype(TYPED, []) == TYPED


def test_drops_matching_issuetype_ignoring_case():
    got = board.exclude_by_issuetype(TYPED, ["エピック", "epic"])
    assert [i.key for i in got] == ["Y-2"]


def test_config_loads_exclude_issuetypes(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('site = "https://example.atlassian.net"\nemail = "you@example.com"\n'
                 'api_token = "t"\nexclude_issuetypes = ["Epic"]\n')
    assert board.Config.load(p).exclude_issuetypes == ["Epic"]


def test_exclude_issuetypes_defaults_to_empty(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('site = "https://example.atlassian.net"\nemail = "you@example.com"\n'
                 'api_token = "t"\n')
    assert board.Config.load(p).exclude_issuetypes == []
