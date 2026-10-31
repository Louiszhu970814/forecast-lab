from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from adapters import gemini
from adapters.base import AdapterRequest, is_metaculus_host
from flab.runner import RunResult

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "gemini"


def _make_req(
    tmp_path: Path,
    *,
    profile: str = "tier1",
    model: str = "gemini-3.8-flash-low",
    effort: str = "low",
    timeout_s: int = 600,
) -> AdapterRequest:
    workdir = tmp_path / "work"
    raw_dir = tmp_path / "raw"
    return AdapterRequest(
        player="gemini",
        profile=profile,
        prompt="Test prompt",
        schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        workdir=workdir,
        raw_dir=raw_dir,
        model=model,
        effort=effort,
        timeout_s=timeout_s,
    )


def test_build_argv_tier1_and_acceptance(tmp_path: Path) -> None:
    for profile in ("tier1", "acceptance"):
        req = _make_req(tmp_path / profile, profile=profile)
        argv = gemini.build_argv(req)

        # build_argv must not create directories or files
        assert not req.workdir.exists()
        assert not req.raw_dir.exists()

        assert argv[0] == "agy"
        assert "--mode" not in argv
        assert "--agent" not in argv
        assert "--dangerously-skip-permissions" not in argv
        assert "--sandbox" in argv
        assert "--disable-slash-commands" in argv

        idx_p = argv.index("-p")
        assert argv[idx_p + 1] == "Test prompt"

        idx_m = argv.index("--model")
        assert argv[idx_m + 1] == req.model

        idx_e = argv.index("--effort")
        assert argv[idx_e + 1] == req.effort

        idx_fmt = argv.index("--output-format")
        assert argv[idx_fmt + 1] == "json"

        idx_schema = argv.index("--json-schema")
        assert argv[idx_schema + 1] == str(req.raw_dir / "schema.json")

        idx_to = argv.index("--print-timeout")
        assert argv[idx_to + 1] == "600s"

        idx_log = argv.index("--log-file")
        assert argv[idx_log + 1] == str(req.raw_dir / "agy_cli.log")

        idx_set = argv.index("--settings")
        expected_settings = (
            '{"agentMode":"","useG1Credits":false,"permissions":'
            '{"allow":["read_url(*)"],"deny":["command(*)","write_file(*)",'
            '"read_file(*)","execute_url(*)","mcp(*)","read_url(metaculus.com)"]}}'
        )
        assert argv[idx_set + 1] == expected_settings
        parsed_settings = json.loads(argv[idx_set + 1])
        assert "use_ai_credits" not in parsed_settings


def test_build_argv_format_repair_uses_tool_less_agent_and_denies_all_permissions(
    tmp_path: Path,
) -> None:
    req = _make_req(tmp_path, profile="format_repair")
    argv = gemini.build_argv(req)

    # Pure build_argv: does not touch disk
    assert not req.workdir.exists()

    # 1st layer: custom `--agent` with excludeDefaultComponents: true and empty tools/agents
    idx_agent = argv.index("--agent")
    expected_agent_path = req.workdir / gemini.FORMAT_REPAIR_AGENT_FILENAME
    assert argv[idx_agent + 1] == str(expected_agent_path)

    written_path = gemini.prepare_agent_files(req)
    assert written_path == expected_agent_path
    assert expected_agent_path.is_file()
    agent_text = expected_agent_path.read_text(encoding="utf-8")
    assert "excludeDefaultComponents: true" in agent_text
    assert "inheritCustomizations: false" in agent_text
    assert "inheritMcp: false" in agent_text
    assert "tools: []" in agent_text
    assert "agents: []" in agent_text
    for forbidden_tool in (
        "search_web",
        "read_url_content",
        "invoke_subagent",
        "define_subagent",
        "schedule",
    ):
        assert forbidden_tool not in agent_text

    # 2nd layer: permission deny rules retained as defense-in-depth
    idx_set = argv.index("--settings")
    settings = json.loads(argv[idx_set + 1])
    assert settings["agentMode"] == ""
    assert settings["useG1Credits"] is False
    assert settings["permissions"]["allow"] == []
    for action in (
        "command(*)",
        "write_file(*)",
        "read_file(*)",
        "read_url(*)",
        "execute_url(*)",
        "mcp(*)",
    ):
        assert action in settings["permissions"]["deny"]


