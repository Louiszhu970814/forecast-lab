"""Authenticated API transport, api_transport_v9. No write retries; V15 checks below.

Only normalized allowlisted receipts leave this boundary. Remote bodies and exception
messages may contain credentials and are never used as diagnostics or ledger evidence.
"""
from __future__ import annotations

import copy
import json
import math
from decimal import Decimal
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from api_backoff import Backoff, Deferred, DEFAULT_PATH

from continuous import current_tournaments
from flab.ids import sha256_hex
from flab.timeutil import parse_iso, utc_now
from metaculus import API_BASE, USER_AGENT, MetaculusClient
from poll_questions import POLL_LOG, TournamentIndex, poll
from question_mapping import select_question
from submit_contract import ConfirmedFailure, Readback, own_forecast_records
import numeric as N

FORECAST_PATH = "/api/questions/forecast/"
COMMENT_PATH = "/api/comments/create/"

VERSION = "api_transport_v9"
# V15: verify every endpoint, field and query on bot testing area before live.
ACCOUNT_PATH = "users/me/"
ACCOUNT_ID, ACCOUNT_IS_BOT = "id", "is_bot"
POST_QUESTION, FORECASTS = "question", "my_forecasts"
REMOTE_ID = "id"
FORECAST_TIME = "start_time"
FORECAST_BINARY = "probability_yes"
FORECAST_CATEGORIES = "probability_yes_per_category"
COMMENTS_PATH = "comments/"
COMMENT_QUERY_POST, COMMENT_QUERY_AUTHOR = "post", "author"
COMMENT_QUERY_LIMIT = "limit"
# D102: the backend lists the requester's private comments only with is_private=true
# (comments/services/feed.py); without it every private comment is filtered out.
COMMENT_QUERY_PRIVATE = "is_private"
COMMENT_PRIVATE = "is_private"  # row field; when present it must equal the frozen payload
COMMENT_TIME, COMMENT_TEXT = "created_at", "text"
COMMENT_AUTHOR, COMMENT_POST = "author", "on_post"
PAGE_RESULTS, PAGE_NEXT = "results", "next"
MAX_COMMENT_PAGES = 20
# M5 OFFLINE ASSUMPTION: MyForecastSerializer stores CDF in forecast_values.
# Change this single boundary after Claude verifies real numeric/discrete GETs.
NUMERIC_READBACK_FIELD = "forecast_values"


def numeric_readback_matches(question, row, expected):
    if question.get("type") not in N.TYPES:
        return False
    try:
        snapshot = {**N.read_scaling_fields(question), "question_type": question["type"]}
        wanted = N.validate_cdf(expected.get("continuous_cdf"), snapshot)
        values = row.get(NUMERIC_READBACK_FIELD)
        return isinstance(values, list) and len(values) == len(wanted) and all(
            N.finite(actual) and 0 <= actual <= 1
            and math.isclose(actual, target, rel_tol=0, abs_tol=N.READBACK_TOLERANCE)
            for actual, target in zip(values, wanted))
    except ValueError:
        return False


def valid_current_options(options):
    return isinstance(options, list) and bool(options) \
        and all(isinstance(option, str) for option in options) \
        and len(set(options)) == len(options)


def known_option_order(question):
    options, current = question.get("all_options_ever"), question.get("options")
    if not isinstance(options, list) or not options or any(not isinstance(v, str) for v in options) \
            or len(set(options)) != len(options) or not isinstance(current, list) or not current \
            or any(not isinstance(v, str) for v in current) or len(set(current)) != len(current) \
            or not set(current) <= set(options):
        return None
    return options


def forecast_identity(row):
    qid, author, stamp = (row.get(k) for k in ("question_id", "author_id", "start_time"))
    if type(qid) is not int or qid <= 0 or type(author) is not int or author <= 0 \
            or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= 0:
        raise ValueError("unrecognized forecast identity")
    datetime.fromtimestamp(stamp, timezone.utc)  # reject out-of-range timestamps
    return f"forecast:{qid}:{author}:{format(Decimal(str(stamp)).normalize(), 'f')}"


