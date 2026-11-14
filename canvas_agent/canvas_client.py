"""Read-only Canvas API client.

Deliberately has NO write methods: nothing here can submit assignments or change the account.
Every call returns a Result; failures (401/403/404/network) are reported, never papered over.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .storage import Store


@dataclass
class Result:
    ok: bool
    data: Any = None
    status: int = 0
    error: str = ""

    @property
    def unavailable_reason(self) -> str:
        if self.ok:
            return ""
        if self.status in (401, 403):
            return f"permission denied (HTTP {self.status})"
        if self.status == 404:
            return "not found or disabled for this course (HTTP 404)"
        return self.error or f"HTTP {self.status}"


class CanvasClient:
    def __init__(self, base_url: str, token: str, store: Store | None = None, timeout: int = 30):
        if not base_url.startswith("https://") and "localhost" not in base_url and "127.0.0.1" not in base_url:
            raise ValueError("CANVAS_BASE_URL must be https:// (token would be sent in clear text).")
        self.base = base_url.rstrip("/")
        self._token = token
        self.store = store
        self.timeout = timeout
        self.requests_made = 0
        self.cache_hits = 0

    # ------------------------------------------------------------------ transport
    def _fetch(self, url: str, binary: bool = False):
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        cached = None
        if self.store and not binary:
            cached = self.store.get_cache(url)
            if cached and cached["etag"]:
                headers["If-None-Match"] = cached["etag"]
        for attempt in range(3):
            self.requests_made += 1
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read()
                    if binary:
                        return r.status, body, ""
                    text = body.decode("utf-8")
                    link = r.headers.get("Link", "")
                    etag = r.headers.get("ETag", "")
                    if self.store and etag:
                        self.store.put_cache(url, etag, text, link)
                    return r.status, text, link
            except urllib.error.HTTPError as e:
                if e.code == 304 and cached:
                    self.cache_hits += 1
                    return 200, cached["body"], cached["links"] or ""
                if e.code in (429, 503) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return e.code, "", ""
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return 0, "", f"network error: {type(e).__name__}"
        return 0, "", "network error"

    def get(self, path: str, params: list[tuple[str, str]] | None = None) -> Result:
        url = self._url(path, params)
        status, body, extra = self._fetch(url)
        if status != 200:
            return Result(False, status=status, error=extra)
        try:
            return Result(True, json.loads(body), 200)
        except json.JSONDecodeError:
            return Result(False, status=200, error="invalid JSON in response")

    def paginate(self, path: str, params: list[tuple[str, str]] | None = None, max_pages: int = 20) -> Result:
        params = list(params or [])
        if not any(k == "per_page" for k, _ in params):
            params.append(("per_page", "100"))
        url, items = self._url(path, params), []
        for _ in range(max_pages):
            status, body, link = self._fetch(url)
            if status != 200:
                return Result(False, status=status, error=link if status == 0 else "")
            try:
                page = json.loads(body)
            except json.JSONDecodeError:
                return Result(False, status=200, error="invalid JSON in response")
            items.extend(page if isinstance(page, list) else [page])
            m = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
            if not m:
                return Result(True, items, 200)
            url = m.group(1)
        return Result(True, items, 200)  # truncated at max_pages

    def download(self, url: str, max_bytes: int) -> Result:
        """Download a file (read-only GET). Refuses non-Canvas hosts so the token is never leaked."""
        if urllib.parse.urlparse(url).netloc != urllib.parse.urlparse(self.base).netloc:
            return Result(False, error="refusing to send Canvas token to a different host")
        status, body, extra = self._fetch(url, binary=True)
        if status != 200:
            return Result(False, status=status, error=extra)
        if len(body) > max_bytes:
            return Result(False, status=200, error=f"file larger than limit ({max_bytes} bytes)")
        return Result(True, body, 200)

    def _url(self, path: str, params) -> str:
        if path.startswith("http"):
            return path
        q = urllib.parse.urlencode(params or [])
        return f"{self.base}/api/v1{path}" + (f"?{q}" if q else "")

    # ------------------------------------------------------------------ permission verification
    def probe_permissions(self) -> dict[str, dict]:
        """Verify what this token can actually read BEFORE monitoring relies on it."""
        caps: dict[str, dict] = {}

        def rec(name: str, res: Result):
            caps[name] = {"available": res.ok, "reason": res.unavailable_reason}

        me = self.get("/users/self")
        rec("profile", me)
        courses = self.get("/courses", [("enrollment_state", "active"), ("per_page", "1")])
        rec("courses", courses)
        if courses.ok and courses.data:
            cid = courses.data[0]["id"]
            rec("assignments", self.get(f"/courses/{cid}/assignments", [("per_page", "1")]))
            rec("announcements", self.get("/announcements", [("context_codes[]", f"course_{cid}"), ("per_page", "1")]))
            rec("files", self.get(f"/courses/{cid}/files", [("per_page", "1")]))
            rec("calendar", self.get("/calendar_events", [("context_codes[]", f"course_{cid}"), ("per_page", "1")]))
        return caps
