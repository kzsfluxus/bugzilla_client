#!/usr/bin/env python3
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import requests


class BugzillaError(Exception):
    """Raised for Bugzilla/API related errors."""


@dataclass
class BugSummary:
    bug_id: int
    summary: str
    priority: str
    severity: str
    status: str
    last_change_time: str
    assigned_to: str = ""


@dataclass
class BugDetail:
    bug_id: int
    summary: str
    priority: str
    severity: str
    status: str
    last_change_time: str
    assigned_to: str
    creator: str
    product: str
    component: str
    platform: str
    op_sys: str
    version: str
    whiteboard: str
    keywords: List[str]
    see_also: List[str]
    comments: List[Dict[str, Any]]


class BugzillaClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        login: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = 25,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.5,
        verify_tls: bool = True,
    ) -> None:
        if not base_url:
            raise BugzillaError("Missing Bugzilla base URL.")

        has_api_key = bool(api_key)
        has_login_password = bool(login and password)

        if not has_api_key and not has_login_password:
            raise BugzillaError(
                "Missing credentials. Configure either bugzilla.api_key "
                "or bugzilla.login + password/password_env/.secrets."
            )

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.verify_tls = verify_tls

        self.api_key = str(api_key) if api_key else None
        self.login_name = str(login) if login else None
        self.password = str(password) if password else None
        self.token: Optional[str] = None

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

        if has_api_key:
            self.session.headers["X-BUGZILLA-API-KEY"] = self.api_key
            self.auth_mode = "api_key"
        else:
            self.auth_mode = "login_password"

    def _url(self, path: str) -> str:
        return f"{self.base_url}/rest{path}"

    def _inject_auth(self, method: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if self.auth_mode == "api_key":
            return kwargs

        if self.token:
            if method.upper() == "GET":
                params = dict(kwargs.get("params") or {})
                params.setdefault("Bugzilla_token", self.token)
                kwargs["params"] = params
            else:
                payload = dict(kwargs.get("json") or {})
                payload.setdefault("Bugzilla_token", self.token)
                kwargs["json"] = payload

        return kwargs

    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify_tls)

        if self.auth_mode == "login_password" and path != "/login" and not self.token:
            self.login()

        kwargs = self._inject_auth(method, dict(kwargs))
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(method, self._url(path), **kwargs)
                if response.ok:
                    return response.json()

                try:
                    payload = response.json()
                    message = payload.get("message") or payload.get("error") or response.text
                except Exception:
                    message = response.text

                if 500 <= response.status_code < 600 and attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * attempt)
                    continue

                raise BugzillaError(f"HTTP {response.status_code}: {message}")

            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * attempt)
                    continue
                raise BugzillaError(f"Network error: {exc}") from exc

        raise BugzillaError(str(last_exc) if last_exc else "Unknown request error")

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request("GET", path, params=params or {})

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", path, json=payload)

    def login(self, login: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
        if self.auth_mode == "api_key":
            return {"token": None, "mode": "api_key"}

        login_name = login or self.login_name
        password_value = password or self.password

        if not login_name or not password_value:
            raise BugzillaError("Missing login/password for Bugzilla login.")

        payload = self._request(
            "GET",
            "/login",
            params={
                "login": login_name,
                "password": password_value,
            },
        )

        token = payload.get("token")
        if not token:
            raise BugzillaError("Login succeeded but no token was returned.")

        self.token = str(token)
        return payload

    def whoami(self) -> Dict[str, Any]:
        return self._get("/whoami")

    def get_fields(self) -> Dict[str, Any]:
        return self._get("/field/bug")

    def list_bugs(
        self,
        params: Dict[str, Any],
        *,
        include_fields: Optional[Iterable[str]] = None,
        page_size: int = 200,
        max_results: int = 1000,
    ) -> List[BugSummary]:
        base_params = dict(params)
        if include_fields:
            base_params["include_fields"] = ",".join(include_fields)

        results: List[BugSummary] = []
        offset = 0

        while offset < max_results:
            query = dict(base_params)
            query["limit"] = min(page_size, max_results - offset)
            query["offset"] = offset
            payload = self._get("/bug", params=query)
            bugs = payload.get("bugs", [])
            if not bugs:
                break

            results.extend(self._parse_bug_summary(b) for b in bugs)

            if len(bugs) < query["limit"]:
                break
            offset += len(bugs)

        return results

    def list_assigned(
        self,
        query_user: str,
        *,
        statuses: Optional[List[str]] = None,
        priorities: Optional[List[str]] = None,
        search: str = "",
        max_results: int = 1000,
    ) -> List[BugSummary]:
        params: Dict[str, Any] = {"assigned_to": query_user}
        if statuses:
            params["bug_status"] = statuses
        if priorities:
            params["priority"] = priorities
        if search:
            params["summary"] = search

        return self.list_bugs(
            params,
            include_fields=[
                "id",
                "summary",
                "priority",
                "severity",
                "status",
                "last_change_time",
                "assigned_to",
            ],
            max_results=max_results,
        )

    def list_review(
        self,
        query_user: str,
        review_fields: List[str],
        *,
        statuses: Optional[List[str]] = None,
        priorities: Optional[List[str]] = None,
        search: str = "",
        max_results: int = 1000,
    ) -> List[BugSummary]:
        seen: set[int] = set()
        results: List[BugSummary] = []

        for field in review_fields:
            params: Dict[str, Any] = {field: query_user}
            if statuses:
                params["bug_status"] = statuses
            if priorities:
                params["priority"] = priorities
            if search:
                params["summary"] = search

            try:
                bugs = self.list_bugs(
                    params,
                    include_fields=[
                        "id",
                        "summary",
                        "priority",
                        "severity",
                        "status",
                        "last_change_time",
                        "assigned_to",
                    ],
                    max_results=max_results,
                )
            except BugzillaError:
                continue

            for bug in bugs:
                if bug.bug_id not in seen:
                    results.append(bug)
                    seen.add(bug.bug_id)

        return results

    def get_bug(self, bug_id: int) -> BugDetail:
        bug_data = self._get(
            f"/bug/{bug_id}",
            params={
                "include_fields": ",".join(
                    [
                        "id",
                        "summary",
                        "priority",
                        "severity",
                        "status",
                        "last_change_time",
                        "assigned_to",
                        "creator",
                        "product",
                        "component",
                        "platform",
                        "op_sys",
                        "version",
                        "whiteboard",
                        "keywords",
                        "see_also",
                    ]
                )
            },
        )

        comments_data = self._get(f"/bug/{bug_id}/comment")
        bugs = bug_data.get("bugs", [])
        if not bugs:
            raise BugzillaError(f"Bug not found: {bug_id}")

        bug = bugs[0]
        comments = comments_data.get("bugs", {}).get(str(bug_id), {}).get("comments", [])

        return BugDetail(
            bug_id=int(bug.get("id", 0)),
            summary=str(bug.get("summary", "")),
            priority=str(bug.get("priority", "")),
            severity=str(bug.get("severity", "")),
            status=str(bug.get("status", "")),
            last_change_time=str(bug.get("last_change_time", "")),
            assigned_to=self._user_to_str(bug.get("assigned_to")),
            creator=self._user_to_str(bug.get("creator")),
            product=str(bug.get("product", "")),
            component=str(bug.get("component", "")),
            platform=str(bug.get("platform", "")),
            op_sys=str(bug.get("op_sys", "")),
            version=str(bug.get("version", "")),
            whiteboard=str(bug.get("whiteboard", "")),
            keywords=[str(x) for x in bug.get("keywords", [])],
            see_also=[str(x) for x in bug.get("see_also", [])],
            comments=[
                {
                    "author": self._user_to_str(c.get("author")),
                    "time": str(c.get("time", "")),
                    "text": str(c.get("text", "")),
                }
                for c in comments
            ],
        )

    def add_comment(self, bug_id: int, comment: str, work_time: Optional[float] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"comment": comment}
        if work_time is not None:
            payload["work_time"] = work_time
        return self._post(f"/bug/{bug_id}/comment", payload)

    @staticmethod
    def _user_to_str(value: Any) -> str:
        if isinstance(value, dict):
            return str(
                value.get("real_name")
                or value.get("name")
                or value.get("email")
                or value.get("id")
                or ""
            )
        return str(value or "")

    @staticmethod
    def _parse_bug_summary(b: Dict[str, Any]) -> BugSummary:
        return BugSummary(
            bug_id=int(b.get("id", 0)),
            summary=str(b.get("summary", "")),
            priority=str(b.get("priority", "")),
            severity=str(b.get("severity", "")),
            status=str(b.get("status", "")),
            last_change_time=str(b.get("last_change_time", "")),
            assigned_to=BugzillaClient._user_to_str(b.get("assigned_to")),
        )