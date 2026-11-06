"""Run the isolated test suite with guards against model CLI/runtime data access."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_guard(root, scratch):
    root, scratch = root.resolve(), scratch.resolve()

    def guard(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if any(path == root / name or (root / name) in path.parents
                   for name in ("data", "reports", ".env", ".agents", ".codex")):
                raise PermissionError("offline tests cannot access project runtime data")
        if event == "subprocess.Popen":
            argv = args[1]
            parts = argv if isinstance(argv, (list, tuple)) else [argv]
            if any(Path(str(part)).name in ("agy", "codex", "claude")
                   and not Path(str(part)).resolve().is_relative_to(scratch) for part in parts[:2]):
                raise PermissionError("offline tests cannot call model CLIs")

    return guard


def main():
    # Fixed system temp parent avoids inheriting a TMPDIR inside the project.
    # Only this run's private temporary tree is removed when pytest exits.
    with tempfile.TemporaryDirectory(prefix="flab-offline-", dir="/tmp") as directory:
        scratch = Path(directory).resolve()
        if scratch.is_relative_to(ROOT):
            raise RuntimeError("offline scratch must be outside the project")
        os.environ["TMPDIR"] = str(scratch)
        tempfile.tempdir = str(scratch)
        sys.path.insert(0, str(ROOT / "src"))
        sys.path.insert(0, str(ROOT / "tests"))
        guard = make_guard(ROOT, scratch)
        active = [True]
        sys.addaudithook(lambda event, args: guard(event, args) if active[0] else None)
        import pytest

        # conftest supplies one host. The old flag no longer adds a second one.
        args = [arg for arg in sys.argv[1:] if arg != "--synthetic-host"]
        try:
            return pytest.main(["--basetemp", str(scratch / "pytest"), *args])
        finally:
            # pytest has exited. The stdlib cleanup uses dir_fd-relative opens,
            # which audit events cannot resolve against their actual parent.
            # rmtree safely unlinks symlinks within this private scratch tree.
            active[0] = False


if __name__ == "__main__":
    raise SystemExit(main())
