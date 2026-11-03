"""Run synthetic tests in the supplied work copy without model CLI/runtime data access."""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
scratch = ROOT / ".d121-checks" / "tmp"
scratch.mkdir(parents=True, exist_ok=True)
os.environ["TMPDIR"] = str(scratch)
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


def guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).absolute()
        if any(path == ROOT / name or (ROOT / name) in path.parents
               for name in ("data", "reports", ".env", ".agents", ".codex")):
            raise PermissionError("offline tests cannot access project runtime data")
    if event == "subprocess.Popen":
        argv = args[1]
        parts = argv if isinstance(argv, (list, tuple)) else [argv]
        if any(Path(str(part)).name in ("agy", "codex", "claude")
               and not Path(str(part)).resolve().is_relative_to(scratch) for part in parts[:2]):
            raise PermissionError("offline tests cannot call model CLIs")


sys.addaudithook(guard)

if __name__ == "__main__":
    import pytest
    args = sys.argv[1:]
    plugins = []
    if "--synthetic-host" in args:
        args.remove("--synthetic-host")
        from offline_workcopy import SyntheticHost
        plugins.append(SyntheticHost())
    raise SystemExit(pytest.main(["--basetemp", str(scratch / f"pytest-{time.time_ns()}"),
                                 *args], plugins=plugins))
