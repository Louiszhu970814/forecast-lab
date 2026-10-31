from __future__ import annotations

from pathlib import Path

import pytest

from adapters.base import (
    AdapterRequest,
    AdapterResult,
    UrlEvent,
    is_metaculus_host,
    model_check_from_ids,
    url_host,
)


def _req(**overrides: object) -> AdapterRequest:
    fields: dict[str, object] = dict(
        player="claude",
        profile="tier1",
        prompt="p",
        schema={},
        workdir=Path("/tmp/w"),
        raw_dir=Path("/tmp/r"),
        model="claude-opus-5-5",
        effort="medium",
        timeout_s=600,
    )
    fields.update(overrides)
    return AdapterRequest(**fields)  # type: ignore[arg-type]


def test_request_validates_player_profile_and_timeout() -> None:
    assert _req().player == "claude"
    with pytest.raises(ValueError):
        _req(player="agy")
    with pytest.raises(ValueError):
        _req(profile="tier3")
    with pytest.raises(ValueError):
        _req(timeout_s=0)


def test_result_defaults_and_validation() -> None:
    res = AdapterResult(
        argv=["x"], run=None, cli_version=None, completed=False, final_text=None,
        structured=None, reported_model_id=None, model_check="unverified", usage_raw=None,
    )
    assert res.usage == {"input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "cached_tokens": None}
    with pytest.raises(ValueError):
        AdapterResult(argv=[], run=None, cli_version=None, completed=False, final_text=None,
                      structured=None, reported_model_id=None, model_check="maybe", usage_raw=None)
    with pytest.raises(ValueError):
        AdapterResult(argv=[], run=None, cli_version=None, completed=False, final_text=None,
                      structured=None, reported_model_id=None, model_check="unverified", usage_raw=None,
                      error_type="parse_failed")
    with pytest.raises(ValueError):
        UrlEvent(url=None, host=None, state="maybe", source="x")


@pytest.mark.parametrize(
    "url,host,is_meta",
    [
        ("https://www.metaculus.com/questions/1/", "www.metaculus.com", True),
        ("https://metaculus.com", "metaculus.com", True),
        ("https://METACULUS.com./x", "metaculus.com", True),
        ("https://notmetaculus.com/", "notmetaculus.com", False),
        ("https://metaculus.com.evil.example/", "metaculus.com.evil.example", False),
        ("not a url", None, False),
        (None, None, False),
    ],
)
def test_url_host_and_metaculus_match(url: str | None, host: str | None, is_meta: bool) -> None:
    assert url_host(url) == host
    assert is_metaculus_host(url_host(url)) is is_meta


def test_model_check_from_ids() -> None:
    assert model_check_from_ids("claude-opus-5-5", None) == "unverified"
    assert model_check_from_ids("claude-opus-5-5", "claude-opus-5-5") == "verified"
    assert model_check_from_ids("haiku", "claude-haiku-5-5", {"haiku": "claude-haiku-5-5"}) == "verified"
    assert model_check_from_ids("claude-opus-5-5", "claude-haiku-5-5") == "mismatch"
