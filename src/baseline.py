"""Global baseline inventory and change classification (PLAN §7.2, D21, D27, D54).

Only hashes, sizes and key names are recorded; file contents are never stored or printed.
Directories and files whose names look like credentials, sessions or history are never
opened. Anything that cannot be inventoried safely (unreadable, unparseable, a symlinked
source, a link pointing outside its source) stops the affected line instead of passing.

Usage:
    python src/baseline.py snapshot --credits 0
    python src/baseline.py approve --reviewed-by Louis --autoload-record <acceptance record id> --credits 0
    python src/baseline.py check --credits 0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flab.ids import canonical_json, hash_obj, new_id  # noqa: E402
from flab.jsonl import append_record, read_records  # noqa: E402
from flab.timeutil import to_iso, utc_now  # noqa: E402
from flab.workdir import PROJECT_ROOT, default_base_dir  # noqa: E402

ACCEPTANCE_LOG = PROJECT_ROOT / "data" / "acceptance.jsonl"
PLAYERS = ("claude", "gemini", "gpt")
VERSION = "baseline_v3"

# Never open files or descend into directories with these names (credentials, sessions, outputs).
# Items inside an inventoried directory that are never read: folders that hold conversation data and
# files that hold credentials. Matching is on whole names, except "oauth...token", which matches anywhere
# in a file name (e.g. jetski-standalone-oauth-token); a broad substring rule hid ordinary plugin files
# such as hooks-handlers/session-start.sh (GPT review 3). Only their name, size and mtime are
# recorded for credentials, which remain blockers. D121 private directories are
# informational and are not traversed or included in the protected content hash.
PRIVATE_DIR = re.compile(r"^(sessions|history|logs?|transcripts?)$", re.I)
CREDENTIAL_FILE = re.compile(
    r"^(auth|\.?credentials?)(\.json)?$|\.(pem|key|p12|pfx)$|^\.env(\..*)?$|oauth.*token|^cookies?(\.sqlite)?$", re.I)

CONTENT = "content"              # instructions, memories, skills, hooks, plugins, MCP, agents: stop and review
GENERAL = "general"              # ordinary settings: automatic re-acceptance
INFORMATIONAL = "informational"  # recorded only: ignored at runtime, or state the CLI rewrites on every run

# D144: development catalogs change independently of the restricted forecasting
# sessions. Keep inventory and read/type errors, but a readable content change in
# these exact sources is no longer a reason to stop/re-accept a forecasting line.
# This is not proof that their text cannot enter context (notably Codex skill
# metadata). Tool permissions remain enforced by adapters and acceptance evidence.
# Global instructions, memories, standalone hooks/MCP, settings, managed policy
# and ancestor sources deliberately retain their existing classifications.
DEVELOPMENT_CATALOGS = frozenset({
    "claude.skills", "claude.skills_synced", "claude.plugins",
    "claude.marketplace_index", "claude.plugin_state", "claude.agents", "claude.commands",
    "codex.skills", "codex.plugins", "codex.agents",
    "agy.skills", "agy.plugins", "agy.agents",
})

CLAUDE_SETTINGS_CONTENT_KEYS = (
    "hooks", "mcpServers", "enabledPlugins", "extraKnownMarketplaces", "statusLine",
    "apiKeyHelper", "env", "otelHeadersHelper", "awsCredentialExport", "awsAuthRefresh",
)
AGY_SETTINGS_CONTENT_KEYS = ("hooks", "mcpServers", "plugins", "skills", "rules", "agents")
# For "state_dir" sources these are the run-state keys (not content); everything else is content.
CLAUDE_PLUGIN_STATE_KEYS = ("usage-rows-queued",)
# Files claude 2.1.316 rewrites when it refreshes the plugin marketplaces (observed 2026-11-01T11:49Z
# during an M2 forecast run). They are indexes, never loaded into the model; inventoried separately.
CLAUDE_MARKETPLACE_INDEX = "known_marketplaces.json"
CLAUDE_PLUGIN_DIRECTORY_CACHE = "plugin-directory-cache-v2.json"
# Per-marketplace keys that change on every refresh; the rest of each entry is content.
CLAUDE_MARKETPLACE_REFRESH_KEYS = ("lastUpdated",)
# Atomic-write temporaries of the two index files above, left behind when a refresh is interrupted
# (observed 2026-11-01T12:50Z: plugin-directory-cache-v2.json.tmp.7a422f2e). Top level only.
# Matched with fullmatch: "$" alone would also accept a trailing newline (GPT review).
CLAUDE_PLUGIN_INDEX_TEMP = re.compile(r"(known_marketplaces|plugin-directory-cache-v2)\.json\.tmp\.[0-9A-Za-z]{1,32}")


@dataclass(frozen=True)
class Source:
    source_id: str
    player: str
    path: str
    kind: str                      # "file" | "dir" | "json_mixed" | "state_dir" | "marketplace_index"
    category: str = CONTENT
    content_keys: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()  # sub-directories or files inventoried by another source
    exclude_top_files: re.Pattern[str] | None = None  # regular top-level files never loaded by the CLI
    general_category: str = GENERAL  # how a change of the general part is classified


def default_sources(env: dict[str, str] | None = None) -> list[Source]:
    env = dict(os.environ) if env is None else env
    codex_home = env.get("CODEX_HOME") or "~/.codex"
    return [
        Source("claude.global_instructions", "claude", "~/.claude/CLAUDE.md", "file"),
        Source("claude.settings", "claude", "~/.claude/settings.json", "json_mixed",
               content_keys=CLAUDE_SETTINGS_CONTENT_KEYS),
        Source("claude.settings_local", "claude", "~/.claude/settings.local.json", "json_mixed",
               content_keys=CLAUDE_SETTINGS_CONTENT_KEYS),
        # D123 first exempted the synced bucket; D144 extends the catalog policy
        # below to personal skills and the other explicitly listed catalogs:
        # headless runs pass --disable-slash-commands and --tools, the claude 2.1.316 init event
        # declared skills=[] and slash_commands=[] with this bucket present (D120 acceptance), and
        # forecast.tool_violations stops the line if any run ever declares a skill (PLAN D123).
        Source("claude.skills", "claude", "~/.claude/skills", "dir", exclude=("synced",)),
        Source("claude.skills_synced", "claude", "~/.claude/skills/synced", "dir", category=INFORMATIONAL),
        Source("claude.plugins", "claude", "~/.claude/plugins", "claude_plugin_dir",
               exclude=("store", CLAUDE_MARKETPLACE_INDEX, CLAUDE_PLUGIN_DIRECTORY_CACHE),
               exclude_top_files=CLAUDE_PLUGIN_INDEX_TEMP, general_category=INFORMATIONAL),
        # Which marketplaces are known is content; only their refresh time is informational.
        Source("claude.marketplace_index", "claude", f"~/.claude/plugins/{CLAUDE_MARKETPLACE_INDEX}",
               "marketplace_index", general_category=INFORMATIONAL),
        # Listing of installable plugins; nothing in it is enabled or loaded (enabledPlugins is content).
        Source("claude.plugin_directory_cache", "claude", f"~/.claude/plugins/{CLAUDE_PLUGIN_DIRECTORY_CACHE}",
               "file", category=INFORMATIONAL),
        # Builtin plugins' state files. Only keys observed to change on every run (claude 2.1.316)
        # are informational; any other key, file or value change stops the line.
        Source("claude.plugin_state", "claude", "~/.claude/plugins/store", "state_dir",
               content_keys=CLAUDE_PLUGIN_STATE_KEYS, general_category=INFORMATIONAL),
        Source("claude.agents", "claude", "~/.claude/agents", "dir"),
        Source("claude.commands", "claude", "~/.claude/commands", "dir"),
        Source("claude.hooks", "claude", "~/.claude/hooks", "dir"),
        Source("claude.managed_settings", "claude", "/Library/Application Support/ClaudeCode/managed-settings.json", "file"),
        Source("claude.managed_mcp", "claude", "/Library/Application Support/ClaudeCode/managed-mcp.json", "file"),
        # ~/.claude.json is not read (it holds account state). MCP loading is checked per run instead:
        # the stream-json init event must declare no MCP servers (adapter `declared`, PLAN §7.6).
        Source("codex.global_instructions", "gpt", f"{codex_home}/AGENTS.md", "file"),
        Source("codex.skills", "gpt", f"{codex_home}/skills", "dir"),
        Source("codex.memories", "gpt", f"{codex_home}/memories", "dir"),
        Source("codex.plugins", "gpt", f"{codex_home}/plugins", "dir"),
        Source("codex.hooks", "gpt", f"{codex_home}/hooks", "dir"),
        Source("codex.agents", "gpt", f"{codex_home}/agents", "dir"),
        Source("codex.config", "gpt", f"{codex_home}/config.toml", "file", category=INFORMATIONAL),
        Source("codex.managed_config", "gpt", "/etc/codex/managed_config.toml", "file"),
        Source("codex.requirements", "gpt", "/etc/codex/requirements.toml", "file"),
        Source("agy.gemini_md", "gemini", "~/.gemini/GEMINI.md", "file"),
        Source("agy.agents_md", "gemini", "~/.gemini/AGENTS.md", "file"),
        Source("agy.rules", "gemini", "~/.gemini/config/rules", "dir"),
        Source("agy.skills", "gemini", "~/.gemini/config/skills", "dir"),
        Source("agy.plugins", "gemini", "~/.gemini/config/plugins", "dir"),
        Source("agy.agents", "gemini", "~/.gemini/config/agents", "dir"),
        Source("agy.hooks", "gemini", "~/.gemini/config/hooks.json", "file"),
        Source("agy.mcp", "gemini", "~/.gemini/config/mcp_config.json", "file"),
        Source("agy.project_grants", "gemini", "~/.gemini/config/projects", "dir", category=INFORMATIONAL),
        # agy automations ("sidecars") can start agent sessions on a schedule (agy 1.4.12 automation skill).
        Source("agy.sidecars", "gemini", "~/.gemini/config/sidecars", "dir"),
        Source("agy.shared_config", "gemini", "~/.gemini/config/config.json", "json_mixed",
               category=INFORMATIONAL, general_category=INFORMATIONAL),
        Source("agy.cli_settings", "gemini", "~/.gemini/antigravity-cli/settings.json", "json_mixed",
               content_keys=AGY_SETTINGS_CONTENT_KEYS, general_category=INFORMATIONAL),
    ]


# agy also loads workspace customizations from .agents/ (or .agent/, _agents/, _agent/) in the
# working directory and every parent up to the project root (agy 1.4.12 agy-customizations docs).
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
INSTRUCTION_DIRS = (".claude", ".codex", ".gemini", ".agents", ".agent", "_agents", "_agent")
INSTRUCTION_NAMES = INSTRUCTION_FILES + INSTRUCTION_DIRS


def ancestor_sources(base: Path) -> list[Source]:
    """Instruction files in every ancestor of the attempt base directory (PLAN §7.2, 共同)."""
    out: list[Source] = []
    for parent in [base, *base.parents]:
        for name in INSTRUCTION_NAMES:
            p = parent / name
            player = {"CLAUDE.md": "claude", ".claude": "claude",
                      "GEMINI.md": "gemini", ".gemini": "gemini",
                      ".codex": "gpt", ".agents": "gemini", ".agent": "gemini",
                      "_agents": "gemini", "_agent": "gemini"}.get(name, "common")
            out.append(Source(f"ancestor:{p}", player, str(p), "dir" if name in INSTRUCTION_DIRS else "file"))
    return out


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _meta(path: Path) -> list[Any]:
    st = path.lstat()
    return [st.st_size, st.st_mtime_ns]


def _not_read(path: Path, rel: str) -> list[Any]:
    """Name, size and mtime of a private folder's entries or a credential file, without reading content."""
    if not path.is_dir() or path.is_symlink():
        return [rel, *_meta(path)]
    listing: list[Any] = [rel + "/"]
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in sorted(dirnames + filenames):
            p = Path(dirpath) / name
            listing.append([str(p.relative_to(path)), *_meta(p)])
    return listing


