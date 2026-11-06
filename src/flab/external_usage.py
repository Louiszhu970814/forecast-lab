"""Shared metadata projection; practice answers never enter production ledgers."""
from flab import advisory_usage as A
from flab.ledger import read_complete
from flab.timeutil import parse_iso

VERSION = "external_usage_v1"


def practice_usage(root):
    admitted, finished = {}, {}
    for row in read_complete(root / "practice_usage.jsonl"):
        aid, player = row.get("attempt_id"), row.get("player")
        if not isinstance(aid, str) or not aid or player not in ("claude", "gemini", "gpt"):
            raise ValueError("invalid practice identity")
        parse_iso(row.get("at"))
        if row.get("type") == "practice_admitted":
            if set(row) != A.ADMITTED_KEYS or aid in admitted or aid in finished \
                    or row.get("version") != "closed_practice_v1" \
                    or type(row.get("question_id")) is not int:
                raise ValueError("invalid practice admission")
            admitted[aid] = row
        elif row.get("type") == "practice_finished":
            prior = admitted.get(aid)
            if set(row) != A.FINISHED_KEYS or prior is None or aid in finished \
                    or row.get("version") != "closed_practice_v1" \
                    or prior["player"] != player or type(row.get("started")) is not bool \
                    or parse_iso(row["at"]) < parse_iso(prior["at"]):
                raise ValueError("invalid practice completion")
            finished[aid] = row
        else:
            raise ValueError("unknown practice event")
    return admitted, finished


def usage(root):
    a, f = A.usage(root)
    pa, pf = practice_usage(root)
    if set(a) & set(pa):
        raise ValueError("external attempt identity collision")
    return {**a, **pa}, {**f, **pf}


def guards(root):
    rows = A.guards(root)
    allowed = {"type", "version", "attempt_id", "player", "question_id", "finished_at_utc",
               "pipeline_version", "model_check", "error_type", "quota_retry_at_utc", "own_audit_flags"}
    seen = {r["attempt_id"] for r in rows}
    for r in read_complete(root / "practice_guards.jsonl"):
        if set(r) != allowed or r.get("type") != "practice_attempt_guard" \
                or r.get("version") != "closed_practice_v1" \
                or r.get("player") not in ("claude", "gemini", "gpt") \
                or not r.get("attempt_id") or r["attempt_id"] in seen:
            raise ValueError("invalid practice safety guard")
        parse_iso(r.get("finished_at_utc"))
        if r.get("error_type") == "quota_exhausted":
            parse_iso(r.get("quota_retry_at_utc"))
        if not isinstance(r.get("own_audit_flags"), list) or any(x != "leak_confirmed" for x in r["own_audit_flags"]):
            raise ValueError("invalid practice exposure flag")
        seen.add(r["attempt_id"])
        rows.append(r)
    return rows
