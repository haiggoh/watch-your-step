"""Stateful rule python/pytest-n-without-xdist: `<python> -m pytest … -n N` where THAT
interpreter cannot import xdist. pytest then exits 4 (usage error: unrecognized -n) before
collecting a single test, and exit 4 read off a pipeline looks like a test verdict.

The verdict needs a live probe of the named interpreter, so every case here runs against a
STUB interpreter written into a temp dir (exit 1 = no xdist, exit 0 = has xdist). That keeps
the suite independent of whichever Python the host machine happens to have — the very trap
the rule is about. Fail-open cases (unresolvable interpreter) must give no verdict."""
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wys_xdist", ROOT / "bin" / "watch-your-step.py")
assert spec and spec.loader
wys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wys)
RULES = json.loads((ROOT / "rules" / "rules.json").read_text())["rules"]
RULE = "python/pytest-n-without-xdist"


def stub(tmp_path, has_xdist, name="python3"):
    """A fake interpreter: answers `-c "import xdist"` with 0 or 1, nothing else."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\nexit %d\n" % (0 if has_xdist else 1))
    p.chmod(0o755)
    return p


def fired(tmp_path, cmd):
    hits = wys.evaluate("Bash", {"command": cmd}, shell="zsh", user="t", rules=RULES,
                        cwd=str(tmp_path))
    return RULE in {h["id"] for h in hits}


def blocked(tmp_path, cmd):
    hits = wys.evaluate("Bash", {"command": cmd}, shell="zsh", user="t", rules=RULES,
                        cwd=str(tmp_path))
    return any(h["id"] == RULE and h["severity"] == "block" for h in hits)


# ---- flag cases: the measured incident (2026-11-25, twice in one day) ----

def test_blocks_python3_m_pytest_n_auto_without_xdist(tmp_path):
    py = stub(tmp_path, has_xdist=False)
    assert blocked(tmp_path, f"{py} -m pytest -n auto tests")


def test_flags_every_spelling_of_the_n_flag(tmp_path):
    py = stub(tmp_path, has_xdist=False)
    for flag in ("-n 4", "-n4", "-nauto", "-n=4", "--numprocesses 4", "--numprocesses=auto"):
        assert fired(tmp_path, f"{py} -m pytest -q {flag} tests"), flag


def test_flags_with_interpreter_flags_and_env_prefix(tmp_path):
    py = stub(tmp_path, has_xdist=False)
    assert fired(tmp_path, f"PYTHONDONTWRITEBYTECODE=1 {py} -B -m pytest -n 2")


def test_flags_a_later_chain_link(tmp_path):
    py = stub(tmp_path, has_xdist=False)
    assert fired(tmp_path, f"cd {tmp_path} && {py} -m pytest -n 2 > /tmp/x.log 2>&1")


def test_resolves_a_bare_interpreter_name_through_path(tmp_path, monkeypatch):
    stub(tmp_path, has_xdist=False)
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ.get("PATH", ""))
    assert fired(tmp_path, "python3 -m pytest -n auto")


# ---- pass cases ----

def test_quiet_when_the_interpreter_has_xdist(tmp_path):
    py = stub(tmp_path, has_xdist=True)
    assert not fired(tmp_path, f"{py} -m pytest -n auto tests")


def test_quiet_without_an_n_flag(tmp_path):
    py = stub(tmp_path, has_xdist=False)
    assert not fired(tmp_path, f"{py} -m pytest -q tests")
    assert not fired(tmp_path, f"{py} -m pytest --no-header tests")


def test_quiet_for_another_module(tmp_path):
    py = stub(tmp_path, has_xdist=False)
    assert not fired(tmp_path, f"{py} -m pip install -n foo")


def test_fails_open_when_the_interpreter_cannot_be_resolved(tmp_path):
    assert not fired(tmp_path, f"{tmp_path}/nope/python3 -m pytest -n auto")
