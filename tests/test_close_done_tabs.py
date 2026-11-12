"""`x`: close the tabs whose issue is done, after naming them."""

import board


def tab(tab_id, label="", workspace=None):
    return {"tab_id": tab_id, "label": label,
            "workspace_id": workspace or tab_id.split(":")[0]}


def pane(pane_id, tab_id):
    return {"pane_id": pane_id, "tab_id": tab_id}


def categories(mapping):
    """A category lookup over a plain dict; an unknown key reads as None."""
    return lambda key: mapping.get(key)


# ---- picking the tabs

def test_picks_the_tab_whose_issue_is_done():
    tabs = [tab("w1:t1", "KAN-1 first"), tab("w1:t2", "KAN-2 second")]
    rows = board.done_issue_tabs(tabs, {}, [], categories({"KAN-1": "done",
                                                           "KAN-2": "indeterminate"}))
    assert rows == [("w1:t1", "KAN-1")]


def test_reads_the_issue_from_the_session_record_over_the_label():
    """A renamed tab keeps its issue: the board's record names it."""
    tabs = [tab("w1:t1", "renamed by hand")]
    rows = board.done_issue_tabs(tabs, {"KAN-7": "w1:p3"}, [pane("w1:p3", "w1:t1")],
                                 categories({"KAN-7": "done"}))
    assert rows == [("w1:t1", "KAN-7")]


def test_a_tab_with_no_issue_is_left_alone():
    rows = board.done_issue_tabs([tab("w1:t1", "shell")], {}, [], categories({}))
    assert rows == []


def test_an_issue_that_cannot_be_read_is_left_alone():
    """Closing ends the session inside, so an unreadable issue is not a done one."""
    rows = board.done_issue_tabs([tab("w1:t1", "KAN-1")], {}, [],
                                 categories({"KAN-1": None}))
    assert rows == []


def test_the_board_tab_is_never_closed():
    tabs = [tab("w1:t1", "KAN-1"), tab("w1:t9", "KAN-2")]
    rows = board.done_issue_tabs(tabs, {}, [], categories({"KAN-1": "done",
                                                           "KAN-2": "done"}),
                                 workspace="w1", skip_tab="w1:t9")
    assert rows == [("w1:t1", "KAN-1")]


def test_other_workspaces_are_left_alone():
    tabs = [tab("w1:t1", "KAN-1"), tab("w2:t1", "KAN-2")]
    rows = board.done_issue_tabs(tabs, {}, [], categories({"KAN-1": "done",
                                                           "KAN-2": "done"}),
                                 workspace="w1")
    assert rows == [("w1:t1", "KAN-1")]


# ---- closing them

def test_close_tabs_counts_what_went(monkeypatch):
    closed = []
    monkeypatch.setattr(board, "herdr", lambda *args: closed.append(args[-1]))
    assert board.close_tabs(["w1:t1", "w1:t2"]) == 2
    assert closed == ["w1:t1", "w1:t2"]


def test_one_tab_that_will_not_close_does_not_stop_the_rest(monkeypatch):
    def herdr(*args):
        if args[-1] == "w1:t1":
            raise OSError("gone")
        return {}

    monkeypatch.setattr(board, "herdr", herdr)
    assert board.close_tabs(["w1:t1", "w1:t2"]) == 1
