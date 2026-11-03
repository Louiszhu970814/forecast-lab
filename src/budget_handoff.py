"""One-way campaign handoff with the source accounting period retained (D118)."""
from __future__ import annotations

from flab.ids import hash_obj
from flab.ledger import read_complete
from flab.timeutil import parse_iso

VERSION = "budget_handoff_v1"


def superseded(campaign_id, rows):
    return any(isinstance(r.get("campaign", {}).get("budget_handoff"), dict)
               and r["campaign"]["budget_handoff"].get("source_campaign_id") == campaign_id
               for r in rows)


def registered(campaign_id, rows):
    own = [r for r in rows if r["campaign"]["campaign_id"] == campaign_id]
    if not own or any(r["sha256"] != hash_obj(r["campaign"]) or
                      r["sha256"] != own[0]["sha256"] for r in own):
        raise ValueError("handoff requires an intact registered campaign")
    return own[0]["campaign"]


def source(policy, rows):
    """Validate the complete, immutable link without modifying either registration."""
    setting = policy.get("budget_handoff")
    if policy.get("version") != "continuous_v9" or not isinstance(setting, dict) \
            or set(setting) != {"version", "source_campaign_id", "source_sha256",
                                "accounting_start", "authorization"} \
            or setting["version"] != VERSION or setting["authorization"] != "PLAN D118" \
            or any(k in policy for k in ("rollover", "series_id", "series_sha256", "window_index")):
        raise ValueError("explicit single-window budget handoff required")
    old = registered(setting["source_campaign_id"], rows)
    if old["campaign_id"] == policy["campaign_id"] or setting["source_sha256"] != hash_obj(old) \
            or setting["accounting_start"] != old["budget_window_start"] \
            or any(k in old for k in ("budget_handoff", "rollover", "series_id")):
        raise ValueError("handoff source or accounting start differs from registration")
    if not parse_iso(old["budget_window_start"]) < parse_iso(policy["budget_window_start"]) \
            < parse_iso(old["budget_window_end"]):
        raise ValueError("handoff must start inside the source window")
    successors = [r["campaign"] for r in rows if
                  isinstance(r["campaign"].get("budget_handoff"), dict) and
                  r["campaign"]["budget_handoff"].get("source_campaign_id") == old["campaign_id"]]
    if any(r["campaign_id"] != policy["campaign_id"] for r in successors):
        raise ValueError("handoff source already has a successor")
    return old


def accounting_start(root, runtime):
    """Budget admits work only for the registered successor, counting old usage too."""
    rows = read_complete(root / "continuous_campaigns.jsonl")
    if superseded(runtime["campaign_id"], rows):
        raise ValueError("campaign has been superseded")
    registered_handoff = any(r["campaign"]["campaign_id"] == runtime["campaign_id"]
                             and "budget_handoff" in r["campaign"] for r in rows)
    if "budget_handoff" not in runtime and not registered_handoff:
        return parse_iso(runtime["budget_window_start"])
    # The continuous scheduler adds only its mutable question queue to the policy.
    policy = {k: v for k, v in runtime.items() if k != "questions"}
    old = source(policy, rows)
    if hash_obj(registered(policy["campaign_id"], rows)) != hash_obj(policy):
        raise ValueError("handoff runtime differs from registered successor")
    return parse_iso(old["budget_window_start"])