def test_build_argv_tier2_pure_and_prepare_writes_agent_files(tmp_path: Path) -> None:
    req = _make_req(
        tmp_path,
        profile="tier2",
        model="gemini-4-argon-high",
        effort="high",
        timeout_s=1200,
    )
    argv_default = gemini.build_argv(req)
    assert "--agent" not in argv_default
    assert not req.workdir.exists()

    argv_enabled = gemini.build_argv(req, subagents_enabled=True)
    main_agent = req.workdir / gemini.TIER2_MAIN_AGENT_FILENAME
    sub_agent = req.workdir / gemini.TIER2_SUBAGENT_FILENAME
    idx_agent = argv_enabled.index("--agent")
    assert argv_enabled[idx_agent + 1] == str(main_agent)
    # Still pure: build_argv never writes files
    assert not req.workdir.exists()

    # Writing happens in prepare_agent_files / run()
    gemini.prepare_agent_files(req)
    assert main_agent.is_file()
    assert sub_agent.is_file()

    main_text = main_agent.read_text(encoding="utf-8")
    sub_text = sub_agent.read_text(encoding="utf-8")
    assert "excludeDefaultComponents: true" in main_text
    assert "inheritCustomizations: false" in main_text
    assert "inheritMcp: false" in main_text
    assert "invoke_subagent" in main_text
    assert f"./{gemini.TIER2_SUBAGENT_FILENAME}" in main_text

    assert "subagent: true" in sub_text
    assert "model: inherit" in sub_text
    assert "excludeDefaultComponents: true" in sub_text
    assert "invoke_subagent" not in sub_text


def test_parse_is_read_only_and_run_propagates_write_errors(tmp_path: Path) -> None:
    # parse() on tier2 and format_repair must not create workdir or write agent files
    for profile in ("tier2", "format_repair"):
        req = _make_req(tmp_path / profile, profile=profile, model="gemini-4-argon-medium", effort="medium")
        shutil.copytree(FIXTURES_DIR / "argon_ok", req.raw_dir)
        raw_files_before = sorted(p.name for p in req.raw_dir.iterdir())

        res = gemini.parse(req, None)
        assert res.completed is True
        assert not req.workdir.exists()
        raw_files_after = sorted(p.name for p in req.raw_dir.iterdir())
        assert raw_files_after == raw_files_before

    # run() must fail explicitly (raise OSError) if writing agent files fails
    bad_req = _make_req(tmp_path / "bad_write", profile="format_repair")
    bad_req.workdir.parent.mkdir(parents=True, exist_ok=True)
    # Make workdir a regular file so mkdir/write inside it raises OSError
    bad_req.workdir.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OSError):
        gemini.run(bad_req)


def test_parse_tier1_ok_fixture(tmp_path: Path) -> None:
    req = _make_req(tmp_path, profile="tier1", model="gemini-3.8-flash-low", effort="low")
    shutil.copytree(FIXTURES_DIR / "tier1_ok", req.raw_dir)

    res = gemini.parse(req, None)
    assert res.completed is True
    assert res.cli_version == "1.4.12"
    assert res.reported_model_id == "Gemini 3.8 Flash (Low)"
    # Adapter returns unverified; log label is stored in usage_raw as auxiliary evidence
    assert res.model_check == "unverified"
    assert res.error_type is None
    assert res.error is None
    assert res.structured is not None
    assert res.structured["opened_urls"] == []
    assert res.usage == {
        "input_tokens": 15806,
        "output_tokens": 105,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
    }
    assert res.usage_raw is not None
    assert res.usage_raw["total_tokens"] == 15911
    assert res.usage_raw["num_turns"] == 1
    assert res.usage_raw["model_label"] == "Gemini 3.8 Flash (Low)"
    assert res.usage_raw["log_model_check"] == "verified"
    assert res.denied_actions is None
    # Because tier1_ok states "no browser tool available to open pages", refused URLs are unknown, not blocked
    assert len(res.url_events) == 2
    hosts = [ev.host for ev in res.url_events]
    assert "www.python.org" in hosts
    assert "www.metaculus.com" in hosts
    assert all(ev.state == "unknown" for ev in res.url_events)
    assert all(ev.source == "structured_output.refused:no_browser_tool" for ev in res.url_events)


