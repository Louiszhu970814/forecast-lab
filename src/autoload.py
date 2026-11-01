"""Manual global auto-load check (PLAN §7.6 item 7, D43).

Never called by acceptance or scheduling. Without both confirmations, run only prints
its plan. Backups and an append-only recovery manifest survive failed/interrupted runs.
SIGINT/SIGTERM/SIGHUP restore in finally; SIGKILL or power loss needs manual recovery.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import tempfile
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline  # noqa: E402
from acceptance import codex_home_mirror, codex_probe_argv  # noqa: E402
from adapters.base import AdapterRequest, AdapterResult  # noqa: E402
from flab import runner  # noqa: E402
from flab.config import load_run_config  # noqa: E402
from flab.ids import new_id, sha256_hex  # noqa: E402
from flab.jsonl import append_record  # noqa: E402
from flab.ledger import transaction  # noqa: E402
from flab.timeutil import to_iso, utc_now  # noqa: E402
from flab.workdir import PROJECT_ROOT, default_base_dir, make_attempt_dir, remove_attempt_dir  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"
PROMPT = ('List every string beginning with FLABAUTO- already visible in your context. '
          'Do not read files, use tools, or search for strings. If none are visible, answer NONE. '
          'Return the answer in the text field of the required JSON object.')
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}},
          "required": ["text"], "additionalProperties": False}


@dataclass(frozen=True)
class Location:
    player: str
    path: Path
    kind: str
    canary: str


def locations(tag: str, players: tuple[str, ...], home: Path, attempt_base: Path,
              env: dict[str, str] | None = None) -> list[Location]:
    """Only inert instruction/rule/skill files, selected from the baseline inventory."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", tag):
        raise ValueError("invalid tag")
    if not players or len(set(players)) != len(players) or set(players) - set(baseline.PLAYERS):
        raise ValueError("players must be distinct names from claude,gemini,gpt")
    srcs = {s.source_id: s for s in baseline.default_sources(env)}
    specs = (("claude.global_instructions", "instructions", ""),
             ("claude.skills", "skill", f"flab-autoload-{tag}/SKILL.md"),
             ("codex.global_instructions", "instructions", ""),
             ("codex.skills", "skill", f"flab-autoload-{tag}/SKILL.md"),
             ("agy.gemini_md", "instructions", ""), ("agy.agents_md", "instructions", ""),
             ("agy.rules", "rule", f"flab-autoload-{tag}.md"),
             ("agy.skills", "skill", f"flab-autoload-{tag}/SKILL.md"))
    paths: list[tuple[str, Path, str]] = []
    for sid, kind, suffix in specs:
        src = srcs[sid]
        if src.player in players:
            path = baseline._expand(src.path, home)
            if not path.is_absolute():
                raise ValueError("global source must be an absolute path")
            paths.append((src.player, path / suffix if suffix else path, kind))
    parent = attempt_base.parent
    for src in baseline.ancestor_sources(attempt_base):
        path = Path(src.path)
        if path.parent == parent and path.name in baseline.INSTRUCTION_FILES:
            paths.append(("common", path, "instructions"))
        elif path == parent / ".agents":
            paths.append(("common", path / "rules" / f"flab-autoload-{tag}.md", "rule"))
    if len({p for _, p, _ in paths}) != len(paths):
        raise ValueError("overlapping global and ancestor targets")
    return [Location(player, path, kind, f"FLABAUTO-{tag}-{i:02d}")
            for i, (player, path, kind) in enumerate(paths, 1)]