def serialized_forecast_receipt(question, row, intent, payload, started):
    """Match the official MyForecastSerializer without inventing a database ID.

    Its identity is (question_id, author_id, start_time), start_time is Unix
    seconds, and forecast_values is ordered by all_options_ever (D115).
    Missing or invalid historical option order leaves MC reconciliation unknown.
    """
    qid, author, stamp = (row.get(k) for k in ("question_id", "author_id", "start_time"))
    if type(qid) is not int or type(author) is not int or qid != intent["question_id"] or author != intent["bot_id"] \
            or type(stamp) not in (int, float) or not math.isfinite(stamp):
        return None
    when = datetime.fromtimestamp(stamp, timezone.utc)
    if not started < when <= utc_now():
        return None
    expected = payload[0]
    if type(expected.get("question")) is not int or expected["question"] != qid:
        return None
    if "continuous_cdf" in expected:
        if set(expected) != {"question", "continuous_cdf"} or not numeric_readback_matches(question, row, expected):
            return None
        return {"id": forecast_identity(row), "id_kind": "question_author_start_time", "bot_id": author,
                "question_id": qid, "start_time_unix": stamp, "payload": copy.deepcopy(payload)}
    values = row.get("forecast_values")
    if not isinstance(values, list):
        return None
    option_order_verified = None
    if "probability_yes" in expected:
        if question.get("type") != "binary":
            return None
        wanted = [1 - expected["probability_yes"], expected["probability_yes"]]
    else:
        options = known_option_order(question)
        current = question.get("options")
        probabilities = expected.get("probability_yes_per_category")
        if question.get("type") != "multiple_choice" or options is None or not valid_current_options(current) \
                or not isinstance(probabilities, dict) or set(current) != set(probabilities) \
                or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
                       for value in probabilities.values()):
            return None
        wanted = [probabilities.get(option) for option in options]
        option_order_verified = True
    if len(values) != len(wanted) or any(
            (actual is not None if target is None else type(actual) not in (int, float) or
             not math.isfinite(actual) or not 0 <= actual <= 1 or actual != target)
            for actual, target in zip(values, wanted)):
        return None
    identity = forecast_identity(row)
    receipt = {"id": identity, "id_kind": "question_author_start_time", "bot_id": author,
        "question_id": qid, "start_time_unix": stamp, "payload": copy.deepcopy(payload)}
    if option_order_verified is not None:
        receipt["option_order_verified"] = option_order_verified
    return receipt


class TransportError(RuntimeError):
    def __init__(self, status=None, summary="API transport unavailable", *, retry_at=None):
        self.status, self.retry_at = status, retry_at
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
            raise TransportError(getattr(exc, "status", None), "API read unavailable",
                retry_at=getattr(exc, "retry_at", None)) from None


