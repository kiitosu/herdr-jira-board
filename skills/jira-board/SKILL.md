---
name: jira-board
description: Read the Jira board — the same issues the herdr jira-board kanban shows, in three columns (To Do / In Progress / Done) with created and due dates and Claude session badges. Use whenever the user asks about "the board", "ボードの状況", "看板", their Jira tickets, what is in progress, what is overdue, or which ticket to pick up next. Use it before discussing or triaging their tickets so the discussion starts from the current board.
---

# Jira board

## Read the board

```
~/.claude/skills/jira-board/dump
```

One command, no TUI. It uses the plugin's own `config.toml`, so the JQL, the
`exclude_statuses` filter and the three-column split are identical to what the
user sees on the kanban.

Machine-readable form when you need to compute over it:

```
~/.claude/skills/jira-board/dump --json
```

Output per issue: key, status, issue type, created date, due date, summary and
browse URL. `<working>` / `<blocked>` / `<idle>` / `<done>` after the status
means a Claude session launched from the board is attached to that issue.
Badges are absent when run outside herdr — that means "unknown", not "no
session".

## Do not list the board through the Jira MCP

`mcp__jira-mcp__searchJiraIssuesUsingJql` returns each issue's full description
even when `fields` excludes it. For this board that is a 50,000+ character
response — it will not fit. Always use `dump` for the board as a whole.

## Going deeper on one issue

Once the user picks an issue from the board, use the MCP tools per issue key:

- `mcp__jira-mcp__getJiraIssue` — description, comments, links
- `mcp__jira-mcp__transitionJiraIssue` — move it (or tell the user to do it on
  the board with `←` / `→` then `Enter`, which is usually faster for them)

## Notes

- The board only shows issues assigned to the user: open ones, plus ones
  completed in the last 7 days.
- A status listed in `exclude_statuses` (in the plugin's `config.toml`) never
  appears, and neither does an issue type listed in `exclude_issuetypes`. The
  dump prints both lists in its header, so say so explicitly if a ticket the
  user expects is filtered out.
- Issues carrying an `exclude_labels` label ("won't do", say) are left out of
  the text dump, but a column that has them says so — `== To Do (7, hidden 2)
  ==`. `--json` includes them with `"hidden": true` when the user asks what
  is hidden.
- Due dates: overdue and due-soon issues are worth calling out unprompted when
  summarizing the board.
