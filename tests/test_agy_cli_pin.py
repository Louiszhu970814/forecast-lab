"""Project-only agy pin: version identity, exact arguments, and fail-closed bytes."""
import hashlib
import os
from pathlib import Path
import subprocess


def test_agy_pin_identity_and_dispatch(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "launchd/bin/agy").read_text()
    wrapper = tmp_path / "launchd/bin/agy"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(source)
    wrapper.chmod(0o755)
    pin = tmp_path / "data/cli_pins/agy/1.4.12"
    pin.parent.mkdir(parents=True)

    def run(*args):
        return subprocess.run([str(wrapper), *args], capture_output=True, text=True,
                              env={**os.environ, "AGY_CLI_DISABLE_AUTO_UPDATE": "false"})

    assert run().returncode == 69
    pin.write_text('#!/bin/sh\nprintf "%s\\n" "$AGY_CLI_DISABLE_AUTO_UPDATE" "$@"\n')
    pin.chmod(0o700)
    wrapper.write_text(source.replace(
        "34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3",
        hashlib.sha256(pin.read_bytes()).hexdigest()))
    result = run("--flag", "space and $literal")
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["true", "--flag", "space and $literal"]
    assert run("changelog").stdout.splitlines() == ["true", "--version"]
    assert run("changelog", "--help").stdout.splitlines() == ["true", "changelog", "--help"]
    pin.chmod(0o600)
    assert run().returncode == 69
    pin.chmod(0o700)
    pin.write_text(pin.read_text() + "# changed\n")
    assert run().returncode == 69
    pin.unlink()
    target = tmp_path / "target"
    target.write_text("fake")
    pin.symlink_to(target)
    assert run().returncode == 69
