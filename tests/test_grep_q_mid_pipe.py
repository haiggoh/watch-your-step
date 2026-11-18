"""shell/grep-q-mid-pipe: grep -q anywhere except the LAST stage of a pipeline always yields empty
output, so the predicate is always false (incident 2026-11-16: `grep -lq pat files | grep -v x`
made a sudo-in-terminal --verify time out as NOT_VERIFIED while the launch had succeeded)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wys_gq", ROOT / "bin" / "watch-your-step.py")
assert spec and spec.loader
wys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wys)
RULES = json.loads((ROOT / "rules" / "rules.json").read_text())["rules"]


def fired(cmd):
    return "shell/grep-q-mid-pipe" in {h["id"] for h in wys.evaluate(
        "Bash", {"command": cmd}, shell="zsh", user="t", rules=RULES, cwd="/tmp")}


def test_grep_q_followed_by_a_pipe_fires():
    assert fired("grep -lq pat files | grep -v x")


def test_combined_flag_fires():
    assert fired("grep -qw foo input | wc -l")


def test_long_flag_fires():
    assert fired("grep --quiet foo input | tail")


def test_grep_q_as_last_stage_stays_quiet():
    assert not fired("ls | grep -q pat")
    assert not fired("cmd | xargs grep -l pat | grep -v x | grep -q .")


def test_grep_q_alone_stays_quiet():
    assert not fired("grep -q pat file")
    assert not fired("if grep -q pat file; then echo ok; fi")


def test_grep_without_q_followed_by_pipe_stays_quiet():
    assert not fired("grep -v q input | tail")
    assert not fired("grep pat input | wc -l")


def test_semicolon_separated_command_after_pipe_is_a_new_command():
    # the pipe belongs to the second command; the first grep -q has no following pipe on ITS line
    assert not fired("grep -q a f; echo b | wc -l")