def _state(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _check_parents(path: Path) -> None:
    for parent in path.parents:
        st = _state(parent)
        if st is not None and not stat.S_ISDIR(st.st_mode):
            raise ValueError(f"unsafe parent (symlink or non-directory): {parent}")


def inspect(path: Path) -> dict[str, Any]:
    _check_parents(path)
    st = _state(path)
    if st is None:
        return {"state": "absent"}
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise ValueError(f"target is not a single-link regular file: {path}")
    return {"state": "file", "sha256": sha256_hex(_read(path)), "mode": stat.S_IMODE(st.st_mode),
            "atime_ns": st.st_atime_ns, "mtime_ns": st.st_mtime_ns}


def _read(path: Path) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        return stream.read()


def _write(path: Path, content: bytes, *, create: bool = False, append: bool = False) -> None:
    flags = os.O_WRONLY | os.O_NOFOLLOW
    flags |= os.O_CREAT | os.O_EXCL if create else (os.O_APPEND if append else os.O_TRUNC)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def plan(targets: list[Location]) -> list[dict[str, Any]]:
    """Metadata only: do not read contents, create directories, or invoke a CLI."""
    out = []
    for loc in targets:
        try:
            _check_parents(loc.path)
            st = _state(loc.path)
            safe = st is None or (stat.S_ISREG(st.st_mode) and st.st_nlink == 1)
            action = "append and back up" if st is not None else "create then remove"
        except (OSError, ValueError):
            safe, action = False, "refuse unsafe parent"
        out.append({"player": loc.player, "path": str(loc.path), "kind": loc.kind,
                    "action": action if safe else "REFUSE: unsafe target or parent"})
    return out


class Placement:
    def __init__(self, targets: list[Location], backup_dir: Path) -> None:
        self.targets = targets
        self.backup_dir = backup_dir
        self.states: dict[Path, dict[str, Any]] = {}
        self.touched: list[Path] = []
        self.created_dirs: list[Path] = []
        self.directory_modes: dict[Path, int] = {}

    def prepare(self) -> None:
        # Preflight every target before even creating the backup directory.
        self.states = {loc.path: inspect(loc.path) for loc in self.targets}
        self.backup_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        for i, loc in enumerate(self.targets):
            state = self.states[loc.path]
            if state["state"] == "file":
                content = _read(loc.path)
                if sha256_hex(content) != state["sha256"]:
                    raise ValueError(f"target changed during backup: {loc.path}")
                backup = self.backup_dir / f"{i:02d}.bin"
                _write(backup, content, create=True)
                state["backup"] = str(backup)
            append_record(self.backup_dir / "manifest.jsonl", {"type": "original", "path": str(loc.path), **state})

    def place(self) -> None:
        for loc in self.targets:
            state = self.states[loc.path]
            current = inspect(loc.path)
            if (current["state"], current.get("sha256")) != (state["state"], state.get("sha256")):
                raise ValueError(f"target changed before placement: {loc.path}")
            missing = [p for p in loc.path.parents if _state(p) is None]
            for directory in reversed(missing):
                append_record(self.backup_dir / "manifest.jsonl", {"type": "create_dir", "path": str(directory)})
                self.created_dirs.append(directory)
                directory.mkdir(mode=0o700)
            if loc.kind == "skill" and state["state"] == "absent":
                content = (f"---\nname: {loc.path.parent.name}\ndescription: Auto-load test marker {loc.canary}\n---\n"
                           f"\nAuto-load test marker: {loc.canary}\n")
            else:
                content = f"\n\n<!-- Forecast Lab auto-load test -->\nAuto-load test marker: {loc.canary}\n"
            append_record(self.backup_dir / "manifest.jsonl", {"type": "place", "path": str(loc.path)})
            self.touched.append(loc.path)
            _write(loc.path, content.encode(), create=state["state"] == "absent", append=state["state"] == "file")

    def restore(self) -> list[dict[str, Any]]:
        failures = []
        for path in reversed(self.touched):
            state = self.states[path]
            try:
                inspect(path)  # Never follow a replacement symlink or truncate a special file.
                if state["state"] == "file":
                    content = _read(Path(state["backup"]))
                    if sha256_hex(content) != state["sha256"]:
                        raise ValueError("backup hash mismatch")
                    _write(path, content, create=_state(path) is None)
                    path.chmod(state["mode"])
                    if sha256_hex(_read(path)) != state["sha256"]:
                        raise ValueError("restored hash mismatch")
                    os.utime(path, ns=(state["atime_ns"], state["mtime_ns"]))
                else:
                    path.unlink(missing_ok=True)
                    if _state(path) is not None:
                        raise ValueError("new file still exists")
            except (OSError, ValueError) as exc:
                failures.append({"path": str(path), "backup": state.get("backup"),
                                 "original_state": state["state"], "error": type(exc).__name__})
        for directory in reversed(self.created_dirs):
            try:
                _check_parents(directory)
                if _state(directory) is not None:
                    directory.rmdir()
                if _state(directory) is not None:
                    raise ValueError("new directory still exists")
            except (OSError, ValueError) as exc:
                failures.append({"path": str(directory), "backup": None, "original_state": "absent directory",
                                 "error": type(exc).__name__})
        for directory, mode in self.directory_modes.items():
            try:
                _check_parents(directory)
                st = directory.lstat()
                if not stat.S_ISDIR(st.st_mode):
                    raise ValueError("attempt base replaced")
                directory.chmod(mode)
                if stat.S_IMODE(directory.stat().st_mode) != mode:
                    raise ValueError("attempt base mode mismatch")
            except (OSError, ValueError) as exc:
                failures.append({"path": str(directory), "backup": None, "original_state": "directory",
                                 "mode": mode, "error": type(exc).__name__})
        return failures


@dataclass
class Declaration:
    text: str = ""
    skills: list[str] | None = None
    source: str = "no usable CLI source declaration"


def claude_declaration(raw_dir: Path) -> Declaration:
    path = raw_dir / "stdout.jsonl"
    if not path.exists():
        return Declaration()
    try:
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        init = [e for e in events if isinstance(e, dict) and e.get("type") == "system" and e.get("subtype") == "init"]
        if len(init) != 1:
            return Declaration()
        data = {k: init[0][k] for k in ("skills", "plugins", "mcp_servers", "slash_commands", "agents") if k in init[0]}
        skills = data.get("skills")
        # Unknown entry shapes cannot prove absence, even if another entry is recognizable.
        names = skills if isinstance(skills, list) and all(isinstance(s, str) for s in skills) else None
        return Declaration(json.dumps(data), names, "Claude stream-json system/init")
    except (OSError, ValueError):
        return Declaration()


def codex_declaration(req: AdapterRequest, adapter: Any) -> Declaration:
    """Use the run's restricted catalog and overrides; never edit the real config.

    The debug CLI lacks --ignore-user-config/--strict-config, so use acceptance's
    config-free mirror. Positive evidence is useful; absence in this different HOME
    is not proof of absence in exec (D43). Do not mark such sources not_loaded.
    """
    probe = req.raw_dir / "codex_probe"
    probe.mkdir(mode=0o700)
    mirror = Path(tempfile.mkdtemp(prefix="flab-codex-home-")) / "home"
    real = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        if mirror.resolve().is_relative_to(PROJECT_ROOT.resolve()):
            raise ValueError("Codex mirror must be outside project root")
        codex_home_mirror(mirror, real)
        argv = codex_probe_argv(adapter.build_argv(req))
        argv[-1] = PROMPT
        result = runner.run(argv, cwd=req.workdir, timeout_s=60, stdout_path=probe / "prompt_input.json",
                            stderr_path=probe / "stderr.txt", extra_env={"CODEX_HOME": str(mirror)})
        if result.exit_code != 0 or result.timed_out or result.killed_signal:
            return Declaration(source="Codex prompt-input failed")
        data = json.loads((probe / "prompt_input.json").read_text())
        if not isinstance(data, list):
            return Declaration(source="unrecognized Codex prompt-input format")
        messages = [e for e in data if isinstance(e, dict) and e.get("type") == "message"]
        text = json.dumps(messages).replace(str(mirror), str(real))
        return Declaration(text, source="Codex prompt-input; config-free mirror, absence is inconclusive")
    finally:
        shutil.rmtree(mirror.parent)


def classify(loc: Location, output: str, declaration: Declaration) -> tuple[str, str]:
    if loc.canary in output:
        return "loaded", "canary found in model/raw output"
    if loc.canary in declaration.text or str(loc.path) in declaration.text:
        return "loaded", f"source found in {declaration.source}"
    if loc.kind == "skill" and declaration.skills is not None:
        if loc.path.parent.name in declaration.skills:
            return "loaded", f"skill listed in {declaration.source}"
        return "not_loaded", f"skill absent from complete skills list in {declaration.source}"
    return "unknown", f"no canary and no exclusion evidence: {declaration.source}"


def _append(log: Path, record: dict[str, Any]) -> None:
    with transaction(log.parent):
        append_record(log, record)


# Louis lowered the autoload-only Argon gate from 45% to 20% (PLAN D70). Manual
# acceptance and the forecast pipeline keep their own reserves.
def _start_call(req: AdapterRequest, run_id: str, log: Path) -> None:
    """Gate and journal before entering the executor, including failed/interrupted calls."""
    call_id = new_id("acccall")
    if req.player == "gemini":
        # Same reserve and credits gate as manual acceptance, before any model call.
        import quota
        before = quota.snapshot(timeout_s=30)
        from flab.config import load_quota_policy
        reason, admission = quota.manual_admission(before, load_quota_policy(), model=req.model,
                                                   history_root=log.parent)
        snapshot_record = quota.record_snapshot(before)
        if reason:
            _append(log, {"type": "acceptance_paused", "run_id": run_id, "player": req.player,
                          "tier": 1, "profile": req.profile, "source": "autoload",
                          "at": to_iso(utc_now()), "reason": reason, "quota_snapshot": snapshot_record, **admission})
            raise ValueError(reason)
        _append(log, {"type": "acceptance_quota_snapshot", "attempt_id": call_id,
                      "when": "before", "source": "autoload", **snapshot_record})
    _append(log, {"type": "acceptance_call_started", "attempt_id": call_id,
                  "run_id": run_id, "player": req.player, "tier": 1, "profile": req.profile,
                  "model": req.model, "effort": req.effort, "started_at": to_iso(utc_now()),
                  "source": "autoload"})


def execute(req: AdapterRequest) -> tuple[AdapterResult, Declaration]:
    adapter = importlib.import_module(f"adapters.{req.player}")
    result = adapter.run(req)
    if req.player == "claude":
        declaration = claude_declaration(req.raw_dir)
    elif req.player == "gpt":
        try:
            declaration = codex_declaration(req, adapter)
        except (OSError, ValueError):
            declaration = Declaration(source="Codex prompt-input unavailable or invalid")
    else:
        declaration = Declaration(source="agy has no usable auto-load source declaration")
    return result, declaration


def raw_output(raw_dir: Path) -> str:
    # Never scan backups, schemas, catalogs or our own manifests as model output.
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for name in ("stdout.jsonl", "events.jsonl", "stderr.txt", "last.json", "agy_cli.log")
                     if (p := raw_dir / name).is_file())


