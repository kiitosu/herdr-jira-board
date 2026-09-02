# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=0.80", "httpx>=0.27"]
# ///
"""herdr-jira-board: Jira kanban board TUI.

- Three status-category columns (To Do / In Progress / Done); inside a group,
  cards rank by the config's priority ladder (`board_priority`: parent epic ×
  Jira priority, first matching rule wins) and wear a P1/P2/… marker
- Move cards between columns via drag & drop or arrow keys (runs Jira transitions);
  config-declared label changes (`status_labels`) apply when a transition lands
- Enter launches a Claude session for the focused card in a new herdr tab
- Polls `herdr agent list` to show session status badges on cards, and
  mirrors agent statuses onto the tab labels of the board's workspace
  (like the sidebar spaces)
- Previews the focused card's session — its last Claude reply, read straight
  from the transcript, so looking never costs the session a turn (`p` turns
  the preview off when the board should stay compact)
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import webbrowser
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

def resolve_config_path() -> Path:
    """Prefer herdr's per-plugin config dir, fall back to the legacy location."""
    plugin_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if plugin_dir and (p := Path(plugin_dir) / "config.toml").exists():
        return p
    # HERDR_PLUGIN_CONFIG_DIR is only set for commands herdr itself starts, so
    # `--check` / `--dump` from a plain shell need herdr's default path too.
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    if (p := xdg / "herdr/plugins/config/jira-board/config.toml").exists():
        return p
    return Path.home() / ".config/herdr-jira-board/config.toml"


CONFIG_PATH = resolve_config_path()
STATE_DIR = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR") or Path.home() / ".local/share/herdr-jira-board")
SESSIONS_PATH = STATE_DIR / "sessions.json"
CLAUDE_SESSIONS_PATH = STATE_DIR / "claude_sessions.json"
COMPANION_PATH = STATE_DIR / "companion.json"


# ---------------------------------------------------------------- i18n

def detect_language() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return "ja" if value.lower().startswith("ja") else "en"
    return "en"


LANG = detect_language()

MESSAGES: dict[str, dict[str, str]] = {
    "config_missing": {
        "en": ("Config file not found: {path}\n"
               "Copy config.toml.example from the plugin repository to that path\n"
               "and fill in site / email / api_token (see README.md)."),
        "ja": ("設定ファイルがありません: {path}\n"
               "リポジトリの config.toml.example をこのパスにコピーし、\n"
               "site / email / api_token を設定してください (README.md 参照)。"),
    },
    "move_right": {"en": "Move right", "ja": "右の列へ"},
    "move_left": {"en": "Move left", "ja": "左の列へ"},
    "cancel_or_unfocus": {"en": "Cancel move / unfocus", "ja": "移動取消 / 選択解除"},
    "cancel": {"en": "Cancel", "ja": "キャンセル"},
    "refresh": {"en": "Refresh", "ja": "更新"},
    "confirm_or_launch": {"en": "Confirm / launch Claude", "ja": "確定 / Claude起動"},
    "open_browser": {"en": "Open in browser", "ja": "ブラウザで開く"},
    "next_card": {"en": "Next card", "ja": "次のカード"},
    "prev_card": {"en": "Previous card", "ja": "前のカード"},
    "quit": {"en": "Quit", "ja": "終了"},
    "pending_hint": {"en": "⏎ confirm / Esc cancel", "ja": "⏎確定 / Esc取消"},
    "created_label": {"en": "created", "ja": "作成"},
    "due_label": {"en": "due", "ja": "期限"},
    "pick_transition": {"en": "Select a transition for [b]{key}[/b]:",
                        "ja": "[b]{key}[/b] のトランジションを選択:"},
    "fetch_failed": {"en": "Failed to fetch from Jira: {error}", "ja": "Jira 取得失敗: {error}"},
    "config_reload_failed": {
        "en": "config.toml could not be read, keeping the previous settings: {error}",
        "ja": "config.toml を読めませんでした。前の設定を使い続けます: {error}",
    },
    "language_needs_restart": {
        "en": "`language` changed; restart the board to relabel the key bindings.",
        "ja": "`language` が変更されました。キー表示を切り替えるにはボードを再起動してください。",
    },
    "transitions_failed": {"en": "Failed to fetch transitions: {error}",
                           "ja": "トランジション取得失敗: {error}"},
    "no_transition": {"en": "{key}: no transition leads to this column",
                      "ja": "{key}: この列へ移動できるトランジションがありません"},
    "transition_failed": {
        "en": "{key}: transition failed (if it requires fields, use the browser): {error}",
        "ja": "{key}: トランジション失敗 (必須フィールドがある場合はブラウザで操作してください): {error}",
    },
    "moved": {"en": "Moved {key}", "ja": "{key} を移動しました"},
    "status_labels_applied": {"en": "{key}: labels updated ({changes})",
                              "ja": "{key}: ラベルを更新しました（{changes}）"},
    "status_labels_failed": {"en": "{key}: could not update the labels: {error}",
                             "ja": "{key}: ラベルを更新できません: {error}"},
    "transition_status": {"en": "Change status", "ja": "ステータス変更"},
    "no_transitions": {"en": "{key}: no transitions available",
                       "ja": "{key}: 実行できるトランジションがありません"},
    "transitioned": {"en": "Updated the status of {key}",
                     "ja": "{key} のステータスを更新しました"},
    "phase_label": {"en": "Phase label", "ja": "フェーズラベル"},
    "pick_phase_label": {
        "en": "Phase labels for [b]{key}[/b] — space to tick, enter to apply:",
        "ja": "[b]{key}[/b] のフェーズラベル — Space で選び、Enter で反映:",
    },
    "toggle_phase_label": {"en": "Tick / untick", "ja": "選択の切り替え"},
    "apply_phase_labels": {"en": "Apply", "ja": "反映"},
    "no_phase_labels": {
        "en": "No phase labels configured; add `phase_labels` to config.toml.",
        "ja": "フェーズラベルが未設定です。config.toml に `phase_labels` を追加してください。",
    },
    "phase_label_failed": {"en": "{key}: could not update the labels: {error}",
                           "ja": "{key}: ラベルを更新できませんでした: {error}"},
    "phase_labels_applied": {"en": "{key}: {changes}",
                             "ja": "{key}: {changes}"},
    "phase_labels_unchanged": {"en": "{key}: the labels are unchanged",
                               "ja": "{key}: ラベルは変わっていません"},
    "confirming": {"en": "Confirming the staged moves…", "ja": "仮移動を確定中です…"},
    "launching_already": {"en": "A session for {key} is already starting…",
                          "ja": "{key} のセッションを起動中です…"},
    "launching": {"en": "Starting a session for {key}…", "ja": "{key} のセッションを起動しています…"},
    "focus_failed": {"en": "{key}: cannot focus the session: {error}",
                     "ja": "{key}: セッションへ移動できません: {error}"},
    "preview_title": {"en": "{key} — last reply", "ja": "{key} の最後の返答"},
    "toggle_preview": {"en": "Preview on / off", "ja": "返答プレビュー 表示/非表示"},
    "preview_enabled": {"en": "Session preview on", "ja": "返答プレビューを表示します"},
    "preview_disabled": {"en": "Session preview off", "ja": "返答プレビューを隠しました"},
    "launch_failed": {"en": "Failed to launch session: {error}", "ja": "セッション起動失敗: {error}"},
    "launched": {"en": "Launched a Claude session for {key}",
                 "ja": "{key} の Claude セッションを起動しました"},
    "resumed": {"en": "Resumed the Claude session for {key}",
                "ja": "{key} の Claude セッションを再開しました"},
    "no_pane_id": {"en": "Cannot find a pane id in the tab create response: {data}",
                   "ja": "tab create の応答から pane id を特定できません: {data}"},
    "no_tab_id": {"en": "Cannot find tab_id for pane {pane}",
                  "ja": "pane {pane} の tab_id を特定できません"},
    "herdr_failed": {"en": "{command} failed: {detail}", "ja": "{command} が失敗: {detail}"},
    "handoff": {
        "en": ("Claude Code sessions linked to the board; their transcripts are:\n"
               "{transcripts}\n"
               "They may already hold work on this issue. Don't read them whole — grep them "
               "for {key} first and read only what matches, then carry that over."),
        "ja": ("ボードに紐づいている Claude Code セッションの記録があります:\n"
               "{transcripts}\n"
               "この課題に関するやり取りが含まれている可能性があります。全部は読まず、"
               "まず {key} で grep して、該当する部分だけ読んで引き継いでください。"),
    },
    "companion": {"en": "Companion session", "ja": "相棒セッション"},
    "companion_opening": {"en": "Opening the companion session…",
                          "ja": "相棒セッションを開いています…"},
    "companion_opened": {"en": "The companion session is ready",
                         "ja": "相棒セッションを開きました"},
    "companion_resumed": {"en": "Resumed the companion session",
                          "ja": "相棒セッションを再開しました"},
    "companion_failed": {"en": "Failed to open the companion session: {error}",
                         "ja": "相棒セッションを開けません: {error}"},
    "companion_focus_failed": {"en": "Cannot focus the companion session: {error}",
                               "ja": "相棒セッションへ移動できません: {error}"},
    "no_pane_env": {
        "en": "HERDR_PANE_ID is unset; the board has to run as a herdr plugin pane for this.",
        "ja": "HERDR_PANE_ID がありません（この機能は herdr のプラグインペインとしての起動が必要です）。",
    },
    "initial_prompt": {
        "en": ("Let's work on Jira issue {key}.\n"
               "Summary: {summary}\n"
               "Status: {status} ({issuetype})  Due: {due}\n"
               "URL: {url}"),
        "ja": ("Jira 課題 {key} の作業をします。\n"
               "サマリ: {summary}\n"
               "ステータス: {status}（{issuetype}）  期限: {due}\n"
               "URL: {url}"),
    },
    "prompt_description": {
        "en": "Description:\n{description}",
        "ja": "説明:\n{description}",
    },
    "description_truncated": {"en": "… (truncated)", "ja": "…（以下省略）"},
}


