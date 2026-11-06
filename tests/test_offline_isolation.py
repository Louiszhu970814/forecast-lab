"""Regression coverage for D136's host boundary and guarded test entrypoint."""
import subprocess
import sys
from pathlib import Path

import pytest

from run_offline import make_guard


def test_host_does_not_populate_test_data_directory(tmp_path):
    import flab.workdir as workdir
    import question_types

    assert list(tmp_path.iterdir()) == []
    assert not workdir.PROJECT_ROOT.is_relative_to(tmp_path)
    assert not question_types.CONFIG_PATH.is_relative_to(tmp_path)


@pytest.mark.parametrize("name", ["data", "reports", ".env", ".agents", ".codex"])
def test_guard_denies_normalized_runtime_paths(tmp_path, name):
    project = tmp_path / "project"
    project.mkdir()
    guard = make_guard(project, tmp_path / "scratch")
    with pytest.raises(PermissionError):
        guard("open", (str(project / "tests" / ".." / name), "r", 0))


def test_guard_denies_symlink_to_runtime_data(tmp_path):
    project = tmp_path / "project"
    (project / "data").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(project / "data", target_is_directory=True)
    with pytest.raises(PermissionError):
        make_guard(project, tmp_path / "scratch")("open", (alias / "canary", "r", 0))


@pytest.mark.parametrize("name", ["claude", "codex", "agy"])
def test_guard_allows_only_scratch_model_stubs(tmp_path, name):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    guard = make_guard(tmp_path / "project", scratch)
    with pytest.raises(PermissionError):
        guard("subprocess.Popen", (name, [f"/usr/local/bin/{name}"], None, None))
    guard("subprocess.Popen", (name, [str(scratch / name)], None, None))


def test_audit_hook_blocks_real_synthetic_file_read_in_child(tmp_path):
    project = tmp_path / "project"
    (project / "data").mkdir(parents=True)
    (project / "data/canary").write_text("synthetic, not a credential")
    code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from run_offline import make_guard
project = Path(sys.argv[2])
sys.addaudithook(make_guard(project, project.parent / 'scratch'))
try:
    (project / 'data/canary').read_text()
except PermissionError:
    print('blocked')
else:
    raise SystemExit('guard did not block')
"""
    result = subprocess.run([sys.executable, "-c", code, str(Path(__file__).parent), str(project)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"