def test_parse_cmd_denied_rule_fixture(tmp_path: Path) -> None:
    req = _make_req(tmp_path, profile="acceptance", model="gemini-3.8-flash-low", effort="low")
    shutil.copytree(FIXTURES_DIR / "cmd_denied", req.raw_dir)

    res = gemini.parse(req, None)
    # Deny rule lets turn finish with status=SUCCESS and non-empty response
    assert res.completed is True
    assert res.model_check == "unverified"
    assert res.usage_raw is not None
    assert res.usage_raw["model_label"] == "Gemini 3.8 Flash (Low)"
    assert res.error_type is None
    assert res.denied_actions is not None
    assert len(res.denied_actions) == 1
    assert res.denied_actions[0]["action"] == "ls"
    assert res.denied_actions[0]["source"] == "model_output_deny_rule"
    assert res.denied_actions[0]["evidence_strength"] == "model_self_report"
    assert "Matches user-configured deny rule" in res.denied_actions[0]["reason"]


def test_parse_ask_denied_fixture(tmp_path: Path) -> None:
    req = _make_req(tmp_path, profile="tier1", model="gemini-4-argon-medium", effort="medium")
    shutil.copytree(FIXTURES_DIR / "ask_denied", req.raw_dir)

    res = gemini.parse(req, None)
    # Ask auto-denial in print mode ends turn with empty response -> incomplete & tool_denied
    assert res.completed is False
    assert res.final_text is None
    assert res.reported_model_id == "Gemini 4 Argon (Medium)"
    assert res.model_check == "unverified"
    assert res.error_type == "tool_denied"
    assert res.usage == {
        "input_tokens": 27257,
        "output_tokens": 942,
        "reasoning_tokens": 238,
        "cached_tokens": 8782,
    }
    assert res.usage_raw is not None
    assert res.usage_raw["total_tokens"] == 28199
    assert res.usage_raw["model_label"] == "Gemini 4 Argon (Medium)"
    assert res.denied_actions is not None
    assert len(res.denied_actions) == 1
    assert res.denied_actions[0]["action"] == "read_file"
    assert res.denied_actions[0]["display_name"] == "ReadFile"
    assert res.denied_actions[0]["source"] == "json_denied_actions"
    assert res.denied_actions[0]["evidence_strength"] == "cli_verified"
    assert res.denied_actions[0]["step"] == 10
    assert res.denied_actions[0]["log_confirmed"] is True


def test_parse_argon_ok_and_apply_quota_model_check(tmp_path: Path) -> None:
    req_ok = _make_req(tmp_path / "ok", profile="tier1", model="gemini-4-argon-medium", effort="medium")
    shutil.copytree(FIXTURES_DIR / "argon_ok", req_ok.raw_dir)
    res_ok = gemini.parse(req_ok, None)
    assert res_ok.completed is True
    assert res_ok.model_check == "unverified"
    assert res_ok.usage_raw is not None
    assert res_ok.usage_raw["model_label"] == "Gemini 4 Argon (Medium)"
    assert res_ok.usage_raw["log_model_check"] == "verified"
    assert res_ok.usage == {
        "input_tokens": 152526,
        "output_tokens": 21155,
        "reasoning_tokens": 6053,
        "cached_tokens": 1223995,
    }
    # A URL only present in `sources` is a citation without page-open evidence -> state="unknown"
    assert len(res_ok.url_events) == 1
    assert res_ok.url_events[0].host == "example.org"
    assert res_ok.url_events[0].state == "unknown"
    assert res_ok.url_events[0].source == "structured_output.sources:citation_only"

    # Main workflow updates model_check from quota.judge result
    gemini.apply_quota_model_check(res_ok, "verified")
    assert res_ok.model_check == "verified"
    assert res_ok.usage_raw["quota_model_check"] == "verified"
    assert res_ok.error_type is None

    # When log label differs from requested model, parse() still returns unverified (auxiliary in usage_raw)
    req_mm = _make_req(tmp_path / "mm", profile="tier2", model="gemini-4-argon-high", effort="high")
    shutil.copytree(FIXTURES_DIR / "argon_ok", req_mm.raw_dir)
    res_mm = gemini.parse(req_mm, None)
    assert res_mm.model_check == "unverified"
    assert res_mm.usage_raw is not None
    assert res_mm.usage_raw["model_label"] == "Gemini 4 Argon (Medium)"
    assert res_mm.usage_raw["log_model_check"] == "mismatch"
    assert res_mm.error_type is None

    # When quota.judge reports mismatch, apply_quota_model_check updates model_check & error_type
    gemini.apply_quota_model_check(res_mm, "mismatch")
    assert res_mm.model_check == "mismatch"
    assert res_mm.error_type == "model_mismatch"
    assert res_mm.usage_raw["quota_model_check"] == "mismatch"


