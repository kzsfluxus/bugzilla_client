#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from bugzilla_api import BugzillaClient

APP_NAME = "bugzilla-tool"
DEFAULT_CONFIG_PATHS = [
    Path("./config.yaml"),
    Path.home() / ".config" / APP_NAME / "config.yaml",
]


def load_config(explicit_path: Optional[str]) -> dict:
    if explicit_path:
        path = Path(explicit_path)
        if not path.exists():
            raise SystemExit(f"Config not found: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for path in DEFAULT_CONFIG_PATHS:
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    searched = ", ".join(str(p) for p in DEFAULT_CONFIG_PATHS)
    raise SystemExit(f"No config.yaml found. Searched: {searched}")


def setup_logging(cfg: dict, debug: bool) -> None:
    log_cfg = cfg.get("logging", {})
    level_name = "DEBUG" if debug else log_cfg.get("level", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    log_file = log_cfg.get("file")
    if log_file:
        log_path = Path(os.path.expanduser(log_file))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )


def get_bugzilla_cfg(cfg: dict) -> Dict[str, object]:
    bc = cfg.get("bugzilla", {})
    if not isinstance(bc, dict):
        raise SystemExit("config.yaml: bugzilla section must be a mapping")
    return bc


def get_login(cfg: dict) -> str:
    bc = get_bugzilla_cfg(cfg)
    login = str(bc.get("login", "") or "")
    if not login and not bc.get("api_key"):
        raise SystemExit("config.yaml: missing bugzilla.login (or api_key)")
    return login


def get_query_user(cfg: dict) -> str:
    bc = get_bugzilla_cfg(cfg)
    query_user = str(bc.get("query_user", "") or "")
    if query_user:
        return query_user

    login = str(bc.get("login", "") or "")
    if login:
        return login

    raise SystemExit("config.yaml: missing bugzilla.login or bugzilla.query_user")


def get_review_fields(cfg: dict) -> List[str]:
    fields = get_bugzilla_cfg(cfg).get("review_fields", [])
    if isinstance(fields, str):
        fields = [x.strip() for x in fields.split(",") if x.strip()]
    return list(fields)


def resolve_password(bugzilla_cfg: Dict[str, object]) -> tuple[str, str]:
    password = str(bugzilla_cfg.get("password", "") or "")
    if password:
        return password, "config"

    password_env = str(bugzilla_cfg.get("password_env", "") or "BUGZILLA_PASSWORD")
    env_value = os.environ.get(password_env, "")
    if env_value:
        return env_value, f"env:{password_env}"

    return "", "missing"


def get_client(cfg: dict) -> BugzillaClient:
    bc = get_bugzilla_cfg(cfg)
    password, _ = resolve_password(bc)
    return BugzillaClient(
        str(bc.get("url", "") or ""),
        api_key=str(bc.get("api_key", "") or "") or None,
        login=str(bc.get("login", "") or "") or None,
        password=password or None,
        timeout=int(bc.get("timeout", 25)),
        max_retries=int(bc.get("max_retries", 3)),
        retry_backoff_seconds=float(bc.get("retry_backoff_seconds", 1.5)),
        verify_tls=bool(bc.get("verify_tls", True)),
    )


def fmt_dt(value: str) -> str:
    return value.replace("T", " ")[:19] if value else ""
