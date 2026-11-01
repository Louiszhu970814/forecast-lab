"""Authenticated API transport, api_transport_v1. No write retries; V15 assumptions below.

Only normalized allowlisted receipts leave this boundary. Remote bodies and exception
messages may contain credentials and are never used as diagnostics or ledger evidence.
"""
from __future__ import annotations

import copy
import json
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus

from continuous import current_tournaments
from flab.ids import sha256_hex
from flab.timeutil import parse_iso
from metaculus import API_BASE, USER_AGENT, MetaculusClient
from poll_questions import TournamentIndex, snapshot_from_post, title_key
from submit_contract import ConfirmedFailure, Readback

FORECAST_PATH = "/api/questions/forecast/"
COMMENT_PATH = "/api/comments/create/"

VERSION = "api_transport_v1"
# V15: verify every endpoint, field and query on bot testing area before live.
ACCOUNT_PATH = "users/me/"
ACCOUNT_ID, ACCOUNT_IS_BOT = "id", "is_bot"
POST_QUESTION, FORECASTS = "question", "my_forecasts"
REMOTE_ID = "id"
FORECAST_TIME = "start_time"
FORECAST_BINARY = "probability_yes"
FORECAST_CATEGORIES = "probability_yes_per_category"
COMMENTS_PATH = "comments/"
COMMENT_QUERY_POST, COMMENT_QUERY_AUTHOR = "post_id", "author"
COMMENT_QUERY_LIMIT = "limit"
COMMENT_TIME, COMMENT_TEXT = "created_at", "text"
COMMENT_AUTHOR, COMMENT_POST = "author", "on_post"
PAGE_RESULTS, PAGE_NEXT = "results", "next"
MAX_COMMENT_PAGES = 20


class TransportError(RuntimeError):
    def __init__(self, status=None, summary="API transport unavailable"):
        self.status = status
        super().__init__(f"HTTP {status}: {summary}" if status is not None else summary)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TransportError(code, "redirect refused")


def default_opener(req, timeout):
    return urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout)


class SafeReadClient(MetaculusClient):
    """Keep the existing GET implementation, but never expose remote error text/URLs."""
    def _get_url(self, url):
        try:
            APITransport._check_url(url)
            return super()._get_url(url)
        except Exception as exc:
            raise TransportError(getattr(exc, "status", None), "API read unavailable") from None