def test_url_events_distinguish_opened_blocked_citation_and_no_tool() -> None:
    # 1. Search result cited in `sources`, `opened_urls` has another URL, `refused` has a deny-rule URL
    # and a no-browser-tool URL.
    structured = {
        "answer": "Opened python.org, metaculus was denied by rule.",
        "opened_urls": ["https://www.python.org/psf-landing/"],
        "refused": [
            "https://www.metaculus.com/questions/ (Matches user-configured deny rule)",
            "https://example.net/page (no browser tool available)",
            "https://unverified.example.org/item",
        ],
        "sources": [
            {"url": "https://www.python.org/psf-landing/", "title": "PSF"},
            {"url": "https://news.example.com/snippet", "title": "Cited from search snippet"},
        ],
    }
    events = gemini._extract_url_events(structured, final_text=json.dumps(structured))
    by_host = {ev.host: ev for ev in events}

    # python.org was in opened_urls -> state="opened" (and not duplicated as unknown from sources)
    assert by_host["www.python.org"].state == "opened"
    assert by_host["www.python.org"].source == "structured_output.opened_urls:model_report"

    # metaculus.com had explicit deny-rule evidence -> state="blocked"
    assert is_metaculus_host(by_host["www.metaculus.com"].host)
    assert by_host["www.metaculus.com"].state == "blocked"
    assert by_host["www.metaculus.com"].source == "structured_output.refused:deny_rule_model_report"

    # example.net had "no browser tool available" -> state="unknown"
    assert by_host["example.net"].state == "unknown"
    assert by_host["example.net"].source == "structured_output.refused:no_browser_tool"

    # unverified.example.org had no specific reason -> state="unknown"
    assert by_host["unverified.example.org"].state == "unknown"
    assert by_host["unverified.example.org"].source == "structured_output.refused:unverified_reason"

    # news.example.com was only in `sources` -> state="unknown" (citation only)
    assert by_host["news.example.com"].state == "unknown"
    assert by_host["news.example.com"].source == "structured_output.sources:citation_only"


