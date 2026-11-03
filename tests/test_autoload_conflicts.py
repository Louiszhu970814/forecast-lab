"""Restoration must not destroy an external edit made while canaries are present."""
import json
from pathlib import Path

import pytest

import autoload as A
from flab.ids import sha256_hex


def placed(tmp_path, existing=True):
    target = tmp_path / 'source' / 'AGENTS.md'
    target.parent.mkdir()
    if existing:
        target.write_bytes(b'original\r\n')
        target.chmod(0o640)
    placement = A.Placement([A.Location('gpt', target, 'instructions', 'FLABAUTO-test-01')],
                            tmp_path / 'backup')
    placement.prepare()
    placement.place()
    return placement, target


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('edit', ['replace', 'append', 'chmod'])
def test_external_edit_is_preserved_and_reported(tmp_path, existing, edit):
    placement, target = placed(tmp_path, existing)
    if edit == 'replace':
        target.write_bytes(b'external replacement')
    elif edit == 'append':
        with target.open('ab') as stream:
            stream.write(b'\nexternal addition')
    else:
        target.chmod(0o600 if existing else 0o640)
    expected = target.read_bytes(), target.stat().st_mode & 0o777
    failures = placement.restore()
    assert failures and failures[0]['path'] == str(target)
    assert (target.read_bytes(), target.stat().st_mode & 0o777) == expected
    if existing:
        assert Path(failures[0]['backup']).read_bytes() == b'original\r\n'


def test_external_deletion_is_not_recreated(tmp_path):
    placement, target = placed(tmp_path)
    target.unlink()
    assert placement.restore()
    assert not target.exists()


@pytest.mark.parametrize('existing', [False, True])
def test_unchanged_canary_restores_idempotently(tmp_path, existing):
    placement, target = placed(tmp_path, existing)
    assert placement.restore() == []
    assert placement.restore() == []
    assert target.exists() is existing
    if existing:
        assert target.read_bytes() == b'original\r\n'
        assert target.stat().st_mode & 0o777 == 0o640


def test_manifest_has_expected_content_before_restore(tmp_path):
    placement, target = placed(tmp_path)
    events = [json.loads(line) for line in (placement.backup_dir/'manifest.jsonl').read_text().splitlines()]
    event = next(e for e in events if e['type'] == 'place')
    assert event['placed_sha256'] == sha256_hex(target.read_bytes())
    assert event['placed_mode'] == 0o640
    assert placement.restore() == []
