"""Read-only Metaculus API client (PLAN §3.2, §3.3).

This module only issues GET requests. Submitting forecasts and comments belongs to
submit.py (M3) and is deliberately absent here. The token is never logged or put
into exception messages.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from api_backoff import Backoff, Deferred, DEFAULT_PATH
from flab.timeutil import parse_iso
from typing import Any, Callable, Iterator

VERSION = "metaculus_read_v1"

API_BASE = "https://www.metaculus.com/api"
USER_AGENT = "forecast-lab/0.1 (+https://github.com/Louiszhu970814/forecast-lab)"
RETRY_STATUSES = {429, 500, 502, 503, 504}
FORECAST_TYPES = "binary,multiple_choice,numeric,discrete"


class MetaculusError(RuntimeError):
    def __init__(self, status: int | None, path: str, reason: str) -> None:
        super().__init__(f"Metaculus API {path} failed: {status} {reason}")
        self.status = status
        self.path = path


Opener = Callable[[urllib.request.Request, float], Any]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MetaculusError(code, "read", "redirect refused")


def _default_opener(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout)


class MetaculusClient:
    def __init__(self, token: str, *, base: str = API_BASE, timeout: float = 30.0, max_retries: int = 3,
                 opener: Opener = _default_opener, sleep: Callable[[float], None] = time.sleep,
                 backoff: Backoff | None = None, manage_backoff: bool = True) -> None:
        if not token:
            raise ValueError("a Metaculus token is required")
        self._token = token
        self._base = base.rstrip("/")
        self._host = urllib.parse.urlsplit(self._base).netloc
        self._timeout = timeout
        self._max_retries = max_retries
        self._open = opener
        self._sleep = sleep
        # D98: only the real network opener paces; mock openers keep private, unpaced state.
        self._backoff = backoff or (Backoff(DEFAULT_PATH, pace=True) if opener is _default_opener else Backoff())
        if manage_backoff:
            self._open = lambda req, timeout: self._backoff.open(opener, req, timeout)

    def __repr__(self) -> str:  # never expose the token
        return f"MetaculusClient(base={self._base!r})"

    def _get_url(self, url: str) -> dict[str, Any]:
        parts = urllib.parse.urlsplit(url)
        base_path = urllib.parse.urlsplit(self._base).path.rstrip("/") + "/"
        if parts.scheme != "https" or parts.netloc != self._host or parts.fragment \
                or not parts.path.startswith(base_path) or ".." in urllib.parse.unquote(parts.path).split("/"):
            raise MetaculusError(None, "read", "refusing to follow a URL outside the API host or HTTPS API path boundary")
        req = urllib.request.Request(url, method="GET", headers={
            "Authorization": f"Token {self._token}", "Accept": "application/json", "User-Agent": USER_AGENT,
        })
        attempt = 0
        while True:
            try:
                with self._open(req, self._timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in RETRY_STATUSES and attempt < self._max_retries:
                    retry_at = getattr(exc, "retry_at", None)
                    delay = max(2.0 ** attempt, (parse_iso(retry_at) - self._backoff.now()).total_seconds()) if retry_at else 2.0 ** attempt
                    if delay > 60:
                        failure = MetaculusError(exc.code, "read", "API cooldown deferred")
                        failure.retry_at = retry_at
                        raise failure from None
                    self._sleep(delay)
                    attempt += 1
                    continue
                summary = HTTPStatus(exc.code).phrase if exc.code in HTTPStatus._value2member_map_ else "Request rejected"
                raise MetaculusError(exc.code, "read", summary) from None
            except Deferred as exc:
                failure = MetaculusError(exc.status, "read", "API cooldown active")
                failure.retry_at = exc.retry_at
                raise failure from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self._max_retries:
                    self._sleep(2.0 ** attempt)
                    attempt += 1
                    continue
                raise MetaculusError(None, "read", type(exc).__name__) from None

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        return self._get_url(f"{self._base}/{path.lstrip('/')}" + (f"?{query}" if query else ""))

    def list_posts(self, tournament: int, *, statuses: str | list[str] = "open", forecast_type: str | None = FORECAST_TYPES,
                   limit: int = 50, max_pages: int = 20) -> Iterator[dict[str, Any]]:
        """All posts of a tournament, following pagination on the same host only.

        ``tournament`` must be the numeric project ID: the API silently ignores an unknown slug
        and returns every post on the site.
        """
        if isinstance(tournament, bool) or not (isinstance(tournament, int) or (isinstance(tournament, str) and tournament.isdigit())):
            raise ValueError(f"tournament must be a numeric project ID, got {tournament!r}")
        params = {"tournaments": int(tournament), "statuses": statuses,
                  "limit": limit, "offset": 0, "include_description": "true"}
        if forecast_type is not None:
            params["forecast_type"] = forecast_type
        page = self.get("posts/", params)
        for pages_read in range(1, max_pages + 1):
            results = page.get("results") or []
            yield from results
            nxt = page.get("next")
            # The API keeps returning a "next" link after the last result, so an empty page ends the listing.
            if not nxt or not results:
                return
            if pages_read == max_pages:
                break
            page = self._get_url(nxt)
        raise MetaculusError(None, "posts/", f"more than {max_pages} pages")

    def get_post(self, post_id: int) -> dict[str, Any]:
        return self.get(f"posts/{int(post_id)}/")
