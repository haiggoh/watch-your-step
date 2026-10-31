"""The suite never touches the real ~/.gitconfig: the git guard writes GLOBAL config, so every test
must see a temp file there (tests/conftest.py pins it; the conftest tripwire proves nothing leaked)."""
import os
import subprocess
from pathlib import Path


def test_global_git_config_is_a_temp_file_not_the_real_one():
    p = subprocess.run(["git", "config", "--global", "--show-origin", "--list"],
                       capture_output=True, text=True)
    real = Path("~/.gitconfig").expanduser().resolve()
    assert os.environ.get("GIT_CONFIG_GLOBAL"), "GIT_CONFIG_GLOBAL is not pinned"
    assert Path(os.environ["GIT_CONFIG_GLOBAL"]).resolve() != real
    assert str(real) not in p.stdout, "a test can read or write the real ~/.gitconfig"


def test_system_git_config_is_ignored():
    assert os.environ.get("GIT_CONFIG_NOSYSTEM") == "1"


def test_evaluate_overhead_is_well_under_half_a_second():
    """0.8.0 deferred pin: the hook must stay cheap enough to run on EVERY tool call.
    Bound 0.5 s against the measured ~20x headroom, so it fails only on a real regression."""
    import importlib.util
    import json
    import time
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("wys_perf", root / "bin" / "watch-your-step.py")
    wys = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wys)
    rules = json.loads((root / "rules" / "rules.json").read_text())["rules"]
    cmd = "git status --porcelain && ls -la"
    t0 = time.monotonic()
    for _ in range(5):
        wys.evaluate("Bash", {"command": cmd}, shell="zsh", user="t", rules=rules, cwd=str(root))
    per_call = (time.monotonic() - t0) / 5
    assert per_call < 0.5, f"evaluate took {per_call:.3f}s per call (>0.5s bound)"