def test_url_events_cli_denial_matches_target_url_only(tmp_path: Path) -> None:
    # 1. Cross-URL + no-browser-tool mixed case:
    # refused has example.net (no browser tool available), other.example, and unverified.example.org,
    # while CLI denial specifically targets https://other.example/.
    structured = {
        "answer": "Attempted URL reads.",
        "opened_urls": [],
        "refused": [
            "https://example.net/ (no browser tool available)",
            "https://other.example/",
            "https://unverified.example.org/path",
        ],
    }
    cli_denials_targeted = [
        {
            "action": "read_url(https://other.example/)",
            "display_name": "ReadUrl",
            "source": "json_denied_actions",
            "evidence_strength": "cli_verified",
        }
    ]
    events = gemini._extract_url_events(
        structured,
        final_text=json.dumps(structured),
        log_denials=[],
        denied_actions=cli_denials_targeted,
    )
    by_host = {ev.host: ev for ev in events}

    # Only https://other.example/ matches the CLI denial
    assert by_host["other.example"].state == "blocked"
    assert by_host["other.example"].source == "structured_output.refused:cli_verified"

    # https://example.net/ must NOT be marked cli_verified blocked; keeps no_browser_tool unknown
    assert by_host["example.net"].state == "unknown"
    assert by_host["example.net"].source == "structured_output.refused:no_browser_tool"

    # https://unverified.example.org/path also does not match the CLI denial -> unknown
    assert by_host["unverified.example.org"].state == "unknown"
    assert by_host["unverified.example.org"].source == "structured_output.refused:unverified_reason"

    # 2. Targetless read_url / ReadUrl CLI denial:
    # preserved in denied_actions, but must NOT mark any refused URL as cli_verified blocked.
    req = _make_req(tmp_path, profile="tier1", model="gemini-4-argon-medium", effort="medium")
    req.raw_dir.mkdir(parents=True)
    (req.raw_dir / "stdout.json").write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "response": json.dumps(structured),
                "structured_output": structured,
                "denied_actions": [{"action": "read_url", "display_name": "ReadUrl"}],
            }
        ),
        encoding="utf-8",
    )
    res = gemini.parse(req, None)
    assert res.denied_actions is not None
    assert len(res.denied_actions) == 1
    assert res.denied_actions[0]["action"] == "read_url"
    assert res.denied_actions[0]["evidence_strength"] == "cli_verified"

    parsed_by_host = {ev.host: ev for ev in res.url_events}
    assert parsed_by_host["example.net"].state == "unknown"
    assert parsed_by_host["example.net"].source == "structured_output.refused:no_browser_tool"
    assert parsed_by_host["other.example"].state == "unknown"
    assert parsed_by_host["other.example"].source == "structured_output.refused:unverified_reason"
    assert parsed_by_host["unverified.example.org"].state == "unknown"
    assert parsed_by_host["unverified.example.org"].source == "structured_output.refused:unverified_reason"


def test_denied_actions_log_only_response_only_mixed_refused_and_mere_quote() -> None:
    # 1. Log-only soft denial
    log_only = gemini._collect_denied_actions(
        data={"status": "SUCCESS", "response": ""},
        final_text=None,
        structured=None,
        log_denials=[
            {
                "display_name": "RunCommand",
                "source": "cli_log",
                "evidence_strength": "cli_verified",
                "reason": "soft_denied_in_print_mode",
                "step": 3,
            }
        ],
    )
    assert log_only is not None
    assert len(log_only) == 1
    assert log_only[0]["source"] == "cli_log"
    assert log_only[0]["evidence_strength"] == "cli_verified"
    assert log_only[0]["display_name"] == "RunCommand"

    # 2. Response-only deny rule (no structured `refused` list)
    resp_only = gemini._collect_denied_actions(
        data={"status": "SUCCESS"},
        final_text="Tool call failed: Matches user-configured deny rule.",
        structured={"probability": 0.5},
        log_denials=[],
    )
    assert resp_only is not None
    assert len(resp_only) == 1
    assert resp_only[0]["action"] is None
    assert resp_only[0]["source"] == "model_output_deny_rule"
    assert resp_only[0]["evidence_strength"] == "model_self_report"

    # 3. Mixed `refused` items: one deny-rule item + one no-browser-tool item
    mixed_struct = {
        "answer": "Command ls failed with Matches user-configured deny rule; no browser tool available for URL.",
        "refused": [
            "ls (Matches user-configured deny rule)",
            "Opening https://www.python.org/ (no browser tool available)",
        ],
    }
    mixed = gemini._collect_denied_actions(
        data={"status": "SUCCESS"},
        final_text=json.dumps(mixed_struct),
        structured=mixed_struct,
        log_denials=[],
    )
    assert mixed is not None
    assert len(mixed) == 1
    assert mixed[0]["action"] == "ls (Matches user-configured deny rule)"

    # 4. Mere hypothetical quotation of "Matches user-configured deny rule" with empty `refused`
    quote_struct = {
        "answer": "If a tool returns 'Matches user-configured deny rule', it will be listed in refused.",
        "refused": [],
    }
    quoted = gemini._collect_denied_actions(
        data={"status": "SUCCESS"},
        final_text=json.dumps(quote_struct),
        structured=quote_struct,
        log_denials=[],
    )
    assert quoted is None