def t(msg_id: str, **kwargs: object) -> str:
    text = MESSAGES[msg_id].get(LANG) or MESSAGES[msg_id]["en"]
    return text.format(**kwargs) if kwargs else text


def set_language(lang: str | None) -> None:
    """Apply the config override ("en" / "ja"); None keeps auto-detection."""
    global LANG
    if lang in ("en", "ja"):
        LANG = lang


# Key-binding descriptions are fixed at class-definition time, so apply the
# config override before the widget classes below are defined.
try:
    set_language(tomllib.loads(CONFIG_PATH.read_text()).get("language"))
except (OSError, ValueError):
    pass

CATEGORY_COLUMNS = [("new", "To Do"), ("indeterminate", "In Progress"), ("done", "Done")]

STATUS_ICONS = {
    "working": "[yellow]● working[/]",
    "blocked": "[red]■ blocked[/]",
    "waiting": "[red]■ waiting[/]",
    "done": "[green]✔ done[/]",
    "idle": "[dim]○ idle[/]",
}

# Plain-text icons for tab labels. herdr's tab bar shows no agent status of its
# own (the sidebar spaces do), so the board carries the status in the label of
# the tabs it launched. Labels are plain text — an icon, but no color.
TAB_STATUS_ICONS = {
    "working": "●",
    "blocked": "■",
    "waiting": "■",
    "done": "✔",
    "idle": "○",
}

# ---------------------------------------------------------------- config / state

@dataclass(frozen=True)
class PhaseLabel:
    """A Jira label the board can toggle, and the short name shown on cards.

    The Jira label is namespaced (`jb_…`) because labels are shared across every
    project on the site; the card only shows `display` so the namespace does not
    eat the card's width.
    """

    label: str
    display: str

    @classmethod
    def parse(cls, raw: object) -> "PhaseLabel | None":
        """One `phase_labels` entry, or None when it is not usable.

        A bare string is accepted as shorthand for "show the label as it is".
        """
        if isinstance(raw, str):
            return cls(raw, raw) if raw else None
        if isinstance(raw, dict) and raw.get("label"):
            return cls(str(raw["label"]), str(raw.get("display") or raw["label"]))
        return None


@dataclass
class StatusLabelRule:
    """Label changes to make when an issue lands on a status.

    `remove_except` is the "everything but" form: it removes every label the
    config manages (see `managed_labels`) other than the ones listed. A rule
    carries either `remove` or `remove_except`, never both — and never removes
    a label it adds, so the two sets cannot contradict each other.
    """

    status: str
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    remove_except: list[str] | None = None

    @classmethod
    def parse(cls, raw: object) -> "StatusLabelRule | None":
        """One `status_labels` entry, or None when it is not usable.

        Contradictory rules — both removal forms at once, or a label both
        added and removed — are dropped whole rather than half-applied.
        """
        if not isinstance(raw, dict) or not raw.get("status"):
            return None
        add = [str(n) for n in raw.get("add", []) if n]
        remove = [str(n) for n in raw.get("remove", []) if n]
        except_ = raw.get("remove_except")
        if except_ is not None:
            if remove:
                return None
            except_ = [str(n) for n in except_ if n]
        if set(add) & set(remove):
            return None
        return cls(str(raw["status"]), add, remove, except_)


@dataclass
class BoardPriorityRule:
    """One step of the board's priority ladder.

    Rules are evaluated top-down and the first match gives the card its rank
    (`P1` for the first rule, and so on), so "an ST blocker beats a UAT
    blocker beats any other ST issue" is written as three rules in that order
    — orderings no single epic-then-priority comparison could express.
    """

    epic: str = ""  # the parent epic: its key, or a piece of its name; "" = any
    priority: str = ""  # the Jira priority name; "" = any
    display: str = ""  # what the card shows; "" = P<rank>

    @classmethod
    def parse(cls, raw: object) -> "BoardPriorityRule | None":
        """One `board_priority` entry, or None when it is not usable.

        A rule with neither an epic nor a priority would swallow every card,
        which can only be a mistake.
        """
        if not isinstance(raw, dict) or not (raw.get("epic") or raw.get("priority")):
            return None
        return cls(str(raw.get("epic") or ""), str(raw.get("priority") or ""),
                   str(raw.get("display") or ""))

    def matches(self, issue: Issue) -> bool:
        if self.epic:
            wanted = self.epic.casefold()
            if (wanted != issue.epic_key.casefold()
                    and not (issue.epic_name and wanted in issue.epic_name.casefold())):
                return False
        return not self.priority or self.priority.casefold() == issue.priority.casefold()


@dataclass
class Config:
    site: str
    email: str
    api_token: str
    jql: str
    exclude_statuses: list[str] = field(default_factory=list)
    status_order: list[str] = field(default_factory=list)
    phase_labels: list[PhaseLabel] = field(default_factory=list)
    status_labels: list[StatusLabelRule] = field(default_factory=list)
    board_priority: list[BoardPriorityRule] = field(default_factory=list)
    project_dirs: dict[str, str] = field(default_factory=dict)
    # Whether the session preview starts on; `p` toggles it for the session.
    preview: bool = True

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or CONFIG_PATH
        if not path.exists():
            raise SystemExit(t("config_missing", path=path))
        raw = tomllib.loads(path.read_text())
        set_language(raw.get("language"))
        token = raw.get("api_token", "")
        if not token and raw.get("api_token_cmd"):
            token = subprocess.run(
                ["bash", "-c", raw["api_token_cmd"]], capture_output=True, text=True, check=True
            ).stdout.strip()
        return cls(
            site=raw["site"].rstrip("/"),
            email=raw["email"],
            api_token=token,
            jql=raw.get(
                "jql",
                "assignee = currentUser() AND (statusCategory != Done OR updated >= -7d) ORDER BY updated DESC",
            ),
            exclude_statuses=raw.get("exclude_statuses", []),
            status_order=raw.get("status_order", []),
            phase_labels=[p for p in map(PhaseLabel.parse, raw.get("phase_labels", []))
                          if p is not None],
            status_labels=[r for r in map(StatusLabelRule.parse, raw.get("status_labels", []))
                           if r is not None],
            board_priority=[r for r in map(BoardPriorityRule.parse,
                                           raw.get("board_priority", []))
                            if r is not None],
            project_dirs=raw.get("project_dirs", {}),
            preview=bool(raw.get("preview", True)),
        )


