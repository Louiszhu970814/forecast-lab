"""Immutable bounded handoffs retaining the original accounting period (D118/D137)."""
from __future__ import annotations

from flab.ids import hash_obj
from flab.ledger import read_complete
from flab.timeutil import parse_iso

LEGACY_VERSION = "budget_handoff_v1"
VERSION = "budget_handoff_v2"
MAX_LINKS = 2
EXPANSION_VERSION = "budget_handoff_v3"
CONFIG_HANDOFF_VERSION = "budget_handoff_v4"
MAX_CONFIG_LINKS = 4


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


def _legacy_source(policy, rows):
    """Validate the complete, immutable link without modifying either registration."""
    setting = policy.get("budget_handoff")
    if policy.get("version") != "continuous_v9" or not isinstance(setting, dict) \
            or set(setting) != {"version", "source_campaign_id", "source_sha256",
                                "accounting_start", "authorization"} \
            or setting["version"] != LEGACY_VERSION or setting["authorization"] != "PLAN D118" \
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


def ancestors(policy, rows, *, seen=()):
    """Validate up to two immutable links; v1 rules remain unchanged."""
    if isinstance(policy.get("budget_handoff"), dict) and policy["budget_handoff"].get("version") == CONFIG_HANDOFF_VERSION:
        return config_handoff_ancestors(policy, rows, seen=seen)
    if isinstance(policy.get('budget_handoff'),dict) and policy['budget_handoff'].get('version')==EXPANSION_VERSION:
        if seen:
            raise ValueError('v3 is terminal; fourth handoff is forbidden')
        return expansion_ancestors(policy, rows)
    if len(seen) >= MAX_LINKS:
        raise ValueError("handoff exceeds two links")
    identity = policy.get("campaign_id")
    if not isinstance(identity, str) or not identity or identity in seen:
        raise ValueError("handoff identity cycle")
    setting = policy.get("budget_handoff")
    if not isinstance(setting, dict):
        raise ValueError("explicit budget handoff required")
    if setting.get("version") == LEGACY_VERSION:
        return [_legacy_source(policy, rows)]
    if policy.get("version") != "continuous_v10" or set(setting) != {
            "version", "source_campaign_id", "source_sha256", "accounting_start", "authorization"} \
            or setting.get("version") != VERSION or setting.get("authorization") != "PLAN D137" \
            or any(k in policy for k in ("rollover", "series_id", "series_sha256", "window_index")):
        raise ValueError("explicit bounded v2 handoff required")
    old = registered(setting["source_campaign_id"], rows)
    if old["campaign_id"] in (*seen, identity) or setting["source_sha256"] != hash_obj(old):
        raise ValueError("handoff source identity or hash differs")
    previous = ancestors(old, rows, seen=(*seen, identity)) if "budget_handoff" in old else []
    chain = [old, *previous]
    if len(chain) > MAX_LINKS or len({p["campaign_id"] for p in chain}) != len(chain) \
            or any(p["campaign_id"] in (*seen, identity) for p in chain):
        raise ValueError("handoff exceeds two links or repeats an ancestor")
    if any(k in old for k in ("rollover", "series_id", "series_sha256", "window_index")):
        raise ValueError("handoff source cannot be a rollover series")
    start, end = parse_iso(policy["budget_window_start"]), parse_iso(policy["budget_window_end"])
    if not parse_iso(old["budget_window_start"]) < start < parse_iso(old["budget_window_end"]) \
            or not start < end <= parse_iso(old["budget_window_end"]):
        raise ValueError("v2 handoff cannot extend or leave the source window")
    if setting["accounting_start"] != chain[-1]["budget_window_start"]:
        raise ValueError("handoff must retain the root accounting start")
    for key in ("weekly_attempt_caps", "run_config_sha256", "quota_policy_sha256",
                "expected_new_questions", "repair_reserve", "urgent_slack_s"):
        if policy.get(key) != old.get(key):
            raise ValueError("v2 handoff must preserve source caps and fixed configuration")
    if not isinstance(policy.get("venues"), list) or not policy["venues"] \
            or not set(policy["venues"]) <= {"main", "minibench"}:
        raise ValueError("D137 handoff permits only approved competition venues")
    successors = [r["campaign"] for r in rows if
                  isinstance(r["campaign"].get("budget_handoff"), dict) and
                  r["campaign"]["budget_handoff"].get("source_campaign_id") == old["campaign_id"]]
    if any(r["campaign_id"] != identity for r in successors):
        raise ValueError("handoff source already has a successor")
    return chain