def test_parse_timeout_and_finished_reason(tmp_path: Path) -> None:
    req = _make_req(tmp_path, profile="tier1", model="gemini-4-argon-medium", effort="medium")
    req.raw_dir.mkdir(parents=True)
    (req.raw_dir / "stdout.json").write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "finished_reason": "print_timeout",
                "response": '{"partial":true}',
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "thinking_tokens": 0,
                    "cache_read_tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    res = gemini.parse(req, None)
    assert res.completed is False
    assert res.error_type == "timeout"
    assert res.model_check == "unverified"


def test_parse_runner_nonzero_exit_and_forced_timeout_with_complete_json(tmp_path: Path) -> None:
    # Even if stdout.json has status="SUCCESS" and a non-empty response, a non-zero exit_code
    # or runner-level timed_out=True must mark completed=False.
    req_nz = _make_req(tmp_path / "nz", profile="tier1", model="gemini-4-argon-medium", effort="medium")
    shutil.copytree(FIXTURES_DIR / "argon_ok", req_nz.raw_dir)
    nz_run = RunResult(
        exit_code=1,
        timed_out=False,
        killed_signal=None,
        duration_s=2.0,
        started_at="2026-10-31T09:00:00Z",
        finished_at="2026-10-31T09:00:02Z",
        stdout_path=req_nz.raw_dir / "stdout.json",
        stderr_path=req_nz.raw_dir / "stderr.txt",
        scrubbed_env_names=[],
    )
    res_nz = gemini.parse(req_nz, nz_run)
    assert res_nz.completed is False
    assert res_nz.error_type == "cli_error"
    assert "non-zero exit code (1)" in (res_nz.error or "")

    req_to = _make_req(tmp_path / "to", profile="tier1", model="gemini-4-argon-medium", effort="medium")
    shutil.copytree(FIXTURES_DIR / "argon_ok", req_to.raw_dir)
    to_run = RunResult(
        exit_code=-9,
        timed_out=True,
        killed_signal=9,
        duration_s=600.0,
        started_at="2026-10-31T09:00:00Z",
        finished_at="2026-10-31T09:10:00Z",
        stdout_path=req_to.raw_dir / "stdout.json",
        stderr_path=req_to.raw_dir / "stderr.txt",
        scrubbed_env_names=[],
    )
    res_to = gemini.parse(req_to, to_run)
    assert res_to.completed is False
    assert res_to.error_type == "timeout"


def test_parse_cli_error_and_quota_exhausted(tmp_path: Path) -> None:
    req_err = _make_req(tmp_path / "err", profile="tier1", model="gemini-4-argon-medium", effort="medium")
    req_err.raw_dir.mkdir(parents=True)
    (req_err.raw_dir / "stdout.json").write_text("not valid json", encoding="utf-8")
    (req_err.raw_dir / "stderr.txt").write_text("Error: --settings: unknown setting", encoding="utf-8")
    res_err = gemini.parse(req_err, None)
    assert res_err.completed is False
    assert res_err.error_type == "cli_error"
    assert "unknown setting" in (res_err.error or "")

    req_q = _make_req(tmp_path / "quota", profile="tier1", model="gemini-4-argon-medium", effort="medium")
    req_q.raw_dir.mkdir(parents=True)
    (req_q.raw_dir / "stdout.json").write_text(
        json.dumps(
            {
                "status": "ERROR",
                "error": "RESOURCE_EXHAUSTED: weekly limit reached",
                "response": "",
            }
        ),
        encoding="utf-8",
    )
    res_q = gemini.parse(req_q, None)
    assert res_q.completed is False
    assert res_q.error_type == "quota_exhausted"


def test_cli_version_parsing() -> None:
    sample = (
        "1.4.12:\n"
        "· Added optional true for API key providers.\n\n"
        "1.4.11:\n"
        "· Added billing model choice.\n"
    )
    assert gemini._parse_changelog_version(sample) == "1.4.12"
    assert gemini._parse_changelog_version("") is None