def load_map(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update_map(path: Path, set_items: dict[str, str] | None = None,
               drop_keys: tuple[str, ...] = ()) -> dict[str, str]:
    """Apply a small change to a JSON map on disk and return the result.

    Several boards can be open at once, each holding its own copy in memory.
    Writing that copy wholesale would clobber entries the other boards saved
    in the meantime, so the file is re-read under an exclusive lock and only
    this change is applied to it.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / f"{path.name}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load_map(path)
        data.update(set_items or {})
        for key in drop_keys:
            data.pop(key, None)
        path.write_text(json.dumps(data, indent=1))
    return data


def load_sessions() -> dict[str, str]:
    """issue key -> herdr pane_id"""
    return load_map(SESSIONS_PATH)


def load_claude_sessions() -> dict[str, str]:
    """issue key -> Claude session id, kept so a relaunch can resume the conversation."""
    return load_map(CLAUDE_SESSIONS_PATH)


def load_companion() -> str:
    """Claude session id of the board's companion session ("" when there is none)."""
    try:
        return str(json.loads(COMPANION_PATH.read_text()).get("session_id") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def save_companion(session_id: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    COMPANION_PATH.write_text(json.dumps({"session_id": session_id}, indent=1))


# ---------------------------------------------------------------- jira client

@dataclass
class Issue:
    key: str
    summary: str
    status: str
    category: str  # new / indeterminate / done
    issuetype: str
    created: str = ""  # YYYY-MM-DD
    duedate: str | None = None  # YYYY-MM-DD, None when the issue has no due date
    labels: list[str] = field(default_factory=list)
    priority: str = ""  # Jira priority name ("" when the field is hidden)
    epic_key: str = ""  # the parent issue, epics in practice ("" when none)
    epic_name: str = ""


class Jira:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.http = httpx.Client(
            base_url=cfg.site, auth=(cfg.email, cfg.api_token), timeout=30,
            headers={"Accept": "application/json",
                     # Ask Jira for status/transition names in the UI language
                     # instead of the Jira account's profile language.
                     "Accept-Language": LANG,
                     "X-Force-Accept-Language": "true"},
        )

    def search(self) -> list[Issue]:
        r = self.http.post(
            "/rest/api/3/search/jql",
            json={"jql": self.cfg.jql, "maxResults": 100,
                  "fields": ["summary", "status", "issuetype", "created", "duedate",
                             "labels", "priority", "parent"]},
        )
        r.raise_for_status()
        issues = []
        for it in r.json().get("issues", []):
            f = it["fields"]
            parent = f.get("parent") or {}
            issues.append(Issue(
                key=it["key"],
                summary=f.get("summary") or "",
                status=f["status"]["name"],
                category=f["status"]["statusCategory"]["key"],
                issuetype=(f.get("issuetype") or {}).get("name", ""),
                # created is a timestamp ("2026-08-13T20:53:14.000+0900"), duedate a plain date
                created=(f.get("created") or "")[:10],
                duedate=f.get("duedate") or None,
                labels=list(f.get("labels") or []),
                priority=(f.get("priority") or {}).get("name", ""),
                epic_key=parent.get("key", ""),
                epic_name=(parent.get("fields") or {}).get("summary", ""),
            ))
        return exclude_by_status(issues, self.cfg.exclude_statuses)

    def description(self, key: str) -> str:
        """The issue's description as plain text ("" when it has none)."""
        r = self.http.get(f"/rest/api/3/issue/{key}", params={"fields": "description"})
        r.raise_for_status()
        adf = (r.json().get("fields") or {}).get("description")
        return adf_to_text(adf).strip()

    def transitions(self, key: str) -> list[dict]:
        r = self.http.get(f"/rest/api/3/issue/{key}/transitions")
        r.raise_for_status()
        return r.json().get("transitions", [])

    def update_labels(self, key: str, add: list[str], remove: list[str]) -> None:
        """Apply every label change at once, leaving the issue's others alone.

        The `update` verb sends the individual changes rather than the whole
        list, so labels other people put on the issue survive; it also takes as
        many of them as we like, so a whole picker's worth of ticks and unticks
        costs one request.
        """
        ops = [{"add": name} for name in add] + [{"remove": name} for name in remove]
        if not ops:
            return
        r = self.http.put(f"/rest/api/3/issue/{key}", json={"update": {"labels": ops}})
        r.raise_for_status()

    def do_transition(self, key: str, transition_id: str) -> None:
        r = self.http.post(f"/rest/api/3/issue/{key}/transitions",
                           json={"transition": {"id": transition_id}})
        r.raise_for_status()


def exclude_by_status(issues: list[Issue], excluded: list[str]) -> list[Issue]:
    """Issues whose status name is not listed in `excluded` (case-insensitive).

    Lets a custom workflow status be hidden from the board without rewriting the
    `jql` option, which the user may have customized (including its ORDER BY).
    """
    if not excluded:
        return issues
    drop = {name.casefold() for name in excluded}
    return [i for i in issues if i.status.casefold() not in drop]


# Block-level ADF nodes end the line they produced; everything else is inline.
ADF_BLOCK_TYPES = {"paragraph", "heading", "blockquote", "codeBlock", "listItem",
                   "tableRow", "tableHeader", "tableCell", "rule", "panel"}


def adf_to_text(node: object) -> str:
    """Plain text of an Atlassian Document Format tree (best effort).

    Jira v3 returns descriptions as ADF; the launched session only needs the
    words, so formatting is reduced to line breaks and "- " list bullets.
    """
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    typ = node.get("type")
    if typ == "text":
        return node.get("text", "")
    if typ == "hardBreak":
        return "\n"
    if typ in ("mention", "emoji", "status"):
        attrs = node.get("attrs") or {}
        return str(attrs.get("text") or attrs.get("shortName") or "")
    text = adf_to_text(node.get("content", []))
    if typ == "listItem":
        text = f"- {text}"
    if typ in ADF_BLOCK_TYPES and not text.endswith("\n"):
        text += "\n"
    return text


DESCRIPTION_LIMIT = 3000


def clip_description(text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + t("description_truncated")


def group_by_status(issues: list[Issue], order: list[str]) -> list[tuple[str, list[Issue]]]:
    """Issues grouped by status name.

    Statuses listed in `order` come first, in that order (compared
    case-insensitively); the rest follow in order of first appearance,
    which is the JQL result order.
    """
    groups: dict[str, list[Issue]] = {}
    for issue in issues:
        groups.setdefault(issue.status, []).append(issue)
    ranks = {name.casefold(): i for i, name in enumerate(order)}
    appearance = list(groups)
    statuses = sorted(groups, key=lambda s: (ranks.get(s.casefold(), len(ranks)),
                                             appearance.index(s)))
    return [(status, groups[status]) for status in statuses]


def phase_rank(issue: Issue, phase_labels: list[PhaseLabel]) -> int:
    """The issue's best (lowest) phase-label position; past the end when none."""
    ranks = {p.label: i for i, p in enumerate(phase_labels)}
    return min((ranks[name] for name in issue.labels if name in ranks),
               default=len(ranks))


def board_priority_rank(cfg: Config, issue: Issue) -> int | None:
    """The position of the first `board_priority` rule the issue matches."""
    return next((i for i, rule in enumerate(cfg.board_priority) if rule.matches(issue)),
                None)


# The ladder's top steps are what the eye should catch: red, then yellow,
# then nothing special — the same escalation the due dates use.
PRIORITY_TAG_STYLES = ("red", "red", "yellow", "yellow")


def board_priority_label(cfg: Config, issue: Issue) -> str:
    """The issue's priority marker ("P1", or the rule's display name); "" unranked."""
    rank = board_priority_rank(cfg, issue)
    if rank is None:
        return ""
    return cfg.board_priority[rank].display or f"P{rank + 1}"


def board_priority_tag(cfg: Config, issue: Issue) -> str:
    """The card's priority marker with its style ("P1" red …), "" when unranked."""
    label = board_priority_label(cfg, issue)
    if not label:
        return ""
    rank = board_priority_rank(cfg, issue)
    style = PRIORITY_TAG_STYLES[rank] if rank < len(PRIORITY_TAG_STYLES) else "dim"
    return f"[{style}]{label}[/]"


def sort_cards(issues: list[Issue], cfg: Config) -> list[Issue]:
    """The order cards take inside one status group.

    Board priority first — it answers "which of these do I pick up" — then the
    phase labels, and the JQL order settles the rest (the sort is stable).
    Sorting rather than grouping keeps this independent of `status_order`: the
    status groups stay as they are and only the cards inside one reorder.
    """
    def key(issue: Issue) -> tuple[int, int]:
        rank = board_priority_rank(cfg, issue)
        return (len(cfg.board_priority) if rank is None else rank,
                phase_rank(issue, cfg.phase_labels))

    return sorted(issues, key=key)


def describe_label_changes(add: list[str], remove: list[str],
                           phase_labels: list[PhaseLabel]) -> str:
    """What changed, in the short names the cards use ("+A +B -C")."""
    shown = {p.label: p.display for p in phase_labels}
    return " ".join([f"+{shown.get(name, name)}" for name in add]
                    + [f"-{shown.get(name, name)}" for name in remove])


def phase_labels_of(issue: Issue, phase_labels: list[PhaseLabel]) -> list[PhaseLabel]:
    """The configured phase labels the issue carries, in the configured order."""
    return [p for p in phase_labels if p.label in issue.labels]


def transitions_to_category(transitions: list[dict], target_category: str) -> list[dict]:
    """Transitions whose target status belongs to the given status category."""
    return [tr for tr in transitions
            if tr["to"]["statusCategory"]["key"] == target_category]


def managed_labels(cfg: Config) -> set[str]:
    """Every label the config itself mentions.

    This is the universe `remove_except` removes from: labels other people put
    on an issue are not the board's to take off, so a rule can only ever remove
    what the config somewhere names.
    """
    names = {p.label for p in cfg.phase_labels}
    for rule in cfg.status_labels:
        names.update(rule.add, rule.remove, rule.remove_except or ())
    return names


def status_label_changes(cfg: Config, current: list[str],
                         to_status: str) -> tuple[list[str], list[str]]:
    """(labels to add, labels to remove) when an issue lands on `to_status`.

    Statuses are compared case-insensitively, like `exclude_statuses`. A label
    any matching rule adds is never removed. Both lists are filtered against
    the labels the issue already has, so an issue in the right state costs no
    request.
    """
    add: set[str] = set()
    remove: set[str] = set()
    target = to_status.casefold()
    for rule in cfg.status_labels:
        if rule.status.casefold() != target:
            continue
        add.update(rule.add)
        remove.update(rule.remove)
        if rule.remove_except is not None:
            remove.update(managed_labels(cfg) - set(rule.remove_except) - set(rule.add))
    remove -= add
    have = set(current)
    return sorted(add - have), sorted(remove & have)


# ---------------------------------------------------------------- herdr helpers

def herdr(*args: str) -> dict:
    bin_ = os.environ.get("HERDR_BIN_PATH", "herdr")
    out = subprocess.run([bin_, *args], capture_output=True, text=True, check=True).stdout
    try:
        return json.loads(out)
    except ValueError:
        return {}


def find_key(obj, key: str):
    """Return the first value found for key anywhere in nested JSON."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            if (r := find_key(v, key)) is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            if (r := find_key(v, key)) is not None:
                return r
    return None


def agent_statuses() -> dict[str, str] | None:
    """herdr pane_id -> agent status; None when `herdr agent list` itself failed.

    None and {} differ: {} is a real answer ("no agents running"), while None
    means the answer is unknown — a recorded pane must not be treated as gone
    on the strength of a failed call.
    """
    try:
        data = herdr("agent", "list")
    except (subprocess.CalledProcessError, OSError):
        return None
    agents = find_key(data, "agents") or []
    out = {}
    for a in agents:
        if isinstance(a, dict) and a.get("pane_id"):
            out[str(a["pane_id"])] = str(a.get("agent_status") or a.get("status") or "")
    return out


def find_session_pane(key: str) -> str | None:
    """Find a running agent's pane for the issue key by agent name."""
    try:
        data = herdr("agent", "list")
    except (subprocess.CalledProcessError, OSError):
        return None
    prefix = f"{key.lower()}-"
    for a in find_key(data, "agents") or []:
        if isinstance(a, dict) and str(a.get("name") or "").startswith(prefix) and a.get("pane_id"):
            return str(a["pane_id"])
    return None


def strip_status_icon(label: str) -> str:
    """The label without its leading status icon (the part the board manages)."""
    for icon in TAB_STATUS_ICONS.values():
        if label.startswith(f"{icon} "):
            return label[len(icon) + 1:]
    return label


def tab_label_for(base: str, status: str | None) -> str:
    """The tab label for an issue's session: status icon + base, or the bare base."""
    icon = TAB_STATUS_ICONS.get(status or "")
    return f"{icon} {base}" if icon else base


# When a tab holds several agents, the icon shows the most attention-worthy
# one: stuck agents first, then activity, then a finished turn waiting to be
# read, and a quiet prompt last.
STATUS_PRIORITY = ("blocked", "waiting", "working", "done", "idle")


def aggregate_status(statuses: list[str]) -> str | None:
    """The most attention-worthy status among a tab's agents (None when none)."""
    for status in STATUS_PRIORITY:
        if status in statuses:
            return status
    return statuses[0] if statuses else None


def sync_tab_labels(statuses: dict[str, str] | None, sessions: dict[str, str]) -> None:
    """Mirror agent statuses onto the tab labels of the board's workspace.

    Like the sidebar spaces, every tab shows the state of the agents it holds —
    the sessions launched from cards, the companion, and any other agent the
    user runs in a tab; a tab whose agents are gone loses its icon. Only the
    leading icon is the board's: the rest of the label — the issue key set at
    launch, or whatever the user renamed the tab to — is kept as is. Runs on
    the badge tick. With statuses None (herdr unreachable) the labels keep
    their last state, like the badges.
    """
    if statuses is None:
        return
    try:
        panes = find_key(herdr("pane", "list"), "panes") or []
        tabs = find_key(herdr("tab", "list"), "tabs") or []
    except (subprocess.CalledProcessError, OSError):
        return
    pane_tabs = {str(p["pane_id"]): str(p.get("tab_id") or "")
                 for p in panes if isinstance(p, dict) and p.get("pane_id")}
    by_tab: dict[str, list[str]] = {}
    for pane, status in statuses.items():
        if tab_id := pane_tabs.get(pane):
            by_tab.setdefault(tab_id, []).append(status)
    session_tabs = {pane_tabs.get(pane): key for key, pane in sessions.items()}
    # "w1:t2" belongs to workspace "w1"; tab rows also carry workspace_id.
    workspace = (os.environ.get("HERDR_TAB_ID") or "").split(":")[0]
    for tab in tabs:
        if not isinstance(tab, dict) or not tab.get("tab_id"):
            continue
        tab_id = str(tab["tab_id"])
        # Outside herdr the board's workspace is unknown; then only the tabs
        # of the board's own sessions are touched.
        if workspace:
            if str(tab.get("workspace_id") or tab_id.split(":")[0]) != workspace:
                continue
        elif tab_id not in session_tabs:
            continue
        label = str(tab.get("label") or "")
        base = strip_status_icon(label) or session_tabs.get(tab_id, "")
        if not base:
            continue
        desired = tab_label_for(base, aggregate_status(by_tab.get(tab_id, [])))
        if desired == label:
            continue
        try:
            herdr("tab", "rename", tab_id, desired)
        except (subprocess.CalledProcessError, OSError):
            pass


def claude_panes() -> list[dict[str, str]]:
    """Every pane running Claude, as {pane_id, tab_id, session_id, cwd}."""
    try:
        panes = find_key(herdr("pane", "list"), "panes") or []
    except (subprocess.CalledProcessError, OSError):
        return []
    out = []
    for pane in panes:
        if not isinstance(pane, dict) or pane.get("agent") != "claude":
            continue
        if session := (pane.get("agent_session") or {}).get("value"):
            out.append({"pane_id": str(pane.get("pane_id") or ""),
                        "tab_id": str(pane.get("tab_id") or ""),
                        "session_id": str(session), "cwd": str(pane.get("cwd") or "")})
    return out


def neighbor_sessions() -> list[dict[str, str]]:
    """The sessions a launched one inherits from.

    That is the companion session wherever it sits, plus any other Claude pane in
    the board's own tab (herdr exports HERDR_PANE_ID / HERDR_TAB_ID to the plugin
    pane, so the board can tell which panes sit next to it).
    """
    own_pane = os.environ.get("HERDR_PANE_ID")
    tab_id = os.environ.get("HERDR_TAB_ID")
    companion = load_companion()
    return [pane for pane in claude_panes()
            if pane["pane_id"] != own_pane
            and (pane["session_id"] == companion or (tab_id and pane["tab_id"] == tab_id))]


def companion_pane() -> dict[str, str] | None:
    """The companion session's pane, when that session is still running."""
    if not (session_id := load_companion()):
        return None
    return next((p for p in claude_panes() if p["session_id"] == session_id), None)


def companion_cwd() -> str:
    """Where the companion session starts: the workspace's directory, else home."""
    try:
        context = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        context = {}
    cwd = str(context.get("workspace_cwd") or "")
    # a workspace can outlive its directory (a removed worktree, for instance)
    return cwd if cwd and Path(cwd).is_dir() else os.path.expanduser("~")


def start_claude(pane_id: str, name: str, *agent_args: str) -> dict:
    """Start Claude in a fresh pane, retrying while its shell is still coming up."""
    args = ["agent", "start", name, "--kind", "claude", "--pane", pane_id, "--timeout", "60000"]
    if agent_args:
        args += ["--", *agent_args]
    for attempt in range(20):
        try:
            return herdr(*args)
        except subprocess.CalledProcessError as e:
            # Right after the pane is created its shell may not be up yet and agent
            # start fails with agent_pane_busy ("not an available shell"); retry.
            if attempt < 19 and "agent_pane_busy" in ((e.stdout or "") + (e.stderr or "")):
                time.sleep(0.5)
                continue
            raise
    return {}


def open_companion() -> bool:
    """Put the companion session in a pane beside the board. True when it resumed.

    The recorded session is resumed so the same conversation comes back after its
    pane, or the board, was closed; a session Claude no longer knows starts fresh.
    """
    own_pane = os.environ.get("HERDR_PANE_ID")
    if not own_pane:
        raise RuntimeError(t("no_pane_env"))
    pane = herdr("pane", "split", own_pane, "--direction", "right",
                 "--cwd", companion_cwd(), "--no-focus")
    pane_id = find_key(pane, "pane_id")
    if not pane_id:
        raise RuntimeError(t("no_pane_id", data=pane))
    name = f"companion-{str(pane_id).split(':')[-1].lower()}"
    resumed = False
    try:
        if session_id := load_companion():
            try:
                started = start_claude(str(pane_id), name, "--resume", session_id)
                resumed = True
            except subprocess.CalledProcessError:
                started = start_claude(str(pane_id), name)
        else:
            started = start_claude(str(pane_id), name)
    except subprocess.CalledProcessError as e:
        # Don't leave the empty pane behind on failure
        try:
            herdr("pane", "close", str(pane_id))
        except subprocess.CalledProcessError:
            pass
        detail = (e.stderr or e.stdout or "").strip()[:200]
        raise RuntimeError(t("herdr_failed", command=" ".join(e.cmd[1:3]), detail=detail)) from e
    if session := (find_key(started, "agent_session") or {}).get("value"):
        save_companion(str(session))
    return resumed


def transcript_path(session_id: str) -> Path | None:
    """The Claude Code transcript of a session, looked up by its id.

    Its directory is named after the session's cwd with the punctuation rewritten,
    so glob for the (unique) session id rather than rebuilding that name.
    """
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    try:
        return next(root.glob(f"*/{session_id}.jsonl"), None)
    except OSError:
        return None


PREVIEW_LIMIT = 2000
TRANSCRIPT_TAIL_BYTES = 256 * 1024


def last_assistant_text(path: Path) -> str:
    """The last assistant text message in a Claude Code transcript ("" when none).

    Transcripts grow to megabytes, so only the tail is read. Entries whose
    content is tool calls only (no text) are skipped, as are subagent
    ("sidechain") entries.
    """
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TRANSCRIPT_TAIL_BYTES))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            texts = [part.get("text", "") for part in content
                     if isinstance(part, dict) and part.get("type") == "text"]
        else:
            continue
        if text := "\n".join(filter(None, texts)).strip():
            return text
    return ""


def handoff_note(issue_key: str) -> str:
    """Point the new session at the transcripts of the sessions next to the board."""
    lines = [f"- {path} (cwd: {pane['cwd'] or '-'})"
             for pane in neighbor_sessions()
             if (path := transcript_path(pane["session_id"]))]
    return t("handoff", key=issue_key, transcripts="\n".join(lines)) if lines else ""


def initial_prompt(issue: Issue, cfg: Config, description: str = "") -> str:
    """The prompt the launched session starts from, with the handoff appended."""
    parts = [t("initial_prompt", key=issue.key, summary=issue.summary,
               status=issue.status, issuetype=issue.issuetype or "-",
               due=issue.duedate or "-", url=f"{cfg.site}/browse/{issue.key}")]
    if description:
        parts.append(t("prompt_description", description=clip_description(description)))
    if note := handoff_note(issue.key):
        parts.append(note)
    return "\n\n".join(parts)


def send_prompt(pane_id: str, prompt: str) -> None:
    """Send a prompt to an agent waiting at its prompt, and let it start working."""
    try:
        herdr("agent", "prompt", pane_id, prompt, "--wait", "--until", "working",
              "--timeout", "30000")
    except subprocess.CalledProcessError:
        # The text usually lands but the submitting Enter can be swallowed.
        # Give the agent a moment, then press Enter instead of resending the
        # text (a resend would duplicate the prompt in the composer).
        try:
            herdr("agent", "wait", pane_id, "--until", "working", "--timeout", "10000")
        except subprocess.CalledProcessError:
            herdr("agent", "send-keys", pane_id, "enter")
            herdr("agent", "wait", pane_id, "--until", "working", "--timeout", "30000")


def launch_claude(issue: Issue, cfg: Config, description: str = "",
                  resume_session: str = "") -> tuple[str, str, bool]:
    """Create a tab for the issue and launch Claude.

    With resume_session the recorded Claude session is resumed, so the same
    conversation comes back after its pane (or herdr itself) was closed; a
    session Claude no longer knows starts fresh. A resumed session already has
    its context on screen, so nothing is sent to it.

    Returns (pane_id, Claude session id, resumed).
    """
    project = issue.key.split("-")[0]
    cwd = os.path.expanduser(cfg.project_dirs.get(project, "~"))
    tab = herdr(
        "tab", "create", "--cwd", cwd, "--label", issue.key,
        "--env", f"JIRA_ISSUE_KEY={issue.key}", "--no-focus",
    )
    pane_id = find_key(tab, "active_pane_id") or find_key(tab, "pane_id")
    if not pane_id:
        raise RuntimeError(t("no_pane_id", data=tab))
    try:
        agent_name = f"{issue.key.lower()}-{str(pane_id).split(':')[-1].lower()}"
        resumed = False
        if resume_session:
            try:
                started = start_claude(str(pane_id), agent_name, "--resume", resume_session)
                resumed = True
            except subprocess.CalledProcessError:
                started = start_claude(str(pane_id), agent_name)
        else:
            started = start_claude(str(pane_id), agent_name)
        # Claude may not accept input immediately after start; wait until it is
        # idle (prompt ready), then send and confirm the working transition.
        herdr("agent", "wait", str(pane_id), "--until", "idle", "--timeout", "60000")
        if not resumed:
            send_prompt(str(pane_id), initial_prompt(issue, cfg, description))
    except subprocess.CalledProcessError as e:
        # Don't leave the empty tab behind on failure
        tab_id = find_key(tab, "tab_id")
        if tab_id:
            try:
                herdr("tab", "close", str(tab_id))
            except subprocess.CalledProcessError:
                pass
        detail = (e.stderr or e.stdout or "").strip()[:200]
        raise RuntimeError(t("herdr_failed", command=" ".join(e.cmd[1:3]), detail=detail)) from e
    session = str((find_key(started, "agent_session") or {}).get("value") or "")
    return str(pane_id), session, resumed


BROWSER_OPENERS = ("wslview", "xdg-open", "open")


def open_url(url: str) -> None:
    # webbrowser.open() spawns the opener with our stdout/stderr attached, so
    # anything it prints lands on top of the board. wslu's wslview does exactly
    # that on WSL. Run the opener ourselves with its output discarded.
    for name in BROWSER_OPENERS:
        path = shutil.which(name)
        if path:
            subprocess.Popen(
                [path, url],
                # The board's own directory can be gone by now — a worktree it
                # was opened in, removed or moved since. A child inherits that
                # deleted directory, and on WSL launching a Windows binary from
                # one fails outright (wslview then opens nothing at all), so
                # the opener is anchored somewhere that cannot go missing. It
                # is given an absolute URL and needs no directory of its own.
                cwd="/",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
    webbrowser.open(url)


# ---------------------------------------------------------------- dates

DUE_SOON_DAYS = 3


def due_style(duedate: str | None, today: date) -> str:
    """Rich style for a due date: overdue is red, due within DUE_SOON_DAYS yellow."""
    if not duedate:
        return "dim"
    try:
        remaining = (date.fromisoformat(duedate) - today).days
    except ValueError:
        return "dim"
    if remaining < 0:
        return "red"
    return "yellow" if remaining <= DUE_SOON_DAYS else "dim"


def dates_line(issue: Issue, today: date | None = None) -> str:
    """Created date, plus the due date colored by urgency. Empty when neither is set."""
    parts = []
    if issue.created:
        parts.append(f"[dim]{t('created_label')} {issue.created}[/]")
    if issue.duedate:
        style = due_style(issue.duedate, today or date.today())
        parts.append(f"[{style}]{t('due_label')} {issue.duedate}[/]")
    return "  ".join(parts)


# ---------------------------------------------------------------- widgets

class Card(Static, can_focus=True):
    # VerticalScroll (the column) binds arrow keys to scrolling, so the focused
    # card carries these bindings itself to take precedence.
    BINDINGS = [
        Binding("right", "app.move(1)", t("move_right"), key_display="→"),
        Binding("left", "app.move(-1)", t("move_left"), key_display="←"),
        Binding("escape", "app.cancel_move", t("cancel_or_unfocus"), key_display="Esc"),
    ]

    def __init__(self, issue: Issue, phase_labels: list[PhaseLabel] | None = None,
                 priority_tag: str = ""):
        super().__init__()
        self.issue = issue
        self.phase_labels = phase_labels or []
        self.priority_tag = priority_tag
        self.agent_status: str | None = None
        self.pending_target: str | None = None  # target category (unconfirmed)
        self.render_card()

    def render_card(self) -> None:
        tag = f" {self.priority_tag}" if self.priority_tag else ""
        badge = ""
        if self.agent_status is not None:
            badge = "  " + STATUS_ICONS.get(self.agent_status, f"[dim]{self.agent_status}[/]")
        pending = f"  [yellow]{t('pending_hint')}[/]" if self.pending_target else ""
        phases = "".join(f"  [cyan]{p.display}[/]"
                         for p in phase_labels_of(self.issue, self.phase_labels))
        dates = dates_line(self.issue)
        self.update(f"[b]{self.issue.key}[/b]{tag} [dim]{self.issue.status}[/]{badge}{phases}"
                    f"{pending}\n{self.issue.summary}" + (f"\n{dates}" if dates else ""))

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.focus()
        self.capture_mouse()
        self.add_class("dragging")

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.capture_mouse(False)
        self.remove_class("dragging")
        target = self.screen.get_widget_at(*event.screen_offset)[0]
        while target is not None and not isinstance(target, Column):
            target = target.parent
        if isinstance(target, Column) and target.category != self.issue.category:
            self.app.stage_move(self, target.category)


class Column(VerticalScroll):
    def __init__(self, category: str, title: str):
        super().__init__(classes="column")
        self.category = category
        self.border_title = title


class Preview(VerticalScroll, can_focus=False):
    """The focused card's session at a glance: its last Claude reply.

    The text comes straight from the session's transcript, so reading it never
    costs the session a turn (unlike prompting it for a status).
    """


class StatusDivider(Static):
    """A label separating the status groups inside a column."""

    def __init__(self, status: str):
        super().__init__(f"[dim]── {status} ──[/]", classes="status-divider")
        self.status = status


class TransitionPicker(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", t("cancel"))]

    def __init__(self, issue: Issue, transitions: list[dict]):
        super().__init__()
        self.issue = issue
        self.transitions = transitions

    def compose(self) -> ComposeResult:
        # Append the transition name only when two candidates share the same
        # target status and the label alone would be ambiguous.
        targets = [tr["to"]["name"] for tr in self.transitions]

        def label(tr: dict) -> str:
            base = f"{self.issue.status} → {tr['to']['name']}"
            if targets.count(tr["to"]["name"]) > 1:
                base += f"  [dim]({tr['name']})[/]"
            return base

        with Vertical(id="picker"):
            yield Static(t("pick_transition", key=self.issue.key))
            yield OptionList(*[Option(label(tr), id=tr["id"]) for tr in self.transitions])

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)


class PhaseLabelPicker(ModalScreen["set[str] | None"]):
    """Tick the phase labels the issue should end up with.

    Space edits the ticks locally and enter dismisses with the whole set, so
    several labels can go on and off in one trip to Jira instead of one each.
    """

    # OptionList binds enter (and, depending on the version, space) itself, so
    # these have to be priority bindings or the focused list swallows them.
    BINDINGS = [
        Binding("escape", "dismiss(None)", t("cancel")),
        Binding("space", "toggle", t("toggle_phase_label"), priority=True),
        Binding("enter", "apply", t("apply_phase_labels"), priority=True),
    ]

    def __init__(self, issue: Issue, phase_labels: list[PhaseLabel]):
        super().__init__()
        self.issue = issue
        self.phase_labels = phase_labels
        self.ticked = {p.label for p in phase_labels if p.label in issue.labels}

    def rows(self) -> list[Option]:
        return [Option(f"{'[green]✔[/]' if p.label in self.ticked else '[dim]·[/]'} {p.display}"
                       f"  [dim]{p.label}[/]", id=p.label)
                for p in self.phase_labels]

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static(t("pick_phase_label", key=self.issue.key))
            yield OptionList(*self.rows())

    def action_toggle(self) -> None:
        options = self.query_one(OptionList)
        if (index := options.highlighted) is None:
            return
        self.ticked ^= {str(options.get_option_at_index(index).id)}
        # The tick is part of the option's text, so the row has to be rebuilt;
        # restore the cursor afterwards or it jumps back to the top.
        options.clear_options()
        options.add_options(self.rows())
        options.highlighted = index

    def action_apply(self) -> None:
        self.dismiss(self.ticked)


# ---------------------------------------------------------------- app

class BoardApp(App):
    TITLE = "Jira Board"
    CSS = """
    Horizontal#columns { height: 1fr; }
    .column { width: 1fr; border: round $primary; margin: 0 1; padding: 0 1; }
    Card { border: round $surface-lighten-2; margin-bottom: 1; padding: 0 1; }
    Card:focus { border: round $accent; }
    Card.dragging { opacity: 0.6; }
    Card.pending { border: round $warning; }
    .status-divider { margin-bottom: 1; text-align: center; }
    Preview { display: none; height: auto; max-height: 12; border: round $primary;
              margin: 0 1; padding: 0 1; }
    #picker { width: 60; height: auto; max-height: 20; border: thick $accent; background: $surface; padding: 1; }
    TransitionPicker { align: center middle; }
    PhaseLabelPicker { align: center middle; }
    """
    BINDINGS = [
        Binding("r", "refresh", t("refresh")),
        Binding("enter", "confirm_or_launch", t("confirm_or_launch")),
        Binding("escape", "cancel_move", t("cancel_or_unfocus"), show=False),
        Binding("o", "open_browser", t("open_browser")),
        Binding("t", "transition", t("transition_status")),
        Binding("l", "phase_label", t("phase_label")),
        Binding("c", "companion", t("companion")),
        Binding("p", "toggle_preview", t("toggle_preview")),
        Binding("down", "focus_next", t("next_card"), show=False),
        Binding("up", "focus_previous", t("prev_card"), show=False),
        Binding("q", "quit", t("quit")),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        self.jira = Jira(self.cfg)
        self.sessions = load_sessions()
        self.claude_sessions = load_claude_sessions()
        # `p` overrides the config default for the rest of the session, so a
        # config reload must not reach back in and undo it.
        self.preview_enabled = self.cfg.preview
        self._launching: set[str] = set()
        self._moving = False
        self._opening_companion = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="columns"):
            for cat, title in CATEGORY_COLUMNS:
                yield Column(cat, title)
        with Preview(id="preview"):
            yield Static(id="preview-text")
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh()
        self.set_interval(5, self.update_badges)

    # ---- data loading

    def reload_config(self) -> None:
        """Pick up config.toml edits without restarting the board.

        This runs on every refresh, so a half-written or broken file must not
        take the board down: the previous settings stay in place and the error
        is reported instead.
        """
        language_before = LANG
        try:
            cfg = Config.load()
        except (Exception, SystemExit) as e:  # noqa: BLE001
            # SystemExit covers the file going missing mid-edit; Config.load
            # raises it rather than an ordinary exception.
            self.call_from_thread(self.notify, t("config_reload_failed", error=e),
                                  severity="error")
            return
        # The HTTP client bakes in the credentials, so it only needs rebuilding
        # when those change. Everything else is read from cfg on each use.
        if (cfg.site, cfg.email, cfg.api_token) != (
                self.cfg.site, self.cfg.email, self.cfg.api_token):
            previous, self.jira = self.jira, Jira(cfg)
            previous.http.close()
        else:
            self.jira.cfg = cfg
        self.cfg = cfg
        if LANG != language_before:
            # Key-binding descriptions were fixed at class-definition time and
            # the Accept-Language header at client-construction time.
            self.call_from_thread(self.notify, t("language_needs_restart"),
                                  severity="warning")

    @work(thread=True, exclusive=True)
    def action_refresh(self) -> None:
        self.reload_config()
        try:
            issues = self.jira.search()
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self.notify, t("fetch_failed", error=e), severity="error")
            return
        self.call_from_thread(self.populate, issues)

    def populate(self, issues: list[Issue]) -> None:
        for col in self.query(Column):
            col.remove_children()
            column_issues = [i for i in issues if i.category == col.category]
            groups = group_by_status(column_issues, self.cfg.status_order)
            for status, group in groups:
                # The divider only earns its line when the column actually
                # mixes statuses (To Do / Done usually have just one).
                if len(groups) > 1:
                    col.mount(StatusDivider(status))
                for issue in sort_cards(group, self.cfg):
                    col.mount(Card(issue, self.cfg.phase_labels,
                                   board_priority_tag(self.cfg, issue)))
        if (first := next(iter(self.query(Card)), None)) is not None:
            first.focus()
        self.update_badges()

    @work(thread=True, exclusive=True, group="badges")
    def update_badges(self) -> None:
        statuses = agent_statuses()
        # Re-read the state files on every tick, so sessions another board
        # launched (or dropped) since ours started show up here too.
        sessions = load_sessions()
        # herdr calls block, so the tab labels sync here in the worker thread.
        sync_tab_labels(statuses, sessions)
        self.call_from_thread(self.apply_badges, statuses,
                              sessions, load_claude_sessions())

    def apply_badges(self, statuses: dict[str, str] | None,
                     sessions: dict[str, str], claude_sessions: dict[str, str]) -> None:
        self.sessions = sessions
        self.claude_sessions = claude_sessions
        # With statuses None (herdr unreachable) the badges keep their last
        # known state instead of flashing off.
        if statuses is not None:
            for card in self.query(Card):
                agent = self.sessions.get(card.issue.key)
                new = statuses.get(agent) if agent else None
                if new != card.agent_status:
                    card.agent_status = new
                    card.render_card()
        # The badge poll doubles as the preview's refresh tick, so the last
        # reply keeps up while the focused card's session is working.
        self.refresh_preview()

    # ---- session preview

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.refresh_preview()

    def action_toggle_preview(self) -> None:
        """Turn the session preview off (and on again).

        Off, the board is nothing but its columns — worth having when the
        cards themselves are what you want on screen.
        """
        self.preview_enabled = not self.preview_enabled
        self.refresh_preview()
        self.notify(t("preview_enabled" if self.preview_enabled else "preview_disabled"))

    def refresh_preview(self) -> None:
        """Show the focused card's last session reply, or hide the pane."""
        card = self.focused_card()
        session = self.claude_sessions.get(card.issue.key, "") if card else ""
        if not self.preview_enabled or not session:
            self.query_one(Preview).display = False
            return
        self.load_preview(card.issue.key, session)

    @work(thread=True, exclusive=True, group="preview")
    def load_preview(self, key: str, session_id: str) -> None:
        path = transcript_path(session_id)
        text = last_assistant_text(path) if path else ""
        self.call_from_thread(self.show_preview, key, text)

    def show_preview(self, key: str, text: str) -> None:
        card = self.focused_card()
        if not card or card.issue.key != key:
            return  # focus moved on; the current card's own load owns the pane
        preview = self.query_one(Preview)
        # The reply is read in a worker, so the preview can have been turned
        # off while this one was still loading.
        if not text or not self.preview_enabled:
            preview.display = False
            return
        preview.border_title = t("preview_title", key=key)
        self.query_one("#preview-text", Static).update(
            Text(clip_description(text, PREVIEW_LIMIT)))
        preview.display = True

    # ---- card movement / transitions

    def focused_card(self) -> Card | None:
        return self.focused if isinstance(self.focused, Card) else None

    def action_move(self, direction: int) -> None:
        card = self.focused_card()
        if not card:
            return
        cats = [c for c, _ in CATEGORY_COLUMNS]
        current = card.pending_target or card.issue.category
        idx = cats.index(current) + direction
        if not 0 <= idx < len(cats):
            return
        if cats[idx] == card.issue.category:
            self.cancel_move(card)
        else:
            self.stage_move(card, cats[idx])

    # ---- pending move (move visually only; Enter confirms, Esc cancels)

    def stage_move(self, card: Card, target_category: str) -> None:
        # Staged moves accumulate: cards are moved one at a time and confirmed together.
        self.mount_card_in(card, target_category)
        card.pending_target = target_category
        card.add_class("pending")
        card.render_card()

    def cancel_move(self, card: Card | None = None) -> None:
        card = card or self.focused_card()
        if not card or not card.pending_target:
            return
        self.mount_card_in(card, card.issue.category)
        card.pending_target = None
        card.remove_class("pending")
        card.render_card()

    def pending_cards(self) -> list[Card]:
        """Cards with a staged move, in board order (left column first)."""
        return [card for card in self.query(Card) if card.pending_target]

    def action_cancel_move(self) -> None:
        if pending := self.pending_cards():
            for card in pending:
                self.cancel_move(card)
        elif self.focused_card():
            self.set_focus(None)
            self.refresh_preview()

    def mount_card_in(self, card: Card, category: str) -> None:
        column = next(c for c in self.query(Column) if c.category == category)
        # Only the card the user is on keeps the focus; re-mounting the others
        # (confirming or cancelling several staged moves) must not steal it.
        had_focus = card.has_focus

        async def _move() -> None:
            await card.remove()
            await column.mount(card)
            if had_focus:
                card.focus()

        self.run_worker(_move(), exclusive=False)

    def action_confirm_or_launch(self) -> None:
        if pending := self.pending_cards():
            if self._moving:
                self.notify(t("confirming"))
                return
            # Set here, not in the worker: a second Enter can arrive before the
            # worker's first line runs.
            self._moving = True
            self.run_moves(pending)
        elif self.focused_card():
            self.action_launch()

    @work(group="moves")
    async def run_moves(self, cards: list[Card]) -> None:
        """Confirm the staged moves one card at a time, pickers included."""
        moved = False
        try:
            for card in cards:
                # A card can lose its staged move while an earlier picker is open.
                if card.pending_target and await self.run_move(card, card.pending_target):
                    moved = True
        finally:
            self._moving = False
        if moved:
            self.action_refresh()

    async def run_move(self, card: Card, target_category: str) -> bool:
        """Run one card's transition. Cancels its staged move and returns False on failure."""
        key = card.issue.key
        try:
            # httpx is synchronous; keep it off the event loop so the board stays live.
            transitions = await asyncio.to_thread(self.jira.transitions, key)
        except Exception as e:  # noqa: BLE001
            self.notify(t("transitions_failed", error=e), severity="error")
            self.cancel_move(card)
            return False
        candidates = transitions_to_category(transitions, target_category)
        if not candidates:
            self.notify(t("no_transition", key=key), severity="warning")
            self.cancel_move(card)
            return False
        if len(candidates) == 1:
            transition_id = candidates[0]["id"]
        else:
            transition_id = await self.push_screen_wait(TransitionPicker(card.issue, candidates))
            if not transition_id:
                self.cancel_move(card)
                return False
        try:
            await asyncio.to_thread(self.jira.do_transition, key, transition_id)
        except Exception as e:  # noqa: BLE001
            self.notify(t("transition_failed", key=key, error=e), severity="error")
            self.cancel_move(card)
            return False
        self.notify(t("moved", key=key))
        chosen = next(tr for tr in candidates if tr["id"] == transition_id)
        await self.apply_status_labels(card.issue, chosen["to"]["name"])
        return True

    def action_transition(self) -> None:
        """Change the focused card's status without leaving its column.

        The arrow keys only reach another column's statuses; this covers
        moves inside one category (e.g. In Progress -> In Review).
        """
        if card := self.focused_card():
            self.run_transition(card)

    @work(group="moves", exclusive=True)
    async def run_transition(self, card: Card) -> None:
        key = card.issue.key
        try:
            transitions = await asyncio.to_thread(self.jira.transitions, key)
        except Exception as e:  # noqa: BLE001
            self.notify(t("transitions_failed", error=e), severity="error")
            return
        if not transitions:
            self.notify(t("no_transitions", key=key), severity="warning")
            return
        # Always ask, even with a single candidate: unlike an arrow-key move
        # the user has not said where the card should go yet.
        transition_id = await self.push_screen_wait(TransitionPicker(card.issue, transitions))
        if not transition_id:
            return
        try:
            await asyncio.to_thread(self.jira.do_transition, key, transition_id)
        except Exception as e:  # noqa: BLE001
            self.notify(t("transition_failed", key=key, error=e), severity="error")
            return
        self.notify(t("transitioned", key=key))
        chosen = next(tr for tr in transitions if tr["id"] == transition_id)
        await self.apply_status_labels(card.issue, chosen["to"]["name"])
        self.action_refresh()

    async def apply_status_labels(self, issue: Issue, to_status: str) -> None:
        """Apply the config's `status_labels` rules after a transition.

        The transition itself has already succeeded, so a failure here is
        reported and swallowed rather than undoing anything.
        """
        add, remove = status_label_changes(self.cfg, issue.labels, to_status)
        if not add and not remove:
            return
        try:
            await asyncio.to_thread(self.jira.update_labels, issue.key, add, remove)
        except Exception as e:  # noqa: BLE001
            self.notify(t("status_labels_failed", key=issue.key, error=e), severity="error")
            return
        changes = ", ".join([f"+{n}" for n in add] + [f"-{n}" for n in remove])
        self.notify(t("status_labels_applied", key=issue.key, changes=changes))

    # ---- phase labels

    def action_phase_label(self) -> None:
        """Put a phase label on the focused card, or take it off.

        Phases that are not worth a workflow status of their own (and that would
        have to be added to every project's workflow separately) live here
        instead: a label is site-wide, so one name works across projects.
        """
        if not self.cfg.phase_labels:
            self.notify(t("no_phase_labels"), severity="warning")
            return
        if card := self.focused_card():
            self.run_phase_label(card)

    @work(group="moves", exclusive=True)
    async def run_phase_label(self, card: Card) -> None:
        key = card.issue.key
        ticked = await self.push_screen_wait(
            PhaseLabelPicker(card.issue, self.cfg.phase_labels))
        if ticked is None:  # cancelled; an empty set means "take them all off"
            return
        known = {p.label for p in self.cfg.phase_labels}
        before = {name for name in card.issue.labels if name in known}
        add, remove = sorted(ticked - before), sorted(before - ticked)
        if not add and not remove:
            self.notify(t("phase_labels_unchanged", key=key))
            return
        try:
            await asyncio.to_thread(self.jira.update_labels, key, add, remove)
        except Exception as e:  # noqa: BLE001
            self.notify(t("phase_label_failed", key=key, error=e), severity="error")
            return
        self.notify(t("phase_labels_applied", key=key,
                      changes=describe_label_changes(add, remove, self.cfg.phase_labels)))
        # Refreshing re-sorts the column, which is the point of the label.
        self.action_refresh()

    # ---- companion session

    def action_companion(self) -> None:
        """Open (or go to) the session the launched ones inherit from."""
        if self._opening_companion:
            self.notify(t("companion_opening"))
            return
        self._opening_companion = True
        self.run_companion()

    @work(thread=True)
    def run_companion(self) -> None:
        try:
            if pane := companion_pane():
                self.focus_pane(pane["pane_id"])
                return
            self.call_from_thread(self.notify, t("companion_opening"))
            resumed = open_companion()
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self.notify, t("companion_failed", error=e), severity="error")
            return
        finally:
            self._opening_companion = False
        self.call_from_thread(
            self.notify, t("companion_resumed") if resumed else t("companion_opened"))

    def focus_pane(self, pane_id: str) -> None:
        try:
            herdr("agent", "focus", pane_id)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify, t("companion_focus_failed", error=e), severity="error")

    # ---- session launch / misc

    def action_launch(self) -> None:
        card = self.focused_card()
        if not card:
            return
        key = card.issue.key
        if key in self._launching:
            self.notify(t("launching_already", key=key))
            return
        pane = self.sessions.get(key)
        statuses = agent_statuses()
        # Drop the mapping only when the answer positively lacks the pane. A
        # None answer (the herdr call failed) proves nothing about the pane.
        if pane and statuses is not None and pane not in statuses:
            self.sessions = update_map(SESSIONS_PATH, drop_keys=(key,))
            pane = None
        if not pane and (pane := find_session_pane(key)):
            # Re-associate if the mapping was lost but the issue's agent is alive
            self.sessions = update_map(SESSIONS_PATH, {key: pane})
        if pane:
            self.focus_session(key, pane)
            return
        self._launching.add(key)
        self.notify(t("launching", key=key))
        self.run_launch(card.issue)

    @work(thread=True)
    def focus_session(self, key: str, pane: str) -> None:
        """Focus an existing session's tab.

        Nothing is sent to the agent — the board's preview pane already shows
        its last reply — so going back to a session never interrupts it and
        never costs it a turn.
        """
        try:
            pane_info = herdr("pane", "get", pane)
            tab_id = find_key(pane_info, "tab_id")
            if not tab_id:
                raise RuntimeError(t("no_tab_id", pane=pane))
            herdr("tab", "focus", str(tab_id))
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.notify, t("focus_failed", key=key, error=e), severity="error")
            return
        # Keep the recorded Claude session current (and backfill sessions
        # launched before it was recorded), so a relaunch can resume it.
        if session := (find_key(pane_info, "agent_session") or {}).get("value"):
            self.record_claude_session(key, str(session))

    @work(thread=True)
    def run_launch(self, issue: Issue) -> None:
        try:
            # The description is a nice-to-have for the prompt; never let
            # fetching it block the launch.
            description = self.jira.description(issue.key)
        except Exception:  # noqa: BLE001
            description = ""
        try:
            pane, session, resumed = launch_claude(
                issue, self.cfg, description, self.claude_sessions.get(issue.key, ""))
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self.notify, t("launch_failed", error=e), severity="error")
            return
        finally:
            self._launching.discard(issue.key)
        self.sessions = update_map(SESSIONS_PATH, {issue.key: pane})
        if session:
            self.record_claude_session(issue.key, session)
        self.call_from_thread(
            self.notify, t("resumed" if resumed else "launched", key=issue.key))
        self.update_badges()

    def record_claude_session(self, key: str, session_id: str) -> None:
        """Remember the issue's Claude session so a later launch can resume it."""
        if self.claude_sessions.get(key) != session_id:
            self.claude_sessions = update_map(CLAUDE_SESSIONS_PATH, {key: session_id})

    def action_open_browser(self) -> None:
        card = self.focused_card()
        if card:
            open_url(f"{self.cfg.site}/browse/{card.issue.key}")


