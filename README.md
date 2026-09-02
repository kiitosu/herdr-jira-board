# herdr-jira-board

A [herdr](https://herdr.dev) plugin that shows your Jira board as a kanban TUI
inside herdr, and launches a [Claude Code](https://claude.com/claude-code)
session for any card — with live session status badges on the board.

日本語版は [README.ja.md](README.ja.md) を参照してください。

![demo](demo/demo.gif)

## Features

- Issues fetched by JQL, shown in three status-category columns
  (To Do / In Progress / Done) — works across projects with custom workflows.
  By default the board shows your open issues plus issues completed within
  the last 7 days (older Done cards drop off automatically; customizable
  via the `jql` config option)
- Move cards with drag & drop or `←` `→` keys, then confirm with `Enter`
  to run the Jira transition (a picker appears when several transitions apply).
  Staged moves pile up, so several cards can be moved one at a time and
  confirmed together. `t` changes a card's status through any transition,
  including those that stay in the same column (e.g. In Progress → In Review)
- `Enter` on a card launches a Claude Code session for that issue in a new
  herdr tab, injecting `JIRA_ISSUE_KEY` and an initial prompt with the issue
  summary, status, due date, description and URL. The board's companion
  session (`c`) hands its transcript
  over, so the new session picks up what you already discussed
  ([details](#the-companion-session))
- Focusing a card previews its session's **last reply** at the bottom of the
  board, read straight from the transcript — nothing is sent to the session,
  and you don't have to keep the state of every parallel issue in your head
  ([details](#the-session-preview)). `Enter` on a card that already has a
  session goes to its tab; `p` turns the preview off when the columns alone
  are what you want (`preview` config option for the starting state)
- A column that mixes several statuses (typically In Progress) groups its
  cards per status under a divider; the order is configurable
  (`status_order`)
- **Phase labels** (`l`) mark a stage that does not deserve a workflow status
  of its own — "verifying the effect after release", say. A Jira label is
  site-wide, so one name works across every project without touching a
  workflow; labelled cards show the label and sort to the top of their status
  group ([details](#phase-labels))
- **Board priority** (`board_priority`) ranks cards by their parent epic and
  Jira priority through an ordered rule list — an ST blocker above a UAT
  blocker above any other ST issue. Ranked cards show `P1`/`P2`/… and sort to
  the top of their status group ([details](#board-priority))
- **Status-linked labels** (`status_labels`) add and shed labels on their own
  when a transition run from the board lands on a status — put "needs
  verifying" on whatever reaches review, shed the board's other labels on
  Done ([details](#status-linked-labels))
- Session status badges (working / blocked / idle / done) on each card,
  refreshed every 5 seconds via `herdr agent list`
- Each card shows its created date and due date; overdue is red, due within
  3 days yellow
- `bin/jira-board --dump` prints the same board as text (or JSON) without the
  TUI, and an optional Claude Code skill lets Claude read it — see
  [Reading the board from Claude](#reading-the-board-from-claude)
- Tab utilities: actions to close other tabs / tabs to the right
- UI in English or Japanese — follows your system locale, can be overridden

## Requirements

- herdr >= 0.7.5 (macOS / Linux)
- Python 3.11+ **or** [uv](https://docs.astral.sh/uv/)
- A Jira Cloud account and an API token

## Install

```
herdr plugin install kiitosu/herdr-jira-board
```

That's it — the install step prepares the Python environment automatically
(uses uv when available, otherwise creates a private virtualenv).

## Configuration

1. Create an API token at
   https://id.atlassian.com/manage-profile/security/api-tokens
   (choose the classic "Create API token", not "with scopes").
2. Copy [config.toml.example](config.toml.example) to the plugin config
   directory and edit it:

```
cp config.toml.example "$(herdr plugin config-dir jira-board)/config.toml"
```

Minimal config:

```toml
site = "https://your-site.atlassian.net"
email = "you@example.com"
api_token = "<your API token>"
```

See the comments in `config.toml.example` for all options
(`api_token_cmd`, `jql`, `exclude_statuses`, `status_order`, `phase_labels`,
`board_priority`, `status_labels`, `language`, `preview`, `[project_dirs]`).

## Usage

Open the board from inside herdr:

```
herdr plugin pane open --plugin jira-board --entrypoint board
```

Recommended: bind it to a key in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+k"
type = "plugin_action"
command = "jira-board.open-board"
description = "Open Jira board"

[[keys.command]]
key = "prefix+x"
type = "plugin_action"
command = "jira-board.close-right-tabs"
description = "Close tabs to the right"

[[keys.command]]
key = "prefix+shift+x"
type = "plugin_action"
command = "jira-board.close-other-tabs"
description = "Close other tabs"
```

### Keys

| Key | Action |
| --- | --- |
| `↑` `↓` | Focus previous / next card |
| `←` `→` | Stage a move to the adjacent column |
| `Enter` | Confirm every staged move, or launch a Claude session for the card (go to its tab when it is already running) |
| `Esc` | Cancel every staged move / unfocus |
| `r` | Refresh the board |
| `o` | Open the issue in the browser |
| `t` | Change the card's status (picker with every transition, same-column ones included) |
| `l` | Edit the card's phase labels (space ticks, enter applies) |
| `c` | Open the companion session beside the board, or jump to it |
| `p` | Turn the session preview off / on |
| `q` | Quit |

Cards can also be dragged between columns with the mouse; drops are staged the
same way and confirmed with `Enter`.

Staging is per card and cumulative: stage as many cards as you like — each one
to its own column — and `Enter` runs them all, one after another. A card whose
move needs a transition picker asks for it when its turn comes; a card whose
transition fails goes back to its original column and the rest still run.
`←` `→` back to a card's own column takes that single card out of the batch.

### Phase labels

Some stages are not worth a workflow status: "the MR is merged, now watch
whether it actually helped" is one. Adding a status for it means editing every
project's workflow, and that is a Jira admin's job — while a Jira label is
shared across the whole site, so one name covers every project and anyone can
put it on.

Declare the labels you want the board to manage:

```toml
[[phase_labels]]
label = "jb_verifying"     # the label as it exists in Jira
display = "verifying"      # what the card shows

[[phase_labels]]
label = "jb_waiting"
display = "waiting"
```

`l` on a card opens a picker of these labels, ticked to match what the card
already carries. `space` ticks and unticks, `enter` applies every change at
once and `esc` throws them away. Only the labels you edited change — labels
other people put on the issue are left alone, and the whole picker's worth of
changes costs a single request.

Cards carrying a phase label sort to the top of their status group, in the
order the labels are declared, and show `display` next to the status. This is
independent of `status_order`: the status groups stay where they are and the
labels only reorder cards inside one of them.

Namespace the labels (`jb_…` above) — Jira labels are shared with every other
project on the site, so a bare `verifying` would show up in everyone's
autocomplete. Only `display` reaches the card, so the prefix costs no width.
A plain string entry is also accepted (`phase_labels = ["verifying"]`) when you
don't need a separate display name.

### Board priority

Which card to pick up next rarely follows one field: it is "a blocker in the
ST epic first, then a UAT blocker, then anything else in ST" — orderings no
single epic-then-priority comparison can express. `board_priority` is an
ordered rule list; a card gets the rank of the first rule it matches:

```toml
[[board_priority]]
epic = "ST test"
priority = "Blocker"

[[board_priority]]
epic = "UAT test"
priority = "Blocker"

[[board_priority]]
epic = "ST test"            # any remaining ST issue, whatever its priority
```

`epic` names the card's parent epic — by key (exact) or by a piece of its
name, so `"ST test"` matches the epic "ST test (bug filing)"; omit it to match
any epic. `priority` is the Jira priority name; omit it for "regardless of
priority". A rule with neither would swallow every card and is ignored.

Ranked cards show a marker — `P1`/`P2`/… by position, or the rule's `display`
— colored red for the top two rungs and yellow for the next two, like the due
dates. Inside a status group they sort by rank, ahead of the phase labels
(the rank answers "which do I pick up", so it wins); cards matching no rule
keep their usual place after the ranked ones. `--dump` prints the marker and
`--dump --json` carries `priority`, `epic` and `board_priority` per issue.

### Status-linked labels

Some label changes should just happen: whatever reaches review needs the
"verify the effect later" label, and a card leaving the board through Done
should drop the board's bookkeeping labels. `status_labels` declares those
changes, and the board applies them whenever a transition it runs lands on
the named status:

```toml
[[status_labels]]
status = "In Review"
add = ["jb_verifying"]

[[status_labels]]
status = "Done"
remove_except = ["jb_verifying"]
```

`add` puts labels on. `remove` takes the listed ones off. `remove_except` is
the "everything but" form: it takes off every label the config mentions —
the `phase_labels` declarations plus the labels in `status_labels` rules —
other than the listed ones. Labels other people put on the issue are never
touched. Statuses compare case-insensitively, changes for one issue land in
a single request, and the labels involved don't have to be phase labels
(though the card only displays the ones that are).

Two contradictions are rejected whole rather than half-applied: a rule
carrying both `remove` and `remove_except`, and a rule adding and removing
the same label. Across the rules for one status, an added label is never
removed.

The rules fire only for transitions run from the board (`←` `→` + `Enter`,
or `t`). A status changed in the Jira web UI doesn't trigger them — rules
that must hold everywhere are what Jira's own automation is for.

### The companion session

`c` opens a Claude Code session in a pane beside the board — the one you think
out loud in, while the sessions launched from cards do the implementation. Press
`c` again to jump to it.

The board records its Claude session id, so once its pane (or the board itself)
is closed, `c` brings the same conversation back through
`claude --resume <session-id>`. A session Claude no longer knows — one that was
closed before anything was said in it, for instance — is replaced by a fresh one.

### Handing over to a launched session

When you launch a session with `Enter`, the board appends the transcript paths of
the sessions it is linked to — the companion wherever it sits, plus any other
Claude pane in the board's own herdr tab (found through `HERDR_TAB_ID` and
`herdr pane list`) — to the initial prompt. The new session is told to grep those
transcripts for the issue key and read only what matches, so what you already
worked out carries over instead of being retyped.

Transcripts are located by session id under
`${CLAUDE_CONFIG_DIR:-~/.claude}/projects/*/<session-id>.jsonl`. A session that
has no transcript yet (nothing said in it) is skipped, and with nothing linked
the prompt is unchanged.

### The session preview

Focusing a card that has a session previews that session's **last reply** at
the bottom of the board. The point is to keep the state of parallel issues out
of your head: coming back to a card shows you where it stands.

The reply is read straight from the transcript
(`${CLAUDE_CONFIG_DIR:-~/.claude}/projects/*/<session-id>.jsonl`). **Nothing is
sent to the session**, so checking on it never costs it a turn and never
interrupts work in progress. The preview refreshes on the same 5-second tick as
the status badges, so a working session's latest report keeps up.

Cards without a session, and sessions with no reply yet, show no preview.

`p` turns the preview off, leaving the board to its columns alone, and on
again. Set `preview = false` in the config to have it start off.

## Reading the board from Claude

`--dump` prints the board as text instead of opening the TUI, using the same
config, JQL, exclusions and columns:

```
bin/jira-board --dump          # text
bin/jira-board --dump --json   # machine-readable
```

This is what makes the board readable by a Claude Code session — asking Jira
for the whole board over MCP returns every issue description, which quickly
exceeds what fits in a reply.

A Claude Code skill that wraps it ships in `skills/jira-board`. It is **not**
installed by default; opt in by setting an environment variable, which copies
the skill into `~/.claude/skills` (honouring `CLAUDE_CONFIG_DIR`):

```
HERDR_JIRA_BOARD_INSTALL_SKILL=1 herdr plugin install kiitosu/herdr-jira-board
# already installed? just re-run the build step:
HERDR_JIRA_BOARD_INSTALL_SKILL=1 bin/setup
```

Claude then picks it up when you ask about "the board", and runs the dump for
you. Nothing is written outside `~/.claude/skills/jira-board`, and a directory
already at that path is left alone unless this plugin installed it.

The copy finds the plugin itself, so it keeps working across upgrades. If you
keep your plugins somewhere unusual, point it at the plugin explicitly with
`HERDR_JIRA_BOARD_ROOT`.

## Development

```
git clone https://github.com/kiitosu/herdr-jira-board
herdr plugin link herdr-jira-board   # edits take effect immediately
```

Check the config without opening the TUI: `bin/jira-board --check`  
Print the board without opening the TUI: `bin/jira-board --dump`

Run tests:

```
uv run --with "textual>=0.80" --with "httpx>=0.27" --with pytest --with pytest-asyncio -m pytest tests/
```

## License

[MIT](LICENSE)