@contextmanager
def interruptions():
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}

    def stop(signum, frame):
        # Ignore subsequent signals while the runner stops children and finally restores files.
        for s in previous:
            signal.signal(s, signal.SIG_IGN)
        raise InterruptedError(f"signal {signum}")

    try:
        for s in previous:
            signal.signal(s, stop)
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def run(*, approved_by: str, confirmed: bool, players: tuple[str, ...] = baseline.PLAYERS,
        cheap: bool = False, home: Path | None = None, attempt_base: Path | None = None,
        data_dir: Path = DATA_DIR,
        executor: Callable[[AdapterRequest], tuple[AdapterResult, Declaration]] = execute) -> dict[str, Any]:
    if not approved_by.strip() or not confirmed:
        raise ValueError("both --approved-by and --i-understand-this-edits-global-files are required")
    home = (home or Path.home()).resolve()
    attempt_base = (attempt_base or default_base_dir()).resolve()
    run_id = new_id("autoload")
    targets = locations(run_id, players, home, attempt_base)
    config = load_run_config()
    models = {p: {k: v for k, v in (config["acceptance"][p] if cheap else config["players"][p]["tier1"]).items()
                  if k in ("model", "effort")} for p in players}
    raw_dir = data_dir / "autoload_runs" / run_id
    placement = Placement(targets, data_dir / "autoload_backups" / run_id)
    rec: dict[str, Any] = {"type": "autoload_result", "record_id": new_id("auto"), "run_id": run_id,
        "mode": "cheap_dry_run" if cheap else "production", "approved_by": approved_by.strip(),
        "models": models, "cli_versions": {}, "locations": [], "must_review": [], "restore_ok": False,
        "passed": False, "started_at": to_iso(utc_now()), "raw_dir": str(raw_dir),
        "attempt_base": str(attempt_base),
        "backup_dir": str(placement.backup_dir), "errors": [], "completed_players": []}
    evidence: dict[str, tuple[str, Declaration]] = {}
    with interruptions():
        try:
            _check_parents(attempt_base)
            base_state = _state(attempt_base)
            if base_state is not None and not stat.S_ISDIR(base_state.st_mode):
                raise ValueError("attempt base is not a directory")
            placement.prepare()
            raw_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
            placement.place()
            if _state(attempt_base) is None:
                append_record(placement.backup_dir / "manifest.jsonl", {"type": "create_dir", "path": str(attempt_base)})
                placement.created_dirs.append(attempt_base)
                attempt_base.mkdir(mode=0o700)
            else:
                mode = stat.S_IMODE(attempt_base.stat().st_mode)
                placement.directory_modes[attempt_base] = mode
                append_record(placement.backup_dir / "manifest.jsonl",
                              {"type": "directory_mode", "path": str(attempt_base), "mode": mode})
            for player in players:
                workdir = make_attempt_dir(attempt_base)
                try:
                    req = AdapterRequest(player, "tier1", PROMPT, SCHEMA, workdir, raw_dir / player,
                                         models[player]["model"], models[player]["effort"], config["timeouts_s"]["tier1"])
                    _start_call(req, run_id, data_dir / "acceptance.jsonl")
                    result, declaration = executor(req)
                    rec["cli_versions"][player] = result.cli_version
                    evidence[player] = (result.final_text or "", declaration)
                    if result.completed and result.error_type is None and result.cli_version and (
                            result.run is None or (result.run.exit_code == 0 and not result.run.timed_out
                                                   and not result.run.killed_signal)):
                        rec["completed_players"].append(player)
                    else:
                        rec["errors"].append({"player": player, "error": result.error_type or "incomplete run/version"})
                finally:
                    remove_attempt_dir(workdir, attempt_base)
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            # Exception messages can contain user file contents; persist the type only.
            rec["errors"].append({"error": type(exc).__name__})
        finally:
            # Protect recovery itself even when an exception was not a signal.
            for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(s, signal.SIG_IGN)
            rec["restore_failures"] = placement.restore()
            rec["restore_ok"] = not rec["restore_failures"]
            if not rec["restore_ok"]:
                print_recovery(rec)
        for player in players:
            declaration = claude_declaration(raw_dir / player) if player == "claude" else Declaration()
            output, declaration = evidence.get(player, ("", declaration))
            try:
                output += "\n" + raw_output(raw_dir / player)
            except OSError:
                rec["errors"].append({"player": player, "error": "unreadable raw output"})
            for loc in targets:
                if loc.player not in (player, "common"):
                    continue
                outcome, reason = classify(loc, output, declaration)
                entry = {"player": player, "path": str(loc.path), "kind": loc.kind,
                         "canary_sha256": sha256_hex(loc.canary), "outcome": outcome, "evidence": reason}
                rec["locations"].append(entry)
                if outcome in ("loaded", "unknown"):
                    rec["must_review"].append({"player": player, "path": str(loc.path), "outcome": outcome})
        # A subset must never authorize a global baseline for untested lines.
        rec["passed"] = (not cheap and set(players) == set(baseline.PLAYERS)
                         and set(rec["completed_players"]) == set(players) and rec["restore_ok"]
                         and not rec["errors"])
        rec["finished_at"] = to_iso(utc_now())
        _append(data_dir / "acceptance.jsonl", rec)
    return rec