def _unsafe_target(target: Path, root_real: Path, exclude: tuple[str, ...]) -> bool:
    if not target.is_relative_to(root_real) or not target.is_file():
        return True
    parts = target.relative_to(root_real).parts
    return any(PRIVATE_DIR.match(x) for x in parts[:-1]) or bool(CREDENTIAL_FILE.search(parts[-1])) \
        or any(x in exclude for x in parts)


def _hash_dir(root: Path, exclude: tuple[str, ...] = (), exclude_top_files: re.Pattern[str] | None = None,
              *, synced_metadata: bool = False) -> dict[str, Any]:
    """Hash of sorted (relative path, kind, digest) entries.

    Private folders and credential files are not read (see PRIVATE_DIR, CREDENTIAL_FILE): their
    name, size and mtime enter the hash and they are listed under ``not_read``. A symlink to a
    regular file inside the same root is hashed by its target content; any other symlink is unsafe.
    """
    entries: list[list[Any]] = []
    raw_entries: list[list[Any]] = []
    sync_state: list[list[Any]] = []
    not_read: list[Any] = []
    private_dirs: list[str] = []
    unsafe: list[str] = []
    errors: list[str] = []
    root_real = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False,
                                                onerror=lambda e: errors.append(f"{type(e).__name__}: {e.filename}")):
        keep = []
        for d in sorted(dirnames):
            p = Path(dirpath) / d
            rel = str(p.relative_to(root))
            if rel in exclude:
                continue
            if p.is_symlink():
                unsafe.append(rel)
            elif PRIVATE_DIR.match(d):
                private_dirs.append(rel)
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            if rel in exclude:
                continue
            if (exclude_top_files is not None and Path(dirpath) == root and exclude_top_files.fullmatch(name)
                    and not p.is_symlink() and p.is_file()):
                continue
            if p.is_symlink():
                if synced_metadata:
                    from claude_sync_state import metadata_parts
                    # Exact metadata paths must be regular files, including internal links.
                    metadata_parts(p, rel)
                target = p.resolve()
                if _unsafe_target(target, root_real, exclude):
                    unsafe.append(rel)
                else:
                    item = [rel, "symlink->file", _hash_file(target)]
                    entries.append(item)
                    raw_entries.append(item)
            elif CREDENTIAL_FILE.search(name):
                not_read.append(_not_read(p, rel))
            elif p.is_file():
                parts = None
                if synced_metadata:
                    from claude_sync_state import metadata_parts
                    parts = metadata_parts(p, rel)
                if parts is None:
                    item = [rel, "file", _hash_file(p)]
                    entries.append(item)
                    raw_entries.append(item)
                else:
                    content, state = parts
                    entries.append([rel, "file", hash_obj(content)])
                    raw_entries.append([rel, "file", state["raw_sha256"]])
                    sync_state.append([rel, state])
            else:
                unsafe.append(rel)
    out = {"hash": hash_obj([entries, not_read]), "entries": len(entries),
           "not_read": [x[0] if isinstance(x[0], str) else x for x in not_read], "unsafe_symlinks": unsafe,
           "informational_private_dirs": private_dirs}
    if errors:
        out["walk_errors"] = errors
    if synced_metadata:
        out.update(content_hash=hash_obj(["claude_plugin_inventory_v2", out["hash"]]),
                   general_hash=hash_obj(sync_state), inventory_version="claude_plugin_inventory_v2",
                   file_hashes=entries, raw_file_hashes=raw_entries)
    return out


