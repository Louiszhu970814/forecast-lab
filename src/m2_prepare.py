"""Prepare a local-only, unregistered M2 campaign without exposing forecast values."""
from __future__ import annotations

import argparse
import json
from datetime import timedelta

import forecast as F
from flab.config import load_run_config, load_quota_policy
from flab.envfile import get_secret
from flab.ids import hash_obj, new_id
from flab.ledger import transaction, read_complete
from flab.timeutil import utc_now, to_iso
from flab.workdir import PROJECT_ROOT
from m2_campaign import SafeSnapshots
from continuous import current_tournaments
from flab.config import load_tournaments
from project_policy import trial_membership_error
from metaculus import MetaculusClient
from poll_questions import (poll, TournamentIndex, latest_snapshots, snapshot_from_post,
                            is_synced_copy, record_snapshots)


def prepare(attempt_cap: int):
    root = PROJECT_ROOT / "data"
    client = MetaculusClient(get_secret("METACULUS_TOKEN", PROJECT_ROOT / ".env"))
    cfg = current_tournaments(client, configured=load_tournaments())
    project_ids = set(cfg["excluded_project_ids"])
    index = TournamentIndex.from_polls(poll(client, i, metadata_only=True) for i in sorted(project_ids))
    with transaction(root):
        previous = {r.get("question_id") for r in read_complete(root / "forecasts.jsonl")}
    now, rcfg, chosen = utc_now(), load_run_config(), []
    for ground in ("main_site_questions",):
        project = cfg["tournaments"][ground]["id"]
        # Cap listing at one page; verify all memberships independently of feed filters.
        posts = client.get("posts/", {"main_feed": "true", "statuses": "open", "forecast_type": "binary,multiple_choice",
                                      "limit": 50, "offset": 0, "include_description": "true"})
        candidates = []
        for post in posts.get("results", []):
            if trial_membership_error(post, cfg["tournaments"]["bot_testing_area"]["id"]):
                continue
            snap = snapshot_from_post(post, ground)
            if snap and snap["question_id"] not in previous and project in snap["project_ids"] and not is_synced_copy(snap, index):
                snap["tournament_id"] = project
                candidates.append(snap)
        with transaction(root):
            record_snapshots(candidates)
            saved = latest_snapshots()
        candidates = [saved[s["question_id"]] for s in candidates]
        candidates = sorted([s for s in candidates if not F.eligible(s, rcfg, now) and F.decision_deadline(s, rcfg) is not None],
                            key=lambda s: (F.decision_deadline(s, rcfg), s["question_id"]))
        for snap in candidates:
            if snap["question_id"] not in {s["question_id"] for s in chosen}:
                chosen.append({**snap, "ground": ground})
            if len(chosen) == 20:
                break
        if len(chosen) == 20:
            break
    if len(chosen) < 20:
        raise SystemExit(f"Fewer than 20 eligible, unforecast, nonsynchronized trial questions: {len(chosen)}")
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    campaign = {"version": "m2_campaign_v1", "campaign_id": new_id("m2"),
        "preparation_version": "m2_prepare_v2",
        "questions": [{"question_id": s["question_id"], "post_id": s["post_id"], "ground": s["ground"], "priority": "main"} for s in chosen],
        "budget_window_start": to_iso(start), "budget_window_end": to_iso(start + timedelta(days=7)),
        "weekly_attempt_caps": dict.fromkeys(("claude", "gemini", "gpt"), attempt_cap),
        "expected_new_questions": 0, "repair_reserve": 2, "urgent_slack_s": 120,
        "run_config_sha256": hash_obj(rcfg), "quota_policy_sha256": hash_obj(load_quota_policy())}
    verified = SafeSnapshots(client, root, campaign, tournaments=cfg)()
    if any(trial_membership_error(client.get_post(s["post_id"]), cfg["tournaments"]["bot_testing_area"]["id"])
           for s in verified.values()):
        raise SystemExit("Project membership changed during preparation; no campaign file written")
    if len(verified) != 20:
        raise SystemExit("Selection changed during preparation; no campaign file written")
    path = root / "campaigns" / (campaign["campaign_id"] + ".json")
    path.parent.mkdir(exist_ok=True)
    with path.open("x") as stream:
        json.dump(campaign, stream, indent=2)
    print(json.dumps({"draft_campaign": str(path), "registered": False, "safe_questions": len(verified),
        "excluded_project_ids": sorted(project_ids),
        "earliest_decision_deadline": to_iso(min(F.decision_deadline(s, rcfg) for s in verified.values())),
        "weekly_attempt_caps": campaign["weekly_attempt_caps"], "predictions_run": 0}))
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-cap", type=int, default=64, help="per-line bootstrap cap, including acceptance and all attempts")
    args = parser.parse_args(argv)
    if args.attempt_cap <= 0:
        parser.error("--attempt-cap must be positive")
    prepare(args.attempt_cap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
