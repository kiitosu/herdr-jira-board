"""The issue's own labels on the card, in Jira's grey, all on or all off with `L`."""

import pytest

import board


def cfg_with(phase_labels=(), exclude_labels=(), status_labels=()):
    return board.Config(site="s", email="e", api_token="t", jql="q",
                        phase_labels=list(phase_labels),
                        exclude_labels=list(exclude_labels),
                        status_labels=list(status_labels))


def issue(labels=()):
    return board.Issue(key="X-1", summary="", status="To Do", category="new",
                       issuetype="Task", labels=list(labels))


# ---- which labels show

def test_the_issues_own_labels_show_in_jiras_order():
    got = board.issue_labels_of(issue(["MINT_Ph2", "航空キュー"]), cfg_with())
    assert got == ["MINT_Ph2", "航空キュー"]


def test_a_phase_label_is_left_out():
    """It is already drawn in its own colour; twice would read as two things."""
    verify = board.PhaseLabel("jb_verifying", "verifying")
    got = board.issue_labels_of(issue(["jb_verifying", "MINT_Ph2"]),
                                cfg_with(phase_labels=[verify]))
    assert got == ["MINT_Ph2"]


def test_an_exclude_label_is_left_out():
    """That label is why the card is off the board; it is not news on the card."""
    got = board.issue_labels_of(issue(["jb_wont_do", "MINT_Ph2"]),
                                cfg_with(exclude_labels=["jb_wont_do"]))
    assert got == ["MINT_Ph2"]


def test_a_label_a_status_rule_drives_is_left_out():
    rule = board.StatusLabelRule("Done", add=["jb_verifying"])
    got = board.issue_labels_of(issue(["jb_verifying", "RLC_Ph.2"]),
                                cfg_with(status_labels=[rule]))
    assert got == ["RLC_Ph.2"]


def test_an_issue_without_labels_gets_none():
    assert board.issue_labels_of(issue(), cfg_with()) == []


# ---- how they are drawn

CHIP = ("MINT_Ph2", board.LABEL_STYLES[0])


def test_the_card_draws_them_as_chips():
    content = board.Card(issue(["MINT_Ph2"]), labels=[CHIP]).render()
    # Padded, so the chip reads as one against the summary beside it.
    assert " MINT_Ph2 " in str(content)


def test_the_chip_wears_the_colour_it_was_given():
    content = board.Card(issue(["MINT_Ph2"]), labels=[CHIP]).render()
    assert any(span.style == board.LABEL_STYLES[0] for span in content.spans)


def test_a_card_given_no_labels_draws_none():
    content = board.Card(issue(["MINT_Ph2"]), labels=[]).render()
    assert "MINT_Ph2" not in str(content)


# ---- handing out the colours

def test_the_labels_in_use_come_out_in_different_colours():
    names = ["MINT_Ph2", "RLC_Ph.2", "航空キュー", "Claude内部検知"]
    assert len(set(board.label_styles(names).values())) == len(names)


def test_the_same_set_of_labels_is_dealt_the_same_colours():
    """Sorted, so a refresh that finds the same labels does not reshuffle them."""
    first = board.label_styles(["RLC_Ph.2", "MINT_Ph2"])
    assert board.label_styles(["MINT_Ph2", "RLC_Ph.2", "MINT_Ph2"]) == first


def test_more_labels_than_colours_wrap_round():
    names = [f"L{i}" for i in range(len(board.LABEL_STYLES) + 2)]
    styles = board.label_styles(names)
    assert set(styles.values()) == set(board.LABEL_STYLES)


# ---- the `L` switch

ISSUES = [board.Issue(key="X-1", summary="first", status="To Do", category="new",
                      issuetype="Task", labels=["MINT_Ph2"])]


@pytest.fixture
def app(monkeypatch):
    cfg = board.Config(site="https://example.atlassian.net", email="you@example.com",
                       api_token="t", jql="jql")
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


def card_text(app):
    return str(next(iter(app.query(board.Card))).render())


@pytest.mark.asyncio
async def test_labels_start_on_and_L_takes_them_all_off_and_back(app):
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: list(app.query(board.Card)))
        assert "MINT_Ph2" in card_text(app)

        await pilot.press("L")
        await wait_for(pilot, lambda: "MINT_Ph2" not in card_text(app))
        assert app.show_labels is False

        await pilot.press("L")
        await wait_for(pilot, lambda: "MINT_Ph2" in card_text(app))
        assert app.show_labels is True


@pytest.mark.asyncio
async def test_question_mark_opens_the_key_list_and_closes_it_again(app):
    """The footer is one line; on a narrow board the panel is the whole list."""
    from textual.widgets import HelpPanel

    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: list(app.query(board.Card)))
        assert not app.query(HelpPanel)

        await pilot.press("question_mark")
        await wait_for(pilot, lambda: list(app.query(HelpPanel)))

        await pilot.press("question_mark")
        await wait_for(pilot, lambda: not app.query(HelpPanel))


@pytest.mark.asyncio
async def test_shift_arrows_slide_a_footer_that_does_not_fit(app):
    async with app.run_test(size=(40, 20)) as pilot:
        await wait_for(pilot, lambda: list(app.query(board.Card)))
        footer = app.query_one(board.BoardFooter)
        assert footer.max_scroll_x > 0, "the footer should not fit at 40 columns"

        await pilot.press("shift+right")
        await wait_for(pilot, lambda: footer.scroll_x > 0)

        await pilot.press("shift+left")
        await wait_for(pilot, lambda: footer.scroll_x == 0)


@pytest.mark.asyncio
async def test_the_arrows_beside_the_footer_slide_it(app):
    async with app.run_test(size=(40, 20)) as pilot:
        await wait_for(pilot, lambda: list(app.query(board.Card)))
        footer = app.query_one(board.BoardFooter)
        assert footer.max_scroll_x > 0, "the footer should not fit at 40 columns"

        await pilot.click("#footer-right")
        await wait_for(pilot, lambda: footer.scroll_x > 0)

        await pilot.click("#footer-left")
        await wait_for(pilot, lambda: footer.scroll_x == 0)


def wheel(footer, event_type):
    return event_type(widget=footer, x=1, y=0, delta_x=0, delta_y=1,
                      button=0, shift=False, meta=False, ctrl=False)


@pytest.mark.asyncio
async def test_the_wheel_over_the_footer_slides_it_too(app):
    """What someone tries before they look for a key."""
    from textual import events

    async with app.run_test(size=(40, 20)) as pilot:
        await wait_for(pilot, lambda: list(app.query(board.Card)))
        footer = app.query_one(board.BoardFooter)

        footer.post_message(wheel(footer, events.MouseScrollDown))
        await wait_for(pilot, lambda: footer.scroll_x > 0)

        footer.post_message(wheel(footer, events.MouseScrollUp))
        await wait_for(pilot, lambda: footer.scroll_x == 0)