# ---------------------------------------------------------------- dump (no TUI)

def badge_of(issue: Issue, statuses: dict[str, str], sessions: dict[str, str]) -> str:
    """Agent status for the issue's session, or "" when it has none."""
    pane = sessions.get(issue.key)
    return statuses.get(pane, "") if pane else ""


def dump_text(cfg: Config, issues: list[Issue], statuses: dict[str, str],
              sessions: dict[str, str]) -> str:
    """The board as plain text, for reading outside the TUI (`--dump`)."""
    lines = [f"JQL: {cfg.jql}", f"exclude_statuses: {cfg.exclude_statuses}"]
    for cat, title in CATEGORY_COLUMNS:
        column = [i for i in issues if i.category == cat]
        lines.append(f"\n== {title} ({len(column)}) ==")
        for issue in column:
            prio = f" {label}" if (label := board_priority_label(cfg, issue)) else ""
            badge = f" <{status}>" if (status := badge_of(issue, statuses, sessions)) else ""
            badge += "".join(f" <{p.display}>"
                             for p in phase_labels_of(issue, cfg.phase_labels))
            lines.append(
                f"  {issue.key}{prio} [{issue.status}]{badge} ({issue.issuetype}, "
                f"{t('created_label')} {issue.created or '-'}, "
                f"{t('due_label')} {issue.duedate or '-'}) {issue.summary}")
            lines.append(f"    {cfg.site}/browse/{issue.key}")
    return "\n".join(lines)