def test_run_writes_schema_and_agent_files_and_invokes_flab_runner(tmp_path: Path) -> None:
    req = _make_req(tmp_path, profile="format_repair", model="gemini-3.8-flash-low", effort="low")

    def fake_run(
        argv: list[str],
        *,
        cwd: Path,
        stdin_text: str | None,
        timeout_s: float,
        stdout_path: Path,
        stderr_path: Path,
        keep_env: tuple[str, ...],
    ) -> RunResult:
        assert (req.raw_dir / "schema.json").is_file()
        assert (req.workdir / gemini.FORMAT_REPAIR_AGENT_FILENAME).is_file()
        assert "--agent" in argv
        assert keep_env == ()
        shutil.copyfile(FIXTURES_DIR / "tier1_ok" / "stdout.json", stdout_path)
        shutil.copyfile(FIXTURES_DIR / "tier1_ok" / "agy_cli.log", req.raw_dir / "agy_cli.log")
        stderr_path.write_text("", encoding="utf-8")
        return RunResult(
            exit_code=0,
            timed_out=False,
            killed_signal=None,
            duration_s=1.2,
            started_at="2026-10-31T09:00:00Z",
            finished_at="2026-10-31T09:00:01Z",
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            scrubbed_env_names=[],
        )

    with patch("adapters.gemini.flab_runner.run", side_effect=fake_run):
        res = gemini.run(req)

    assert res.completed is True
    assert res.cli_version == "1.4.12"
    assert res.model_check == "unverified"
    assert res.usage_raw is not None
    assert res.usage_raw["model_label"] == "Gemini 3.8 Flash (Low)"



def test_cli_url_denial_matching_is_bound_to_read_url_targets():
    from adapters import gemini as g
    cli = {"source": "json_denied_actions", "evidence_strength": "cli_verified"}
    # A denied shell command that mentions a URL is not evidence that reading that URL was blocked.
    assert not g._cli_entry_matches_url({**cli, "action": "command(curl https://example.net/)"}, "https://example.net/")
    # Different resources: trailing slash on a non-root path is kept.
    assert not g._cli_entry_matches_url({**cli, "action": "read_url(https://example.net/path/)"}, "https://example.net/path")
    # Same target matches; root "" and "/" are the same; a bare domain rule covers its subdomains.
    assert g._cli_entry_matches_url({**cli, "action": "read_url(https://example.net/path)"}, "https://example.net/path")
    assert g._cli_entry_matches_url({**cli, "action": "read_url(https://example.net)"}, "https://example.net/")
    assert g._cli_entry_matches_url({**cli, "action": "read_url(metaculus.com)"}, "https://www.metaculus.com/questions/")
    assert g._cli_entry_matches_url({**cli, "action": "read_url", "url": "https://example.net/a"}, "https://example.net/a")
    # Model self-reports never count as CLI evidence.
    assert not g._cli_entry_matches_url({"source": "model_output_deny_rule", "evidence_strength": "model_self_report",
                                         "action": "read_url(https://example.net/)"}, "https://example.net/")



def test_cli_target_keeps_trailing_punctuation():
    from adapters import gemini as g
    cli = {"source": "json_denied_actions", "evidence_strength": "cli_verified"}
    assert not g._cli_entry_matches_url({**cli, "action": "read_url(https://example.net/path.)"}, "https://example.net/path")
    assert not g._cli_entry_matches_url({**cli, "action": "read_url(https://example.net/path?q=a;)"}, "https://example.net/path?q=a")
    assert g._cli_entry_matches_url({**cli, "action": "read_url(https://example.net/path.)"}, "https://example.net/path.")
    assert g._cli_entry_matches_url({**cli, "action": "read_url(https://example.net/path?q=a;)"}, "https://example.net/path?q=a;")
    # Reverse direction: without explicit delimiters a trailing "." or ";" may be part of the resource.
    assert not g._cli_entry_matches_url({**cli, "action": "read_url(https://example.net/path)"}, "https://example.net/path.")
    assert not g._cli_entry_matches_url({**cli, "action": "read_url(https://example.net/path?q=a)"}, "https://example.net/path?q=a;")
    assert not g._cli_entry_matches_url({**cli, "action": "read_url", "url": "https://example.net/path"}, "https://example.net/path.")
