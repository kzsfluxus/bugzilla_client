#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from typing import Iterable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from bugzilla_api import BugzillaError
from shared_config import (
    fmt_dt,
    get_bugzilla_cfg,
    get_client,
    get_login,
    get_query_user,
    get_review_fields,
    load_config,
    resolve_password,
    setup_logging,
)

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bugzilla CLI client")
    parser.add_argument("-c", "--config", help="Path to YAML config")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Check connectivity and auth")
    sub.add_parser("fields", help="List available bug fields")

    def add_filters(p: argparse.ArgumentParser) -> None:
        p.add_argument("--status", action="append", default=[], help="Filter by bug status")
        p.add_argument("--priority", action="append", default=[], help="Filter by priority")
        p.add_argument("--search", default="", help="Filter by summary")
        p.add_argument("--limit", type=int, default=200, help="Maximum results")

    p_assigned = sub.add_parser("assigned", help="List assigned bugs")
    add_filters(p_assigned)

    p_review = sub.add_parser("review", help="List review bugs")
    add_filters(p_review)

    p_show = sub.add_parser("show", help="Show one bug")
    p_show.add_argument("bug_id", type=int)

    p_comment = sub.add_parser("comment", help="Add comment")
    p_comment.add_argument("bug_id", type=int)
    p_comment.add_argument("-m", "--message", help="Comment text")
    p_comment.add_argument("--hours", type=float, help="Optional work_time")
    p_comment.add_argument("--stdin", action="store_true", help="Read body from stdin")

    return parser.parse_args()


def render_bug_table(title: str, bugs: Iterable) -> None:
    table = Table(title=title)
    table.add_column("ID", justify="right")
    table.add_column("Priority")
    table.add_column("Severity")
    table.add_column("Status")
    table.add_column("Last Change")
    table.add_column("Summary", overflow="fold")

    bugs = list(bugs)
    if not bugs:
        console.print("[yellow]No bugs found.[/yellow]")
        return

    for bug in bugs:
        table.add_row(
            str(bug.bug_id),
            bug.priority,
            bug.severity,
            bug.status,
            fmt_dt(bug.last_change_time),
            bug.summary,
        )
    console.print(table)


def do_check(client, cfg: dict) -> int:
    bc = get_bugzilla_cfg(cfg)
    _, password_source = resolve_password(bc)
    auth_mode = getattr(client, "auth_mode", "unknown")

    info = [f"Auth mode: {auth_mode}"]

    if auth_mode == "api_key":
        who = client.whoami()
        info.extend(
            [
                f"Authenticated as: {who.get('name', 'unknown')}",
                f"Login: -",
                f"Query user: {get_query_user(cfg)}",
                f"Password source: -",
                "Token acquired: n/a",
            ]
        )
    else:
        login = get_login(cfg)
        password, _ = resolve_password(bc)
        payload = client.login(login, password)
        info.extend(
            [
                f"Login successful for: {login}",
                f"Query user: {get_query_user(cfg)}",
                f"Password source: {password_source}",
                f"User id: {payload.get('id', '-')}",
                f"Token acquired: {'yes' if payload.get('token') else 'no'}",
            ]
        )

    console.print(Panel.fit("\n".join(info), title="Check"))
    return 0


def do_fields(client) -> int:
    payload = client.get_fields()
    table = Table(title="Bugzilla Fields")
    table.add_column("Name")
    table.add_column("Display Name")
    table.add_column("Type")

    for f in payload.get("fields", []):
        table.add_row(
            str(f.get("name", "")),
            str(f.get("display_name", "")),
            str(f.get("type", "")),
        )

    console.print(table)
    return 0


def do_assigned(client, cfg: dict, args: argparse.Namespace) -> int:
    bugs = client.list_assigned(
        get_query_user(cfg),
        statuses=args.status or None,
        priorities=args.priority or None,
        search=args.search,
        max_results=args.limit,
    )
    render_bug_table("Assigned Bugs", bugs)
    return 0


def do_review(client, cfg: dict, args: argparse.Namespace) -> int:
    bugs = client.list_review(
        get_query_user(cfg),
        get_review_fields(cfg),
        statuses=args.status or None,
        priorities=args.priority or None,
        search=args.search,
        max_results=args.limit,
    )
    render_bug_table("Review Bugs", bugs)
    return 0


def do_show(client, bug_id: int) -> int:
    bug = client.get_bug(bug_id)
    meta = Table.grid(padding=(0, 1))
    meta.add_column(style="bold cyan")
    meta.add_column()

    for k, v in [
        ("ID", bug.bug_id),
        ("Summary", bug.summary),
        ("Priority", bug.priority),
        ("Severity", bug.severity),
        ("Status", bug.status),
        ("Assigned To", bug.assigned_to),
        ("Creator", bug.creator),
        ("Product", bug.product),
        ("Component", bug.component),
        ("Platform", bug.platform),
        ("OS", bug.op_sys),
        ("Version", bug.version),
        ("Last Change", fmt_dt(bug.last_change_time)),
        ("Keywords", ", ".join(bug.keywords) if bug.keywords else "-"),
        ("Whiteboard", bug.whiteboard or "-"),
    ]:
        meta.add_row(str(k), str(v))

    console.print(Panel(meta, title=f"Bug #{bug.bug_id}", expand=False))

    if bug.comments:
        console.print(Text("Comments", style="bold"))
        for c in bug.comments:
            console.print(
                Panel(
                    c.get("text", ""),
                    title=f'{c.get("author", "unknown")} — {fmt_dt(c.get("time", ""))}',
                    expand=False,
                )
            )
    return 0


def do_comment(client, args: argparse.Namespace) -> int:
    if args.stdin:
        body = sys.stdin.read().strip()
    else:
        body = (args.message or "").strip()

    if not body:
        raise SystemExit("Provide comment text via --message or --stdin.")

    client.add_comment(args.bug_id, body, args.hours)
    console.print(f"[green]Comment posted to bug #{args.bug_id}.[/green]")
    return 0


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    setup_logging(cfg, args.debug)
    client = get_client(cfg)

    try:
        if args.command == "check":
            return do_check(client, cfg)
        if args.command == "fields":
            return do_fields(client)
        if args.command == "assigned":
            return do_assigned(client, cfg, args)
        if args.command == "review":
            return do_review(client, cfg, args)
        if args.command == "show":
            return do_show(client, args.bug_id)
        if args.command == "comment":
            return do_comment(client, args)
        raise SystemExit(f"Unknown command: {args.command}")
    except BugzillaError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())