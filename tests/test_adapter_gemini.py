from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from adapters import gemini
from adapters.base import AdapterRequest
from flab import netguard
from flab.runner import RunResult

CONV = "11111111-2222-3333-4444-555555555555"
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}


def _make_req(tmp_path: Path, *, profile: str = "tier1", model: str = "gemini-3.8-flash-low",
              effort: str = "low", timeout_s: int = 600) -> AdapterRequest:
    return AdapterRequest(player="gemini", profile=profile, prompt="Test prompt", schema=SCHEMA,
                          workdir=tmp_path / "work", raw_dir=tmp_path / "raw", model=model, effort=effort,
                          timeout_s=timeout_s)


@pytest.fixture(autouse=True)
def _brain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    brain = tmp_path / "brain"
    (brain / CONV / ".system_generated" / "steps" / "2").mkdir(parents=True)
    monkeypatch.setattr(gemini, "brain_dir", lambda: brain.resolve())
    return brain


def _page(brain: Path) -> str:
    return str(brain / CONV / ".system_generated" / "steps" / "2" / "content.md")


def _tool(idx: int, name: str, params: dict, state: str = "DONE", error: str | None = None) -> list[dict]:
    info: dict = {"name": name, "parameters": params}
    active = {"step_update": {"conversation_id": CONV, "step_index": idx, "state": "ACTIVE", "step_type": "tool",
                              "tool_name": name, "tool_info": dict(info)}, "event": "step_update"}
    if error is not None:
        info["error"] = {"type": "TOOL_ERROR", "message": error}
    final = {"step_update": {"conversation_id": CONV, "step_index": idx, "state": state, "step_type": "tool",
                             "tool_name": name, "tool_info": info}, "event": "step_update"}
    return [active] if state == "ACTIVE" else [active, final]


def _write_stream(req: AdapterRequest, steps: list[list[dict]], result: dict | None, *, log: str = "") -> None:
    req.raw_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict] = [{"event": "init", "conversation_id": CONV,
                           "init": {"model": req.model, "cwd": str(req.workdir), "agent": "flab-forecast",
                                    "tools": ["run_command", "search_web"], "permission_mode": "request-review"}}]
    for s in steps:
        events += s
    if result is not None:
        events.append({"event": "result", "result": {"conversation_id": CONV, **result}})
    (req.raw_dir / gemini.STDOUT_FILENAME).write_text("".join(json.dumps(e) + "\n" for e in events))
    (req.raw_dir / "agy_cli.log").write_text(log)


OK_RESULT = {"status": "SUCCESS", "response": '{"answer":"x"}', "num_turns": 1,
             "structured_output": {"answer": "x"},
             "usage": {"input_tokens": 100, "output_tokens": 20, "thinking_tokens": 5, "cache_read_tokens": 7}}
DENY = "permission check failed for read_file \"{p}\": Permission denied for read_file({p}). Matches user-configured deny rule."
SOFT = ("permission check failed for read_file \"{p}\": user denied permission for read_file({p})\n"
        "Do not attempt to circumvent this denial. Proceed without performing this action.")


# ---------------------------------------------------------------- argv, agents and settings

def test_build_argv_selects_minimal_agent_stream_json_and_loopback_guard(tmp_path: Path) -> None:
    req = _make_req(tmp_path)
    argv = gemini.build_argv(req)
    assert netguard.is_wrapped(argv)
    inner = argv[3:]
    assert inner[:3] == ["agy", "-p", "Test prompt"]
    assert inner[inner.index("--agent") + 1] == gemini.TIER1_AGENT
    assert inner[inner.index("--output-format") + 1] == "stream-json"
    assert inner[inner.index("--json-schema") + 1] == str(req.raw_dir / "schema.json")
    assert "--disable-slash-commands" in inner and "--sandbox" in inner
    settings = json.loads(inner[inner.index("--settings") + 1])
    assert settings["permissions"]["allow"] == ["read_url(*)"]
    deny = settings["permissions"]["deny"]
    assert {"command(*)", "write_file(*)", "execute_url(*)", "mcp(*)", "read_url(metaculus.com)"} <= set(deny)
    assert "read_file(*)" not in deny  # it would also deny the saved page files
    brain = str(gemini.brain_dir())
    for rule in (r for r in deny if r.startswith("read_file(")):
        path = rule[len("read_file("):-1]
        assert brain != path and not brain.startswith(path.rstrip("/") + "/")


