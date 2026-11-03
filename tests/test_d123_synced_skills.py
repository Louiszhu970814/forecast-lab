"""D123: claude.ai-synced skills are informational; loading any skill still stops the line."""
from types import SimpleNamespace

import baseline
import forecast as F


def _sources(tmp_path):
    srcs = [s for s in baseline.default_sources() if s.source_id.startswith("claude.skills")]
    return {s.source_id: s for s in srcs}


def test_synced_bucket_changes_are_informational_but_personal_skills_stop(tmp_path):
    srcs = _sources(tmp_path)
    personal = tmp_path / ".claude/skills/mine"
    synced = tmp_path / ".claude/skills/synced/bucket/docx"
    personal.mkdir(parents=True); synced.mkdir(parents=True)
    (personal / "SKILL.md").write_text("synthetic personal skill")
    (synced / "SKILL.md").write_text("synthetic synced v1")
    snap = lambda: {"sources": {sid: baseline.inspect_source(s, tmp_path) for sid, s in srcs.items()},
                    "versions": {"claude": "1", "gemini": "1", "gpt": "1"}, "gemini_credits": 0}
    approved = snap()
    (synced / "SKILL.md").write_text("synthetic synced v2")
    (synced.parent / ".last-complete-round").write_text("round 2")
    result = baseline.compare(approved, snap())
    assert result["decisions"]["claude"] == "ok"
    assert {c["source"] for c in result["changes"]} == {"claude.skills_synced"}
    (personal / "SKILL.md").write_text("synthetic personal skill changed")
    assert baseline.compare(approved, snap())["decisions"]["claude"] == "stop"


def _result(declared):
    return SimpleNamespace(violations=[], tool_call_counts={}, subagent_count=0, declared=declared)


def test_any_declared_skill_command_mcp_or_extra_plugin_is_a_violation():
    clean = {"tools": ["StructuredOutput", "WebFetch", "WebSearch"], "skills": [], "slash_commands": [],
             "mcp_servers": [], "plugins": [{"name": "cc-plugin-telemetry", "path": "builtin"}]}
    assert F.tool_violations("claude", _result(clean), repair=False) == []
    for key, value in (("skills", ["docx"]), ("slash_commands", ["pdf"]), ("mcp_servers", [{"name": "x"}]),
                       ("plugins", [{"name": "x", "path": "/tmp/x"}])):
        found = F.tool_violations("claude", _result({**clean, key: value}), repair=False)
        assert found == [{"type": "unexpected_declaration", "what": key, "count": 1}]
    # Missing init declarations alone do not create a stop (fail soft for early CLI errors).
    assert F.tool_violations("claude", _result(None), repair=False) == []