def _state_dir_parts(root: Path, state_keys: tuple[str, ...]) -> tuple[str, str, list[str]]:
    """(content hash, state hash, problems) of a directory that may hold only top-level JSON objects.

    Anything else (a subdirectory, symlink, non-JSON file or unreadable entry) is a problem, because
    such a directory is never read beyond the state files the CLI was observed to write.
    """
    content: list[Any] = []
    state: list[Any] = []
    problems: list[str] = []
    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        return hash_obj([]), hash_obj([]), [f"cannot list: {type(exc).__name__}"]
    for name in names:
        p = root / name
        if not p.is_symlink() and p.is_dir() and PRIVATE_DIR.fullmatch(name):
            continue
        if p.is_symlink() or not p.is_file() or not name.endswith(".json") or CREDENTIAL_FILE.search(name):
            problems.append(f"unexpected entry {name!r}")
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            problems.append(f"cannot read {name!r}: {type(exc).__name__}")
            continue
        if not isinstance(data, dict):
            content.append([name, "json", hash_obj(data)])
            continue
        content.append([name, "json", hash_obj({k: v for k, v in data.items() if k not in state_keys})])
        state.append([name, hash_obj({k: v for k, v in data.items() if k in state_keys})])
    return hash_obj(content), hash_obj(state), problems