def dump_json(cfg: Config, issues: list[Issue], statuses: dict[str, str],
              sessions: dict[str, str]) -> str:
    """The same board as machine-readable JSON (`--dump --json`)."""
    columns = [
        {"category": cat, "title": title,
         "issues": [{"key": i.key, "summary": i.summary, "status": i.status,
                     "issuetype": i.issuetype, "created": i.created, "duedate": i.duedate,
                     "agent_status": badge_of(i, statuses, sessions) or None,
                     "phase_labels": [p.display for p in phase_labels_of(i, cfg.phase_labels)],
                     "priority": i.priority or None,
                     "epic": {"key": i.epic_key, "name": i.epic_name} if i.epic_key else None,
                     "board_priority": board_priority_label(cfg, i) or None,
                     "url": f"{cfg.site}/browse/{i.key}"}
                    for i in issues if i.category == cat]}
        for cat, title in CATEGORY_COLUMNS
    ]
    return json.dumps({"jql": cfg.jql, "exclude_statuses": cfg.exclude_statuses,
                       "columns": columns}, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if "--check" in sys.argv:
        Config.load()
        print("config OK")
        sys.exit(0)
    if "--dump" in sys.argv:
        cfg = Config.load()
        issues = Jira(cfg).search()
        # agent_statuses() returns None when herdr is unreachable (e.g. run from
        # a plain shell), which just leaves the badges off.
        render = dump_json if "--json" in sys.argv else dump_text
        print(render(cfg, issues, agent_statuses() or {}, load_sessions()))
        sys.exit(0)
    BoardApp().run()