def test_format_repair_uses_finish_only_agent_and_denies_reads_and_web(tmp_path: Path) -> None:
    argv = gemini.build_argv(_make_req(tmp_path, profile="format_repair"))
    assert argv[argv.index("--agent") + 1] == gemini.FORMAT_REPAIR_AGENT
    deny = json.loads(argv[argv.index("--settings") + 1])["permissions"]["deny"]
    assert {"read_file(*)", "read_url(*)", "command(*)", "write_file(*)"} <= set(deny)
    assert gemini.agent_tools(gemini.FORMAT_REPAIR_AGENT) == ("finish",)


def test_tier2_without_subagents_is_tier1(tmp_path: Path) -> None:
    t1, t2 = _make_req(tmp_path), _make_req(tmp_path, profile="tier2")
    assert gemini.build_argv(t1) == gemini.build_argv(t2)
    on = gemini.build_argv(t2, subagents_enabled=True)
    assert on[on.index("--agent") + 1] == gemini.TIER2_MAIN_AGENT


def test_read_deny_rules_leave_only_the_kept_path() -> None:
    tree = {"/": ["Users", "private", "tmp"], "/Users": ["Shared", "me"], "/Users/me": [".gemini", ".ssh", "project"],
            "/Users/me/.gemini": ["antigravity", "antigravity-cli", "config"],
            "/Users/me/.gemini/antigravity-cli": ["brain", "conversations"]}
    rules = gemini.read_deny_rules(Path("/Users/me/.gemini/antigravity-cli/brain"), lambda p: tree[str(p)])
    assert rules == ["read_file(/private)", "read_file(/tmp)", "read_file(/Users/Shared)",
                     "read_file(/Users/me/.ssh)", "read_file(/Users/me/project)",
                     "read_file(/Users/me/.gemini/antigravity)", "read_file(/Users/me/.gemini/config)",
                     "read_file(/Users/me/.gemini/antigravity-cli/conversations)"]


def test_prepare_agent_files_writes_isolated_agents_and_keeps_unchanged_files(tmp_path: Path) -> None:
    req = _make_req(tmp_path)
    path = gemini.prepare_agent_files(req)
    assert path == req.workdir / ".agents" / "agents" / f"{gemini.TIER1_AGENT}.md"
    text = path.read_text()
    for flag in ("excludeDefaultComponents: true", "inheritCustomizations: false", "inheritMcp: false", "agents: []"):
        assert flag in text.splitlines()
    assert [l[4:] for l in text.splitlines() if l.startswith("  - ")] == list(gemini.TIER1_TOOLS)
    mtime = path.stat().st_mtime_ns
    gemini.prepare_agent_files(req)
    assert path.stat().st_mtime_ns == mtime
    sub = gemini.prepare_agent_files(_make_req(tmp_path, profile="tier2"), subagents_enabled=True)
    assert sub.name == f"{gemini.TIER2_MAIN_AGENT}.md"
    assert (sub.parent / f"{gemini.TIER2_SUBAGENT}.md").read_text().count("subagent: true") == 1


# ---------------------------------------------------------------- parsing

def test_parse_tool_steps_urls_denials_and_usage(tmp_path: Path, _brain: Path) -> None:
    req = _make_req(tmp_path, model="gemini-4-argon-medium", effort="medium")
    gemini.prepare_agent_files(req)
    home = "/Users/me/notes.txt"
    _write_stream(req, [
        _tool(2, "search_web", {"Query": "python"}),
        _tool(3, "read_url_content", {"Url": "https://example.com/"}),
        _tool(4, "view_file", {"AbsolutePath": _page(_brain)}),
        _tool(5, "view_file", {"AbsolutePath": home}, "ERROR", DENY.format(p=home)),
        _tool(6, "read_url_content", {"Url": "https://www.metaculus.com/questions/"}, "ERROR",
              "Permission denied for read_url(metaculus.com). Matches user-configured deny rule."),
    ], OK_RESULT, log='I1 x.go:1] Propagating selected model override to backend: label="Gemini 4 Argon (Medium)"\n')
    res = gemini.parse(req, None)
    assert res.completed and res.error_type is None and res.structured == {"answer": "x"}
    assert res.conversation_id == CONV and res.agent == gemini.TIER1_AGENT and res.agent_sha256
    assert res.registry_tools == ["run_command", "search_web"]
    assert res.violations == []
    assert res.audit_flags == ["local_read_denied", "metaculus_attempted"]
    assert [(e.url, e.state) for e in res.url_events] == [("https://example.com/", "opened"),
                                                          ("https://www.metaculus.com/questions/", "blocked")]
    assert [d["action"] for d in res.denied_actions] == [f"read_file({home})", "read_url(metaculus.com)"]
    assert all(d["evidence_strength"] == "cli_verified" for d in res.denied_actions)
    assert res.usage == {"input_tokens": 100, "output_tokens": 20, "reasoning_tokens": 5, "cached_tokens": 7}
    assert res.usage_raw["log_model_check"] == "verified" and res.model_check == "unverified"
    assert res.declared["tools"] == list(gemini.TIER1_TOOLS) and res.declared["always_added"] == ["manage_task"]
    assert res.tool_call_counts == {"search_web": 1, "read_url_content": 2, "view_file": 2}