def _json_parts(path: Path, content_keys: tuple[str, ...]) -> tuple[str | None, str | None, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        return hash_obj(data), None, []
    content = {k: data[k] for k in content_keys if k in data}
    general = {k: v for k, v in data.items() if k not in content_keys}
    return (hash_obj(content) if content else None), (hash_obj(general) if general else None), sorted(data)


def _marketplace_parts(path: Path) -> tuple[str, str | None]:
    """(content hash, refresh hash): marketplace entries without their refresh keys, and those keys."""
    # No empty-file fallback: a truncated or unparsable index is an inventory error (stop), not "{}".
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        return hash_obj(data), None
    content = {name: {k: v for k, v in entry.items() if k not in CLAUDE_MARKETPLACE_REFRESH_KEYS}
               for name, entry in data.items()}
    refresh = {name: {k: entry[k] for k in CLAUDE_MARKETPLACE_REFRESH_KEYS if k in entry}
               for name, entry in data.items()}
    return hash_obj(content), hash_obj(refresh)


def _expand(path: str, home: Path) -> Path:
    return Path(str(home) + path[1:]) if path.startswith("~") else Path(path)


def inspect_source(src: Source, home: Path) -> dict[str, Any]:
    path = _expand(src.path, home)
    rec: dict[str, Any] = {"path": str(path), "player": src.player, "kind": src.kind, "category": src.category,
                           "general_category": src.general_category, "baseline_version": VERSION}
    if not path.exists() and not path.is_symlink():
        rec["exists"] = False
        return rec
    rec["exists"] = True
    if path.is_symlink():
        # A symlinked source can change without its path changing; do not follow it.
        rec["symlink_target"] = os.readlink(path)
        rec["error"] = "source is a symlink; review it and inventory the target explicitly"
        return rec
    try:
        if CREDENTIAL_FILE.search(path.name):
            rec["error"] = "credential file is not read; review by hand"
            return rec
        if path.is_dir() and PRIVATE_DIR.fullmatch(path.name):
            rec.update(category=INFORMATIONAL, hash=hash_obj("private_directory_not_read"))
            return rec
        if src.kind in ("dir", "claude_plugin_dir"):
            if not path.is_dir():
                rec["error"] = "expected directory"
                return rec
            rec.update(_hash_dir(path, src.exclude, src.exclude_top_files,
                                 synced_metadata=src.kind == "claude_plugin_dir"))
            if rec["unsafe_symlinks"]:
                rec["error"] = "symlink points outside the source, into a private folder or credential file, or to a directory"
            elif rec.get("walk_errors"):
                rec["error"] = "directory could not be fully read"
            elif rec["not_read"]:
                rec["error"] = "contains private folders or credential files that are not read; review by hand"
        elif src.kind == "file":
            rec["hash"], rec["size"] = _hash_file(path), path.stat().st_size
        elif src.kind == "json_mixed":
            rec["content_hash"], rec["general_hash"], rec["keys"] = _json_parts(path, src.content_keys)
        elif src.kind == "marketplace_index":
            rec["content_hash"], rec["general_hash"] = _marketplace_parts(path)
        elif src.kind == "state_dir":
            if not path.is_dir():
                rec["error"] = "expected directory"
                return rec
            rec["content_hash"], rec["general_hash"], problems = _state_dir_parts(path, src.content_keys)
            if problems:
                rec["error"] = f"state directory holds more than top-level JSON state files: {problems[:3]}"
    except (OSError, ValueError) as exc:
        rec["error"] = f"cannot inventory: {type(exc).__name__}"
    return rec


def cli_versions(runner: Callable[[list[str]], str | None] | None = None) -> dict[str, str | None]:
    def default_runner(argv: list[str]) -> str | None:
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout if out.returncode == 0 else None

    run = runner or default_runner
    claude, codex, agy = run(["claude", "--version"]), run(["codex", "--version"]), run(["agy", "changelog"])
    return {
        "claude": claude.strip().split()[0] if claude and claude.strip() else None,
        "gpt": codex.strip().split()[-1] if codex and codex.strip() else None,
        "gemini": agy.strip().splitlines()[0].rstrip(":") if agy and agy.strip() else None,
    }


@dataclass
class Snapshot:
    taken_at: str
    sources: dict[str, dict[str, Any]]
    versions: dict[str, str | None]
    gemini_credits: int | float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {"taken_at": self.taken_at, "sources": self.sources, "versions": self.versions,
                "gemini_credits": self.gemini_credits, "extra": self.extra}


def take_snapshot(home: Path | None = None, sources: list[Source] | None = None, attempt_base: Path | None = None,
                  versions: dict[str, str | None] | None = None, gemini_credits: int | float | None = None) -> Snapshot:
    home = home or Path.home()
    srcs = list(sources if sources is not None else default_sources())
    srcs += ancestor_sources((attempt_base or default_base_dir()).resolve())
    return Snapshot(taken_at=to_iso(utc_now()), sources={s.source_id: inspect_source(s, home) for s in srcs},
                    versions=versions if versions is not None else cli_versions(), gemini_credits=gemini_credits)


def _fingerprint(rec: dict[str, Any]) -> tuple[Any, Any]:
    if not rec.get("exists"):
        return ("absent", None)
    if rec["kind"] in ("json_mixed", "state_dir", "marketplace_index", "claude_plugin_dir"):
        return (canonical_json([rec.get("content_hash"), rec.get("error")]), rec.get("general_hash"))
    return (canonical_json([rec.get("hash"), rec.get("symlink_target"), rec.get("error")]), None)


def blockers(snapshot: dict[str, Any], player: str | None = None) -> list[dict[str, Any]]:
    """Conditions that stop a line regardless of the baseline: unknown or unsafe state (D21, D54)."""
    out: list[dict[str, Any]] = []
    for sid, rec in snapshot["sources"].items():
        if rec.get("error"):
            out.append({"source": sid, "player": rec["player"], "category": CONTENT, "what": rec["error"]})
    for line in PLAYERS:
        ver = snapshot["versions"].get(line)
        if not (isinstance(ver, str) and ver.strip()):
            out.append({"source": f"cli_version:{line}", "player": line, "category": CONTENT,
                        "what": "CLI version unknown"})
    credits = snapshot.get("gemini_credits")
    if credits is None:
        out.append({"source": "agy.credits", "player": "gemini", "category": CONTENT,
                    "what": "remaining_credits unknown; must be confirmed as 0 (D54)"})
    elif credits != 0:
        out.append({"source": "agy.credits", "player": "gemini", "category": CONTENT,
                    "what": f"remaining_credits is {credits}, expected 0 (D54)"})
    return [r for r in out if player is None or r["player"] in (player, "common")]


def _same_protected(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    """True when compare() would report no non-informational change for this source."""
    if before is None or after is None:
        return before is after
    (b_content, b_general), (a_content, a_general) = _fingerprint(before), _fingerprint(after)
    content_ok = b_content == a_content or after.get("category") == INFORMATIONAL
    general_ok = b_general == a_general or INFORMATIONAL in (after.get("category"), after.get("general_category"))
    return content_ok and general_ok


def compare(approved: dict[str, Any], current: dict[str, Any], *, catalog_drift: bool = True) -> dict[str, Any]:
    """Classify runtime drift; use catalog_drift=False to bind frozen evidence.

    Readable D144 catalogs and known CLI version drift are informational. Unknown
    state/protected content still stop; other general settings require reacceptance.
    """
    changes: list[dict[str, Any]] = []
    a_src, c_src = approved["sources"], current["sources"]
    for sid in sorted(set(a_src) | set(c_src)):
        before, after = a_src.get(sid), c_src.get(sid)
        if before is None or after is None:
            changes.append({"source": sid, "player": (before or after)["player"], "category": CONTENT,
                            "what": "source added or removed from inventory"})
            continue
        if sid in DEVELOPMENT_CATALOGS and any(before.get(k) != after.get(k) for k in ("player", "kind", "path")):
            changes.append({"source": sid, "player": after["player"], "category": CONTENT,
                            "what": "source definition changed"})
            continue
        b_content, b_general = _fingerprint(before)
        a_content, a_general = _fingerprint(after)
        # Preserve raw inventory categories for exact candidate/evidence binding
        # (_same_protected). D144 changes runtime drift handling, not those proofs.
        category = INFORMATIONAL if catalog_drift and sid in DEVELOPMENT_CATALOGS else after["category"]
        if b_content != a_content:
            changes.append({"source": sid, "player": after["player"],
                            "category": category if category in (INFORMATIONAL, GENERAL) else CONTENT,
                            "what": "content changed"})
        if b_general != a_general:
            general = INFORMATIONAL if INFORMATIONAL in (category, after.get("general_category")) else GENERAL
            changes.append({"source": sid, "player": after["player"], "category": general,
                            "what": "general settings changed"})
    for player in PLAYERS:
        if approved["versions"].get(player) != current["versions"].get(player):
            changes.append({"source": f"cli_version:{player}", "player": player, "category": INFORMATIONAL,
                            "what": f"{approved['versions'].get(player)} -> {current['versions'].get(player)}"})
    changes += blockers(current)

    decisions: dict[str, str] = {}
    for player in PLAYERS:
        relevant = [c for c in changes if c["player"] in (player, "common")]
        if any(c["category"] == CONTENT for c in relevant):
            decisions[player] = "stop"
        elif any(c["category"] == GENERAL for c in relevant):
            decisions[player] = "reaccept"
        else:
            decisions[player] = "ok"
    return {"changes": changes, "decisions": decisions}


def latest_approved(log: Path = ACCEPTANCE_LOG) -> dict[str, Any] | None:
    if not log.exists():
        return None
    approved = [r for r in read_records(log) if r.get("type") == "baseline_approved"]
    return approved[-1] if approved else None


def approve(snapshot: Snapshot, reviewed_by: str, autoload_record: str, log: Path = ACCEPTANCE_LOG,
            *, player: str | None = None) -> dict[str, Any]:
    """Record a baseline only after a human content review and a passing auto-load test (PLAN §7.2)."""
    problems = blockers(snapshot.to_record(), player=player)
    if problems:
        raise ValueError(f"snapshot has blocking conditions: {[p['what'] for p in problems]}")
    body = snapshot.to_record()
    if player is not None:
        old = latest_approved(log)
        if player not in PLAYERS or old is None:
            raise ValueError("scoped approval requires a known player and existing baseline")
        # A scoped approval re-attests only this line's sources and CLI version. Other
        # lines keep their approved records (their own checks still see any change);
        # a protected change to a common source would affect every line, so it is refused.
        # Fingerprints are compared, not whole records: baseline_v2 adds bookkeeping keys
        # and re-attributes ancestor files, neither of which is a content change.
        sources: dict[str, dict[str, Any]] = {}
        for sid in sorted(old["sources"].keys() | snapshot.sources.keys()):
            before, after = old["sources"].get(sid), snapshot.sources.get(sid)
            owner = (after or before)["player"]
            if owner == player:
                if after is not None:
                    sources[sid] = after
                continue
            if owner == "common" and not _same_protected(before, after):
                raise ValueError(f"scoped approval cannot change common source {sid}")
            if before is not None:
                sources[sid] = before
        versions = {p: (snapshot.versions.get(p) if p == player else old["versions"].get(p)) for p in PLAYERS}
        credits = snapshot.gemini_credits if player == "gemini" else old.get("gemini_credits")
        body.update(sources=sources, versions=versions, gemini_credits=credits, scope=player,
                    replaces_baseline=old["record_id"])
    records = list(read_records(log)) if log.exists() else []
    if not any(r.get("record_id") == autoload_record and r.get("type") == "autoload_result" and r.get("passed")
               for r in records):
        raise ValueError(f"no passing autoload_result {autoload_record!r} in {log}")
    rec = {"type": "baseline_approved", "record_id": new_id("base"), "reviewed_by": reviewed_by,
           "autoload_record": autoload_record, "approved_at": to_iso(utc_now()), **body}
    append_record(log, rec)
    return rec


def check(snapshot: Snapshot, log: Path = ACCEPTANCE_LOG) -> dict[str, Any]:
    base = latest_approved(log)
    if base is None:
        result = {"changes": blockers(snapshot.to_record()), "decisions": {p: "stop" for p in PLAYERS},
                  "reason": "no approved baseline"}
    else:
        result = compare(base, snapshot.to_record())
        result["baseline_id"] = base["record_id"]
    rec = {"type": "baseline_check", "version": VERSION, "record_id": new_id("chk"),
           **result, "snapshot": snapshot.to_record()}
    append_record(log, rec)
    return rec


def _summary(snapshot: Snapshot) -> str:
    lines = [f"versions: {snapshot.versions}", f"gemini_credits: {snapshot.gemini_credits}"]
    for sid, rec in snapshot.sources.items():
        if sid.startswith("ancestor:") and not rec.get("exists"):
            continue
        state = "present" if rec.get("exists") else "absent"
        extra = f"  entries={rec['entries']}" if "entries" in rec else ""
        extra += f"  ERROR: {rec['error']}" if rec.get("error") else ""
        lines.append(f"  {sid:30} {rec['category']:13} {state}{extra}")
    for b in blockers(snapshot.to_record()):
        lines.append(f"  BLOCKER [{b['player']}] {b['source']}: {b['what']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("snapshot", "approve", "check"))
    ap.add_argument("--reviewed-by")
    ap.add_argument("--autoload-record")
    ap.add_argument("--credits", type=float, default=None, help="agy remaining_credits from quota.snapshot()")
    args = ap.parse_args(argv)
    snap = take_snapshot(gemini_credits=args.credits)
    if args.command == "snapshot":
        print(_summary(snap))
        return 0
    if args.command == "approve":
        if not (args.reviewed_by and args.autoload_record):
            ap.error("--reviewed-by and --autoload-record are required")
        try:
            rec = approve(snap, args.reviewed_by, args.autoload_record)
        except ValueError as exc:
            print(f"not approved: {exc}")
            return 1
        print(f"approved baseline {rec['record_id']}")
        return 0
    rec = check(snap)
    print(json.dumps({"decisions": rec["decisions"], "changes": rec["changes"]}, ensure_ascii=False, indent=2))
    return 0 if all(v == "ok" for v in rec["decisions"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