def source(policy, rows):
    return ancestors(policy, rows)[0]


def overlap_ids(policy, rows):
    return {p["campaign_id"] for p in ancestors(policy, rows)}


def accounting_start(root, runtime):
    """Budget admits work only for the registered successor, counting old usage too."""
    rows = read_complete(root / "continuous_campaigns.jsonl")
    # v10 never admits an unregistered policy, even if handoff is stripped.
    # Older standalone budget contracts retain their original compatibility.
    if runtime.get("version") in ("continuous_v10", "continuous_v11"):
        policy = {k: v for k, v in runtime.items() if k != "questions"}
        if hash_obj(registered(runtime["campaign_id"], rows)) != hash_obj(policy):
            raise ValueError("v10 budget runtime differs from registration")
    if superseded(runtime["campaign_id"], rows):
        raise ValueError("campaign has been superseded")
    registered_handoff = any(r["campaign"]["campaign_id"] == runtime["campaign_id"]
                             and "budget_handoff" in r["campaign"] for r in rows)
    if "budget_handoff" not in runtime and not registered_handoff:
        return parse_iso(runtime["budget_window_start"])
    # The continuous scheduler adds only its mutable question queue to the policy.
    policy = {k: v for k, v in runtime.items() if k != "questions"}
    chain = ancestors(policy, rows)
    if hash_obj(registered(policy["campaign_id"], rows)) != hash_obj(policy):
        raise ValueError("handoff runtime differs from registered successor")
    return parse_iso(chain[-1]["budget_window_start"])


def expansion_ancestors(policy, rows):
    """Exactly D105 -> D118 -> D137 -> D139; old v1/v2 limits do not change."""
    import discovery
    import market_policy
    setting = policy['budget_handoff']
    if policy.get('version')!='continuous_v11' or set(setting)!={
            'version','source_campaign_id','source_sha256','accounting_start','authorization'} or \
            setting.get('authorization')!='PLAN D139' or \
            any(k in policy for k in ('rollover','series_id','series_sha256','window_index')):
        raise ValueError('explicit terminal D139 v3 handoff required')
    old=registered(setting['source_campaign_id'],rows)
    if old.get('version')!='continuous_v10' or old.get('budget_handoff',{}).get('version')!=VERSION or \
            old['campaign_id']==policy['campaign_id'] or setting['source_sha256']!=hash_obj(old):
        raise ValueError('D139 requires exact registered D137 source')
    chain=[old,*ancestors(old,rows)]
    if len(chain)!=3 or len({p['campaign_id'] for p in chain})!=3 or \
            policy['campaign_id'] in {p['campaign_id'] for p in chain}:
        raise ValueError('D139 requires exactly three immutable ancestors')
    if setting['accounting_start']!=chain[-1]['budget_window_start']:
        raise ValueError('D139 must preserve root accounting start')
    start,end=parse_iso(policy['budget_window_start']),parse_iso(policy['budget_window_end'])
    if not parse_iso(old['budget_window_start'])<start<parse_iso(old['budget_window_end']) or \
            policy['budget_window_end']!=old['budget_window_end'] or not start<end:
        raise ValueError('D139 must preserve the original finite end')
    for key in ('weekly_attempt_caps','run_config_sha256','quota_policy_sha256',
                'expected_new_questions','repair_reserve','urgent_slack_s'):
        if policy.get(key)!=old.get(key):
            raise ValueError('D139 must preserve source caps and fixed configuration')
    if policy.get('venues')!=['main','minibench','market_pulse'] or old.get('venues')!=['main','minibench']:
        raise ValueError('D139 adds only Market Pulse to the existing live venues')
    discovery.validate(policy);market_policy.validate(policy)
    successors=[r['campaign'] for r in rows if isinstance(r['campaign'].get('budget_handoff'),dict)
                and r['campaign']['budget_handoff'].get('source_campaign_id')==old['campaign_id']]
    if any(r['campaign_id']!=policy['campaign_id'] for r in successors):
        raise ValueError('handoff source already has a successor')
    return chain