def print_recovery(rec: dict[str, Any]) -> None:
    # Print before writing the result: disk/log failure must not hide recovery instructions.
    print(f"MANUAL RESTORE REQUIRED. Recovery manifest: {rec['backup_dir']}/manifest.jsonl", file=sys.stderr)
    for failure in rec["restore_failures"]:
        if failure["backup"]:
            action = f"restore bytes and metadata from {failure['backup']} and verify the manifest sha256"
        elif "mode" in failure:
            action = f"restore directory permissions to {failure['mode']:04o}"
        else:
            action = "remove the test-created path after inspecting it"
        print(f"  {failure['path']}: {action}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("plan", "run"))
    ap.add_argument("--approved-by", default="")
    ap.add_argument("--i-understand-this-edits-global-files", action="store_true")
    ap.add_argument("--players", default=",".join(baseline.PLAYERS))
    ap.add_argument("--cheap", action="store_true")
    args = ap.parse_args(argv)
    players = tuple(p.strip() for p in args.players.split(","))
    try:
        targets = locations(new_id("plan"), players, Path.home().resolve(), default_base_dir().resolve())
        if args.command == "plan" or not (args.approved_by.strip() and args.i_understand_this_edits_global_files):
            print(json.dumps(plan(targets), ensure_ascii=False, indent=2))
            if args.command == "run":
                print("Refused: --approved-by and --i-understand-this-edits-global-files are both required; plan only.", file=sys.stderr)
                return 2
            return 0
        rec = run(approved_by=args.approved_by, confirmed=args.i_understand_this_edits_global_files,
                  players=players, cheap=args.cheap)
    except (OSError, ValueError) as exc:
        print(f"autoload refused: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0 if rec["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