def test_parse_flags_unexpected_tools_and_reads_outside_the_conversation(tmp_path: Path, _brain: Path) -> None:
    req = _make_req(tmp_path)
    _write_stream(req, [
        _tool(2, "run_command", {"CommandLine": "touch x"}),
        _tool(3, "manage_task", {"Action": "kill"}),
        _tool(5, "manage_task", {"Action": "list"}),
        _tool(4, "view_file", {"AbsolutePath": str(_brain / "other-conv" / "x.md")}),
    ], OK_RESULT)
    res = gemini.parse(req, None)
    assert [(v["type"], v.get("tool") or v.get("path")) for v in res.violations] == [
        ("unexpected_tool", "run_command"), ("unexpected_tool", "manage_task"),
        ("local_read", str(_brain / "other-conv" / "x.md"))]
    assert res.audit_flags == ["manage_task_query"]


def test_parse_soft_deny_stops_the_run_as_tool_denied(tmp_path: Path) -> None:
    req = _make_req(tmp_path)
    p = "/Users/me/x.txt"
    _write_stream(req, [_tool(2, "view_file", {"AbsolutePath": p}, "ERROR", SOFT.format(p=p))],
                  {"status": "SUCCESS", "response": "", "denied_actions": [{"action": "read_file", "display_name": "ViewFile"}]})
    res = gemini.parse(req, None)
    assert not res.completed and res.error_type == "tool_denied"
    assert res.denied_actions[0]["reason"] == "cli_soft_deny" and res.denied_actions[0]["action"] == f"read_file({p})"
    assert res.denied_actions[1]["source"] == "stream_result_denied_actions"


def test_parse_errors_timeout_quota_and_invalid_output(tmp_path: Path) -> None:
    req = _make_req(tmp_path)
    _write_stream(req, [], {"status": "SUCCESS", "response": "plain text"})
    assert gemini.parse(req, None).error_type == "invalid_output"

    run = RunResult(exit_code=1, timed_out=False, killed_signal=None, duration_s=1.0, started_at="a", finished_at="b",
                    stdout_path=req.raw_dir / gemini.STDOUT_FILENAME, stderr_path=req.raw_dir / "stderr.txt",
                    scrubbed_env_names=[])
    _write_stream(req, [], None)
    (req.raw_dir / "stderr.txt").write_text("RESOURCE_EXHAUSTED: weekly limit reached")
    assert gemini.parse(req, run).error_type == "quota_exhausted"
    (req.raw_dir / "stderr.txt").write_text("boom")
    res = gemini.parse(req, run)
    assert res.error_type == "cli_error" and res.error == "boom"

    timed = RunResult(**{**run.__dict__, "timed_out": True, "exit_code": None})
    _write_stream(req, [], OK_RESULT)
    assert gemini.parse(req, timed).error_type == "timeout"

    (req.raw_dir / gemini.STDOUT_FILENAME).write_text('{"event": "init"\nnot json\n')
    res = gemini.parse(req, None)
    assert not res.completed and res.parse_errors


