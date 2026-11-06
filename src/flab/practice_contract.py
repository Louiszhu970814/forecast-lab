"""Narrow opt-in execution clock for registered closed practice, never a new close date."""
from datetime import timedelta
from pathlib import Path

from flab.ids import hash_obj
from flab.ledger import read_complete
from flab.timeutil import parse_iso


def execution_cutoff(ctx, snap, kind, prev):
    c = ctx.closed_practice
    if c is None:
        return None
    if ctx.tier != 1 or kind != "initial" or prev is not None or not ctx.before_attempt \
            or not ctx.recheck_inputs or not ctx.admission_outcome:
        raise ValueError("practice requires gated, budgeted initial tier 1")
    shared = ctx.line_status_log.parent.resolve()
    batch = Path(c["batch_path"]).resolve()
    if batch.parent != shared / "practice" or batch.name != c["batch"]:
        raise ValueError("practice path outside independent storage")
    rows = read_complete(batch / "manifest.jsonl")
    registered = read_complete(shared / "practice_batches.jsonl")
    if len(rows) != 1 or len(registered) != 1 or not any(r.get("manifest_sha256") == hash_obj(rows[0])
            and r.get("batch") == batch.name for r in registered):
        raise ValueError("practice manifest is not registered")
    manifest = rows[0]
    entry = next((r for r in manifest["selected"] if r["question_id"] == snap["question_id"]), None)
    if entry is None or snap != entry["snapshot"] or snap.get("status") not in ("closed", "resolved"):
        raise ValueError("practice snapshot does not match registration")
    expected = batch / str(snap["question_id"]) / ctx.player
    if ctx.forecasts_log.resolve() != expected / "forecasts.jsonl" \
            or ctx.audits_log.resolve() != expected / "audits.jsonl" \
            or ctx.raw_root.resolve() != expected / "raw":
        raise ValueError("practice cannot write formal answers or audits")
    start, cutoff = parse_iso(c["started_at"]), parse_iso(c["cutoff"])
    if not start <= ctx.now() < cutoff <= start + timedelta(minutes=12) \
            or cutoff > parse_iso(manifest["expires_at"]):
        raise ValueError("invalid bounded practice clock")
    return cutoff
