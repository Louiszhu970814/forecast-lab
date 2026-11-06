"""Metadata-only shared accounting for separately stored human advisory attempts."""
from flab.ledger import read_complete
from flab.timeutil import parse_iso

VERSION = "advisory_usage_v1"
ADMITTED_KEYS = frozenset({"type", "version", "at", "attempt_id", "player", "question_id", "batch",
                          "batch_path", "campaign_id", "protected_attempts", "decision_id"})
V2_ADMITTED_KEYS = ADMITTED_KEYS | {"request_id", "content_hash", "update_reason"}
FINISHED_KEYS = frozenset({"type", "version", "at", "attempt_id", "player", "started"})



def usage(root):
    admitted, finished = {}, {}
    for row in read_complete(root / "advisory_usage.jsonl"):
        aid, player = row.get("attempt_id"), row.get("player")
        if not isinstance(aid, str) or not aid or player not in ("claude", "gemini", "gpt"):
            raise ValueError("invalid advisory usage identity")
        parse_iso(row.get("at"))
        if row.get("type") == "advisory_admitted":
            keys = V2_ADMITTED_KEYS if row.get('version') == 'advisory_v2' else ADMITTED_KEYS
            if row.get('version') == 'advisory_v2' and (set(row) != keys or
                    not isinstance(row.get('request_id'),str) or not row['request_id'] or
                    not isinstance(row.get('content_hash'),str) or not row['content_hash'] or
                    (row.get('update_reason') is not None and not isinstance(row['update_reason'],str))):
                raise ValueError('invalid v2 advisory admission')
            if set(row) - keys or aid in admitted or aid in finished:
                raise ValueError("duplicate advisory admission")
            admitted[aid] = row
        elif row.get("type") == "advisory_finished":
            prior = admitted.get(aid)
            if set(row) - FINISHED_KEYS or prior is None or aid in finished or prior["player"] != player \
                    or type(row.get("started")) is not bool \
                    or parse_iso(row["at"]) < parse_iso(prior["at"]):
                raise ValueError("invalid advisory completion")
            finished[aid] = row
        else:
            raise ValueError("unknown advisory usage event")
    return admitted, finished


def guards(root):
    allowed = {"type", "version", "attempt_id", "player", "question_id", "finished_at_utc",
               "pipeline_version", "model_check", "error_type", "quota_retry_at_utc"}
    rows = read_complete(root / "advisory_guards.jsonl")
    seen = set()
    for r in rows:
        if set(r) - allowed or r.get("type") != "advisory_attempt_guard" or \
                r.get("player") not in ("claude", "gemini", "gpt") or not r.get("attempt_id") or \
                r["attempt_id"] in seen:
            raise ValueError("invalid advisory guard metadata")
        parse_iso(r.get("finished_at_utc"))
        if r.get("error_type") == "quota_exhausted":
            parse_iso(r.get("quota_retry_at_utc"))
        seen.add(r["attempt_id"])
    return rows