class APITransport:
    def __init__(self, token, *, opener=default_opener, timeout=30.0):
        if not isinstance(token, str) or not token:
            raise ValueError("Metaculus token required")
        self._token, self._open, self._timeout = token, opener, timeout
        self._bot_id = None
        # The same host/redirect/error boundary protects authenticated GET pagination.
        self.client = SafeReadClient(token, opener=self._safe_open, timeout=timeout)

    @staticmethod
    def _check_url(url):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.netloc != urllib.parse.urlsplit(API_BASE).netloc \
                or not parts.path.startswith("/api/") or parts.fragment:
            raise TransportError(summary="URL outside HTTPS API refused")

    def _safe_open(self, req, timeout):
        self._check_url(req.full_url)
        try:
            response = self._open(req, timeout)
            status = getattr(response, "status", 200)
            if not isinstance(status, int):
                raise TransportError(summary="unrecognized HTTP status")
            if not 200 <= status < 300:
                response.close()
                raise urllib.error.HTTPError(req.full_url, status, "API request failed", {}, None)
            if hasattr(response, "geturl") and response.geturl() != req.full_url:
                response.close()
                raise TransportError(summary="redirect refused")
            return response
        except urllib.error.HTTPError as exc:
            # Use a local status summary, never the error body/reason (even on 4xx).
            status = exc.code
            exc.close()
            raise urllib.error.HTTPError(req.full_url, status, "API request failed", {}, None) from None
        except Exception:
            raise TransportError(summary="API connection or response unavailable") from None

    def account(self):
        value = self.client.get(ACCOUNT_PATH)
        if not isinstance(value, dict) or type(value.get(ACCOUNT_ID)) is not int or value[ACCOUNT_ID] <= 0 \
                or value.get(ACCOUNT_IS_BOT) is not True:
            raise TransportError(summary="authenticated bot identity unavailable")
        self._bot_id = value[ACCOUNT_ID]
        return {"id": self._bot_id, "is_bot": True}

    def get_post(self, post_id):
        return self.client.get_post(post_id)

    def current_tournaments(self, configured):
        return current_tournaments(self.client, configured=configured)

    def tournament_index(self, configured):
        index = TournamentIndex()
        for pid in configured["excluded_project_ids"]:
            index.project_ids.add(pid)
            for post in self.client.list_posts(pid, statuses=["open", "upcoming"]):
                snap = snapshot_from_post(post, str(pid))
                if snap is None:
                    # Exclusion coverage must include every post, including groups.
                    raise TransportError(summary="unrecognized exclusion post")
                index.post_ids.add(snap["post_id"])
                index.question_ids.add(snap["question_id"])
                key = title_key(snap.get("title"))
                if key:
                    index.title_keys.add(key)
        return index

    def _receipt(self, operation, payload, remote_id, bot_id):
        # Numeric IDs only: an arbitrary string from the API could be a credential echo.
        if type(remote_id) is not int or remote_id <= 0 or type(bot_id) is not int:
            return None
        key = "question_id" if operation == "forecast" else "post_id"
        target = payload[0]["question"] if operation == "forecast" else payload["on_post"]
        return {"id": remote_id, "bot_id": bot_id, key: target, "payload": copy.deepcopy(payload)}

    def write(self, path, payload, *, request_id):
        if path not in (FORECAST_PATH, COMMENT_PATH):
            raise TransportError(summary="write endpoint refused")
        if self._bot_id is None:
            raise TransportError(summary="authenticated bot identity required before write")
        req = urllib.request.Request(API_BASE.removesuffix("/api") + path, method="POST",
            data=json.dumps(payload, allow_nan=False).encode(), headers={
                "Authorization": f"Token {self._token}", "Accept": "application/json",
                "Content-Type": "application/json", "User-Agent": USER_AGENT})
        try:
            with self._safe_open(req, self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = exc.code
            if 400 <= status < 500:
                summary = HTTPStatus(status).phrase if status in HTTPStatus._value2member_map_ else "Request rejected"
                failure = ConfirmedFailure(f"HTTP {status}: {summary}")
                failure.status = status
                raise failure from None
            raise TransportError(status, "write outcome unknown") from None
        except Exception:
            raise TransportError(summary="write outcome unknown") from None
        operation = "forecast" if path == FORECAST_PATH else "comment"
        if operation == "forecast" and isinstance(data, list) and len(data) == 1:
            data = data[0]
        # V15 assumption: successful creation returns an object (forecast may be a list)
        # containing its numeric id. Otherwise defer to readback without resubmitting.
        return (self._receipt(operation, payload, data.get(REMOTE_ID), self._bot_id)
                if isinstance(data, dict) else None) or {}

    def readback(self, operation, intent, *, request_id):
        unknown = Readback(request_id=request_id)
        try:
            if operation not in ("forecast", "comment"):
                return unknown
            if self.account()["id"] != intent["bot_id"]:
                return unknown
            started = parse_iso(intent["request_started_at"])
            payload = intent[f"{operation}_payload"]
            if operation == "forecast":
                post = self.get_post(intent["post_id"])
                question = post[POST_QUESTION]
                if type(post[REMOTE_ID]) is not int or type(question[REMOTE_ID]) is not int \
                        or post[REMOTE_ID] != intent["post_id"] or question[REMOTE_ID] != intent["question_id"]:
                    return unknown
                records = question[FORECASTS]
                if not isinstance(records, list):
                    return unknown
                for row in records:
                    if not isinstance(row, dict):
                        continue
                    try:
                        recent = parse_iso(row[FORECAST_TIME]) > started
                    except (KeyError, TypeError, ValueError):
                        continue
                    expected = payload[0]
                    field = FORECAST_BINARY if "probability_yes" in expected else FORECAST_CATEGORIES
                    value = row.get(field)
                    wanted = expected["probability_yes" if field == FORECAST_BINARY else "probability_yes_per_category"]
                    # Reject bools/NaN and require the exact submitted probabilities.
                    valid = type(value) in (int, float) if field == FORECAST_BINARY else (
                        isinstance(value, dict) and all(type(v) in (int, float) for v in value.values()))
                    if recent and valid and value == wanted:
                        receipt = self._receipt(operation, payload, row.get(REMOTE_ID), intent["bot_id"])
                        if receipt:
                            return Readback("found", receipt, request_id)
            else:
                if sha256_hex(payload["text"]) != intent["comment_hash"]:
                    return unknown
                page = self.client.get(COMMENTS_PATH, {COMMENT_QUERY_POST: intent["post_id"],
                    COMMENT_QUERY_AUTHOR: intent["bot_id"], COMMENT_QUERY_LIMIT: 100})
                visited = set()
                for _ in range(MAX_COMMENT_PAGES):
                    if not isinstance(page, dict) or not isinstance(page.get(PAGE_RESULTS), list):
                        return unknown
                    for row in page[PAGE_RESULTS]:
                        if not isinstance(row, dict):
                            continue
                        author = row.get(COMMENT_AUTHOR)
                        author = author.get(ACCOUNT_ID) if isinstance(author, dict) else author
                        try:
                            matches = type(author) is int and author == intent["bot_id"] \
                                and type(row.get(COMMENT_POST)) is int and row[COMMENT_POST] == intent["post_id"] \
                                and parse_iso(row[COMMENT_TIME]) > started \
                                and isinstance(row.get(COMMENT_TEXT), str) \
                                and sha256_hex(row[COMMENT_TEXT]) == intent["comment_hash"]
                        except (KeyError, TypeError, ValueError):
                            matches = False
                        if matches:
                            receipt = self._receipt(operation, payload, row.get(REMOTE_ID), intent["bot_id"])
                            if receipt:
                                return Readback("found", receipt, request_id)
                    nxt = page.get(PAGE_NEXT)
                    if not isinstance(nxt, str) or not nxt or nxt in visited:
                        break
                    visited.add(nxt)
                    page = self.client._get_url(nxt)
        except Exception:
            pass  # Empty pages, schema drift and every read failure remain unknown.
        return unknown
