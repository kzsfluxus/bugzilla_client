#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import List, Optional

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Static, TabbedContent, TabPane, TextArea

from bugzilla_api import BugzillaClient, BugzillaError, BugSummary
from shared_config import fmt_dt, get_client, get_query_user, get_review_fields, load_config, setup_logging


class CommentSubmitted(Message):
    def __init__(self, bug_id: int, comment: str, hours: Optional[float]) -> None:
        self.bug_id = bug_id
        self.comment = comment
        self.hours = hours
        super().__init__()


class CommentScreen(ModalScreen[None]):
    CSS = """
    CommentScreen {
        align: center middle;
    }
    #comment_dialog {
        width: 90;
        height: 24;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }
    #comment_text { height: 10; }
    .comment-row { height: auto; margin: 1 0; }
    .comment-label { width: 16; }
    .comment-input { width: 1fr; }
    #comment_buttons { height: 3; align-horizontal: right; margin-top: 1; }
    """

    def __init__(self, bug_id: int) -> None:
        super().__init__()
        self.bug_id = bug_id

    def compose(self) -> ComposeResult:
        with Vertical(id="comment_dialog"):
            yield Static(f"Új komment a bughoz: #{self.bug_id}")
            with Horizontal(classes="comment-row"):
                yield Label("Óraszám:", classes="comment-label")
                yield Input(placeholder="pl. 1.5", id="hours_input", classes="comment-input")
            yield Label("Komment:")
            yield TextArea(id="comment_text")
            with Horizontal(id="comment_buttons"):
                yield Button("Mentés", variant="success", id="save_comment")
                yield Button("Mégse", variant="default", id="cancel_comment")

    @on(Button.Pressed, "#cancel_comment")
    def cancel(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#save_comment")
    def submit(self) -> None:
        hours_input = self.query_one("#hours_input", Input)
        comment_text = self.query_one("#comment_text", TextArea)

        raw_comment = comment_text.text.strip()
        if not raw_comment:
            self.app.notify("A komment nem lehet üres.", severity="error")
            return

        hours: Optional[float] = None
        if hours_input.value.strip():
            try:
                hours = float(hours_input.value.strip().replace(",", "."))
            except ValueError:
                self.app.notify("Hibás óraszám.", severity="error")
                return

        self.app.post_message(CommentSubmitted(self.bug_id, raw_comment, hours))
        self.dismiss()


class BugzillaTUI(App):
    TITLE = "Bugzilla Tool"
    SUB_TITLE = "Assigned / Review / Detail"

    CSS = """
    Screen { layout: vertical; }
    #toolbar { height: 3; padding: 0 1; }
    #main { height: 1fr; }
    #left-pane { width: 2fr; }
    #right-pane { width: 3fr; border-left: solid $surface-lighten-1; padding-left: 1; }
    #filters_row Input { width: 1fr; margin-right: 1; }
    #filters_row Button { margin-right: 1; }
    #detail_meta { height: 12; border: round $surface-lighten-1; padding: 1; margin-bottom: 1; }
    #detail_comments { height: 1fr; border: round $surface-lighten-1; }
    #detail_actions { height: 3; margin-top: 1; }
    DataTable { height: 1fr; }
    """

    BINDINGS = [
        ("q", "quit", "Kilépés"),
        ("r", "reload", "Frissítés"),
        ("c", "comment_current", "Komment"),
        ("/", "focus_search", "Keresés"),
    ]

    def __init__(self, client: BugzillaClient, cfg: dict) -> None:
        super().__init__()
        self.client = client
        self.cfg = cfg
        self.query_user = get_query_user(cfg)
        self.review_fields = get_review_fields(cfg)
        self.selected_bug_id: Optional[int] = None
        self.assigned_rows: List[BugSummary] = []
        self.review_rows: List[BugSummary] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="toolbar"):
            with Horizontal(id="filters_row"):
                yield Input(placeholder="Keresés summary-ben...", id="search_input")
                yield Input(placeholder="Statuszok pl: NEW,ASSIGNED", id="status_input")
                yield Input(placeholder="Priority-k pl: P1,P2", id="priority_input")
                yield Button("Apply", id="apply_filters", variant="primary")
                yield Button("Reset", id="reset_filters")
        with Horizontal(id="main"):
            with Vertical(id="left-pane"):
                with TabbedContent():
                    with TabPane("Assigned", id="tab-assigned"):
                        yield DataTable(id="assigned_table")
                    with TabPane("Review", id="tab-review"):
                        yield DataTable(id="review_table")
            with Vertical(id="right-pane"):
                yield Static("Nincs kiválasztott bug", id="detail_title")
                yield Static("", id="detail_meta")
                yield RichLog(id="detail_comments", wrap=True, markup=False)
                with Horizontal(id="detail_actions"):
                    yield Button("Komment hozzáadása", id="add_comment_btn", variant="primary")
                    yield Button("Frissítés", id="refresh_detail_btn")
        yield Footer()

    def on_mount(self) -> None:
        for table_id in ("assigned_table", "review_table"):
            table = self.query_one(f"#{table_id}", DataTable)
            table.cursor_type = "row"
            table.zebra_stripes = True
            table.add_columns("ID", "Priority", "Severity", "Status", "Last Change", "Summary")
        self.action_reload()

    def current_filters(self) -> tuple[str, List[str], List[str]]:
        search = self.query_one("#search_input", Input).value.strip()
        statuses = [s.strip() for s in self.query_one("#status_input", Input).value.split(",") if s.strip()]
        priorities = [s.strip() for s in self.query_one("#priority_input", Input).value.split(",") if s.strip()]
        return search, statuses, priorities

    def action_focus_search(self) -> None:
        self.query_one("#search_input", Input).focus()

    def action_reload(self) -> None:
        self.run_worker(self._load_all, exclusive=True, thread=True)

    def _load_all(self) -> None:
        search, statuses, priorities = self.current_filters()
        assigned = self.client.list_assigned(
            self.query_user,
            statuses=statuses or None,
            priorities=priorities or None,
            search=search,
            max_results=200,
        )
        review = self.client.list_review(
            self.query_user,
            self.review_fields,
            statuses=statuses or None,
            priorities=priorities or None,
            search=search,
            max_results=200,
        )

        def update_ui() -> None:
            self.assigned_rows = assigned
            self.review_rows = review
            self._fill_table("assigned_table", assigned)
            self._fill_table("review_table", review)

            if self.selected_bug_id:
                self.run_worker(lambda: self._load_bug_detail(self.selected_bug_id), thread=True)
            elif assigned:
                self.selected_bug_id = assigned[0].bug_id
                self.run_worker(lambda: self._load_bug_detail(assigned[0].bug_id), thread=True)
            else:
                self._clear_detail()
                self.notify("Nincs találat.", severity="warning")

        self.call_from_thread(update_ui)

    def _fill_table(self, table_id: str, bugs: List[BugSummary]) -> None:
        table = self.query_one(f"#{table_id}", DataTable)
        table.clear(columns=False)
        for bug in bugs:
            table.add_row(
                str(bug.bug_id),
                bug.priority,
                bug.severity,
                bug.status,
                fmt_dt(bug.last_change_time),
                bug.summary,
                key=str(bug.bug_id),
            )

    def _clear_detail(self) -> None:
        self.query_one("#detail_title", Static).update("Nincs kiválasztott bug")
        self.query_one("#detail_meta", Static).update("")
        self.query_one("#detail_comments", RichLog).clear()

    def _load_bug_detail(self, bug_id: int) -> None:
        detail = self.client.get_bug(bug_id)

        def update_ui() -> None:
            self.selected_bug_id = bug_id
            self.query_one("#detail_title", Static).update(f"#{detail.bug_id} — {detail.summary}")
            meta = "\n".join(
                [
                    f"Priority: {detail.priority}",
                    f"Severity: {detail.severity}",
                    f"Status: {detail.status}",
                    f"Assigned to: {detail.assigned_to}",
                    f"Creator: {detail.creator}",
                    f"Product / Component: {detail.product} / {detail.component}",
                    f"Platform / OS: {detail.platform} / {detail.op_sys}",
                    f"Version: {detail.version}",
                    f"Last change: {fmt_dt(detail.last_change_time)}",
                    f"Keywords: {', '.join(detail.keywords) if detail.keywords else '-'}",
                    f"Whiteboard: {detail.whiteboard or '-'}",
                ]
            )
            self.query_one("#detail_meta", Static).update(meta)
            log = self.query_one("#detail_comments", RichLog)
            log.clear()
            if not detail.comments:
                log.write("Nincsenek kommentek.")
            else:
                for c in detail.comments:
                    author = c.get("author", "ismeretlen")
                    when = fmt_dt(c.get("time", ""))
                    text = c.get("text", "")
                    log.write(f"[{when}] {author}")
                    log.write(text)
                    log.write("-" * 70)

        self.call_from_thread(update_ui)

    @on(Button.Pressed, "#apply_filters")
    def apply_filters(self) -> None:
        self.action_reload()

    @on(Button.Pressed, "#reset_filters")
    def reset_filters(self) -> None:
        self.query_one("#search_input", Input).value = ""
        self.query_one("#status_input", Input).value = ""
        self.query_one("#priority_input", Input).value = ""
        self.action_reload()

    @on(Button.Pressed, "#refresh_detail_btn")
    def refresh_detail(self) -> None:
        if self.selected_bug_id:
            self.run_worker(lambda: self._load_bug_detail(self.selected_bug_id), thread=True)

    @on(Button.Pressed, "#add_comment_btn")
    def add_comment(self) -> None:
        self.action_comment_current()

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        try:
            bug_id = int(str(event.row_key.value))
        except Exception:
            return
        self.run_worker(lambda: self._load_bug_detail(bug_id), thread=True)

    def action_comment_current(self) -> None:
        if self.selected_bug_id is None:
            self.notify("Nincs kiválasztott bug.", severity="warning")
            return
        self.push_screen(CommentScreen(self.selected_bug_id))

    @on(CommentSubmitted)
    def save_comment(self, message: CommentSubmitted) -> None:
        self.run_worker(lambda: self._save_comment(message.bug_id, message.comment, message.hours), thread=True)

    def _save_comment(self, bug_id: int, comment: str, hours: Optional[float]) -> None:
        self.client.add_comment(bug_id, comment, hours)

        def update_ui() -> None:
            self.notify(f"Komment elmentve a #{bug_id} bughoz.", severity="information")
            self.run_worker(lambda: self._load_bug_detail(bug_id), thread=True)

        self.call_from_thread(update_ui)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bugzilla TUI")
    parser.add_argument("-c", "--config", help="Path to YAML config")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    setup_logging(cfg, args.debug)
    client = get_client(cfg)
    try:
        app = BugzillaTUI(client, cfg)
        app.run()
        return 0
    except BugzillaError as exc:
        print(f"Error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