def test_finish_step_completes_under_its_own_step_type(tmp_path: Path) -> None:
    req = _make_req(tmp_path)
    finish = _tool(9, "finish", {"steps": []}, "ACTIVE")
    finish.append({"event": "step_update", "step_update": {"step_index": 9, "state": "DONE", "step_type": "finish"}})
    other = {"event": "step_update", "step_update": {"step_index": 10, "state": "DONE", "step_type": "agent_response"}}
    _write_stream(req, [finish, [other]], OK_RESULT)
    res = gemini.parse(req, None)
    assert [(s["tool"], s["state"]) for s in res.tool_steps] == [("finish", "DONE")] and not res.violations


def test_pending_tool_step_is_kept(tmp_path: Path) -> None:
    req = _make_req(tmp_path)
    _write_stream(req, [_tool(2, "read_url_content", {"Url": "https://example.com/"}, "ACTIVE")], OK_RESULT)
    res = gemini.parse(req, None)
    assert res.tool_steps == [{"step": 2, "tool": "read_url_content", "state": "ACTIVE",
                               "parameters": {"Url": "https://example.com/"}}]
    assert res.url_events[0].state == "attempted"


def test_apply_quota_model_check(tmp_path: Path) -> None:
    req = _make_req(tmp_path, model="gemini-4-argon-high", effort="high")
    _write_stream(req, [], OK_RESULT,
                  log='I1 x.go:1] Propagating selected model override to backend: label="Gemini 4 Argon (Medium)"\n')
    res = gemini.parse(req, None)
    assert res.usage_raw["log_model_check"] == "mismatch" and res.model_check == "unverified"
    gemini.apply_quota_model_check(res, "mismatch")
    assert res.model_check == "mismatch" and res.error_type == "model_mismatch"
    assert res.usage_raw["quota_model_check"] == "mismatch"
    with pytest.raises(ValueError):
        gemini.apply_quota_model_check(res, "maybe")


def test_cli_version_parsing() -> None:
    sample = "1.4.12:\n· Added optional true for API key providers.\n\n1.4.11:\n· Added billing model choice.\n"
    assert gemini._parse_changelog_version(sample) == "1.4.12"
    assert gemini._parse_changelog_version("") is None


def test_run_writes_schema_and_agent_and_invokes_runner(tmp_path: Path) -> None:
    req = _make_req(tmp_path, profile="format_repair")
    seen: dict = {}

    def fake_run(argv, *, cwd, stdin_text, timeout_s, stdout_path, stderr_path, keep_env):
        seen.update(argv=argv, cwd=cwd, keep_env=keep_env)
        assert (req.raw_dir / "schema.json").read_text() == json.dumps(SCHEMA)
        assert (req.workdir / ".agents" / "agents" / f"{gemini.FORMAT_REPAIR_AGENT}.md").is_file()
        _write_stream(req, [], OK_RESULT)
        stderr_path.write_text("")
        return RunResult(exit_code=0, timed_out=False, killed_signal=None, duration_s=0.1, started_at="a",
                         finished_at="b", stdout_path=stdout_path, stderr_path=stderr_path, scrubbed_env_names=[])

    with patch("adapters.gemini.flab_runner.run", side_effect=fake_run), \
            patch("adapters.gemini.cli_version", return_value="1.4.12"):
        res = gemini.run(req)
    assert res.completed and res.cli_version == "1.4.12" and res.argv == seen["argv"]
    assert seen["cwd"] == req.workdir and seen["keep_env"] == ()


def test_targets_and_page_files_use_only_each_tools_parameter(tmp_path: Path, _brain: Path) -> None:
    page = _page(_brain)
    assert gemini._params_target("view_file", {"AbsolutePath": "/outside.txt", "Url": page}) == "/outside.txt"
    assert gemini._params_target("read_url_content", {"Url": "https://a/", "AbsolutePath": page}) == "https://a/"
    assert gemini.own_page_file(page, CONV)
    for bad in ("/etc", "../x", "a/b", "", None):
        assert not gemini.own_page_file(page, bad)
    req = _make_req(tmp_path)
    _write_stream(req, [_tool(2, "view_file", {"AbsolutePath": "/outside.txt", "Url": page})], OK_RESULT)
    assert [v["type"] for v in gemini.parse(req, None).violations] == ["local_read"]


def test_read_deny_rules_fail_when_a_level_cannot_be_listed() -> None:
    def listdir(p: Path) -> list[str]:
        if str(p) == "/Users":
            raise PermissionError("denied")
        return ["Users", "tmp"]
    with pytest.raises(OSError):
        gemini.read_deny_rules(Path("/Users/me/brain"), listdir)