def config_handoff_ancestors(policy, rows, *, seen=()):
    """Bounded continuous_v11 run_config upgrade handoff preserving root accounting start (PLAN D151)."""
    import discovery
    import market_policy
    if len(seen) >= MAX_CONFIG_LINKS:
        raise ValueError("config handoff exceeds link bound")
    identity = policy.get("campaign_id")
    if not isinstance(identity, str) or not identity or identity in seen:
        raise ValueError("handoff identity cycle")
    setting = policy.get("budget_handoff")
    if policy.get("version") != "continuous_v11" or not isinstance(setting, dict) or set(setting) != {
            "version", "source_campaign_id", "source_sha256", "accounting_start", "authorization"} \
            or setting.get("version") != CONFIG_HANDOFF_VERSION or setting.get("authorization") != "PLAN D151" \
            or any(k in policy for k in ("rollover", "series_id", "series_sha256", "window_index")):
        raise ValueError("explicit bounded D151 v4 config handoff required")
    old = registered(setting["source_campaign_id"], rows)
    if old.get("version") != "continuous_v11" or old["campaign_id"] in (*seen, identity) \
            or setting["source_sha256"] != hash_obj(old) \
            or any(k in old for k in ("rollover", "series_id", "series_sha256", "window_index")):
        raise ValueError("config handoff source identity, version or hash differs")
    previous = ancestors(old, rows, seen=(*seen, identity)) if "budget_handoff" in old else []
    chain = [old, *previous]
    if len({p["campaign_id"] for p in chain}) != len(chain) \
            or any(p["campaign_id"] in (*seen, identity) for p in chain):
        raise ValueError("config handoff repeats an ancestor")
    if setting["accounting_start"] != chain[-1]["budget_window_start"]:
        raise ValueError("config handoff must preserve root accounting start")
    start, end = parse_iso(policy["budget_window_start"]), parse_iso(policy["budget_window_end"])
    if not parse_iso(old["budget_window_start"]) < start < parse_iso(old["budget_window_end"]) \
            or policy["budget_window_end"] != old["budget_window_end"] or not start < end:
        raise ValueError("config handoff must stay inside and preserve the source window end")
    for key in ("weekly_attempt_caps", "quota_policy_sha256", "expected_new_questions",
                "repair_reserve", "urgent_slack_s", "venues", "discovery", "market_pulse"):
        if policy.get(key) != old.get(key):
            raise ValueError("config handoff must preserve source caps, venues and schedule policy")
    if not isinstance(policy.get("run_config_sha256"), str) or not policy["run_config_sha256"] \
            or policy["run_config_sha256"] == old.get("run_config_sha256"):
        raise ValueError("config handoff requires a changed run_config_sha256")
    discovery.validate(policy)
    market_policy.validate(policy)
    successors = [r["campaign"] for r in rows if isinstance(r.get("campaign", {}).get("budget_handoff"), dict)
                  and r["campaign"]["budget_handoff"].get("source_campaign_id") == old["campaign_id"]]
    if any(r["campaign_id"] != identity for r in successors):
        raise ValueError("handoff source already has a successor")
    return chain