class APITransport:
    def __init__(self, token, *, opener=default_opener, timeout=30.0, poll_log=None, backoff=None):
        if not isinstance(token, str) or not token:
            raise ValueError("Metaculus token required")
        self._token, self._open, self._timeout = token, opener, timeout
        self._backoff = backoff or (Backoff(DEFAULT_PATH, pace=True) if opener is default_opener else Backoff())
        self._bot_id = None
        self._write_guard = None
        self._poll_log = POLL_LOG if poll_log is None else poll_log
        # The same host/redirect/error boundary protects authenticated GET pagination.
        self.client = SafeReadClient(token, opener=self._safe_open, timeout=timeout,
            backoff=self._backoff, manage_backoff=False)

    @staticmethod
    def _check_url(url):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.netloc != urllib.parse.urlsplit(API_BASE).netloc \
                or not parts.path.startswith("/api/") or parts.fragment:
            raise TransportError(summary="URL outside HTTPS API refused")

    def set_write_guard(self, callback):
        self._write_guard = callback

    def _guarded_open(self, req, timeout):
        if req.get_method() == "POST" and self._write_guard is not None:
            try:
                self._write_guard()
            except Exception:
                # The underlying opener has not run: this is confirmed nonacceptance.
                raise ConfirmedFailure("local write authorization changed before network") from None
        return self._open(req, timeout)

    def _safe_open(self, req, timeout):
        self._check_url(req.full_url)
        try:
            response = self._backoff.open(self._guarded_open, req, timeout)
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
            retry_at = getattr(exc, "retry_at", None)
            exc.close()
            clean = urllib.error.HTTPError(req.full_url, status, "API request failed", {}, None)
            clean.retry_at = retry_at
            raise clean from None
        except Deferred as exc:
            raise TransportError(exc.status, "API cooldown active", retry_at=exc.retry_at) from None
        except ConfirmedFailure:
            raise
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

    def can_reconcile_forecast(self, post):
        """D115: new MC writes require an exact label-to-position mapping."""
        question = post.get("question") or {}
        if question.get("type") == "binary":
            return True
        if question.get("type") in N.TYPES:
            try:
                N.scaling({**N.read_scaling_fields(question), "question_type": question["type"]})
                # Numeric reconciliation requires the tuple-format history contract.
                return isinstance(question.get("my_forecasts"), dict)
            except ValueError:
                return False
        if question.get("type") != "multiple_choice" or not valid_current_options(question.get("options")):
            return False
        return known_option_order(question) is not None

    def forecast_readback_baseline(self, post):
        question = post.get("question") or {}
        records = own_forecast_records(post)
        if not isinstance(question.get("my_forecasts"), dict):
            return None  # old transport contract: no tuple-format readback
        if any(r.get("question_id") != question.get("id") or r.get("author_id") != self._bot_id for r in records):
            raise TransportError(summary="own forecast identity unavailable")
        return sorted({forecast_identity(row) for row in records})

    def current_tournaments(self, configured):
        return current_tournaments(self.client, configured=configured)

    def tournament_index(self, configured):
        # Match continuous polling and M2 preparation. Unsupported posts are recorded
        # by the shared metadata poller, without exposing competition content.
        return TournamentIndex.from_polls(
            poll(self.client, pid, metadata_only=True, poll_log=self._poll_log)
            for pid in sorted(set(configured["excluded_project_ids"]))
        )

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
                failure.retry_at = getattr(exc, "retry_at", None)
                raise failure from None
            raise TransportError(status, "write outcome unknown", retry_at=getattr(exc, "retry_at", None)) from None
        except (TransportError, ConfirmedFailure):
            raise  # Preserve sanitized cooldown status/deadline; callers still never resend unknown writes.
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
                post = select_question(post, intent['question_id'],
                    allow_groups=intent.get('snapshot',{}).get('post_type')=='group_of_questions')
                if intent.get('snapshot',{}).get('post_type')=='group_of_questions' and \
                        post.get('_group_mapping',{}).get('group_id')!=intent['snapshot'].get('group_id'):
                    return unknown
                question = post[POST_QUESTION]
                if type(post[REMOTE_ID]) is not int or type(question[REMOTE_ID]) is not int \
                        or post[REMOTE_ID] != intent["post_id"] or question[REMOTE_ID] != intent["question_id"]:
                    return unknown
                records = own_forecast_records(post)
                if isinstance(question[FORECASTS], dict):
                    prior = intent.get("request_forecast_baseline")
                    if not isinstance(prior, list) or any(not isinstance(v, str) for v in prior):
                        return unknown
                    matches = {}
                    for row in records:
                        receipt = serialized_forecast_receipt(question, row, intent, payload, started)
                        if receipt and receipt["id"] not in prior:
                            matches[receipt["id"]] = receipt
                    return Readback("found", next(iter(matches.values())), request_id) if len(matches) == 1 else unknown
                for row in records:
                    if "continuous_cdf" in payload[0]:
                        return unknown  # no unverified legacy numeric identity fallback
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
                if type(payload.get("is_private")) is not bool:
                    return unknown
                query = {COMMENT_QUERY_POST: intent["post_id"],
                    COMMENT_QUERY_AUTHOR: intent["bot_id"], COMMENT_QUERY_LIMIT: 100}
                if payload["is_private"]:
                    query[COMMENT_QUERY_PRIVATE] = "true"
                page = self.client.get(COMMENTS_PATH, query)
                visited, matches_found = set(), {}
                for _ in range(MAX_COMMENT_PAGES):
                    if not isinstance(page, dict) or not isinstance(page.get(PAGE_RESULTS), list) or PAGE_NEXT not in page:
                        return unknown
                    for row in page[PAGE_RESULTS]:
                        if not isinstance(row, dict):
                            continue
                        author = row.get(COMMENT_AUTHOR)
                        author = author.get(ACCOUNT_ID) if isinstance(author, dict) else author
                        try:
                            matches = type(author) is int and author == intent["bot_id"] \
                                and type(row.get(COMMENT_POST)) is int and row[COMMENT_POST] == intent["post_id"] \
                                and (COMMENT_PRIVATE not in row or row[COMMENT_PRIVATE] is payload["is_private"]) \
                                and started < parse_iso(row[COMMENT_TIME]) <= utc_now() \
                                and isinstance(row.get(COMMENT_TEXT), str) \
                                and sha256_hex(row[COMMENT_TEXT]) == intent["comment_hash"]
                        except (KeyError, TypeError, ValueError):
                            matches = False
                        if matches:
                            receipt = self._receipt(operation, payload, row.get(REMOTE_ID), intent["bot_id"])
                            if receipt:
                                matches_found[receipt["id"]] = receipt
                    nxt = page.get(PAGE_NEXT)
                    # Countless pagination keeps a next link after the last row; an empty page ends it.
                    if nxt is None or nxt == "" or not page[PAGE_RESULTS]:
                        return Readback("found", next(iter(matches_found.values())), request_id) if len(matches_found) == 1 else unknown
                    if not isinstance(nxt, str) or nxt in visited:
                        return unknown
                    visited.add(nxt)
                    page = self.client._get_url(nxt)
        except Exception:
            pass  # Empty pages, schema drift and every read failure remain unknown.
        return unknown
