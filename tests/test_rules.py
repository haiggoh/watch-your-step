"""watch-your-step: every rule is proven by its own cases, and the hook speaks the PreToolUse protocol."""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "watch-your-step.py"
RULES = json.loads((ROOT / "rules" / "rules.json").read_text())["rules"]
CASES = json.loads((ROOT / "tests" / "cases.json").read_text())["cases"]

spec = importlib.util.spec_from_file_location("wys", SCRIPT)
assert spec and spec.loader
wys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wys)

USER = "tester"
NEAR_MISS = "testerr"  # one letter doubled: the shape of the recorded typo


def fill(s):
    return s.replace("OTHERUSER", NEAR_MISS).replace("HOMEUSER", USER)


def tool_input(case):
    if "tool_input" in case:              # a tool call with more than a path (Read pages, Edit)
        return {k: fill(v) if isinstance(v, str) else v for k, v in case["tool_input"].items()}
    if "file_path" in case:
        return {"file_path": fill(case["file_path"])}
    return {"command": fill(case["command"])}


def test_every_rule_has_a_flag_and_a_pass_case():
    """Stateful rules (their verdict depends on repo state) are proven in test_git_rules.py instead."""
    for rule in (r for r in RULES if not r.get("stateful")):
        kinds = {c["expect"] for c in CASES if c["rule"] == rule["id"]}
        assert kinds == {"flag", "pass"}, f"{rule['id']} needs both a flag and a pass case, has {kinds}"


def test_rules_schema():
    ids = [r["id"] for r in RULES]
    assert len(ids) == len(set(ids)), "duplicate rule ids"
    for r in RULES:
        assert r["severity"] in ("block", "warn"), r["id"]
        for key in ("tools", "shells", "context", "message", "fix", "incident"):
            assert key in r, f"{r['id']} lacks {key}"
        assert set(r["context"]) <= set(wys.CONTEXTS), r["id"]
        if "check" in r:
            assert r["check"] in wys.CHECKS, f"{r['id']}: unknown check {r['check']}"
        else:
            re.compile(r["pattern"])


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['rule']}:{c['expect']}:{str(c.get('command', c.get('file_path', c.get('tool_input'))))[:40]}")
def test_case(case):
    hits = wys.evaluate(case["tool"], tool_input(case), shell=case.get("shell", "zsh"), user=USER, rules=RULES)
    ids = {h["id"] for h in hits}
    if case["expect"] == "flag":
        assert case["rule"] in ids, f"expected {case['rule']} to fire, got {sorted(ids)}"
    else:
        assert case["rule"] not in ids, f"{case['rule']} fired on a safe command"


@pytest.mark.parametrize("rule", [r for r in RULES if not r.get("stateful")], ids=lambda r: r["id"])
def test_mutation_each_flag_case_depends_on_its_own_rule(rule):
    """Neutralise ONE rule: every flag case written for it must stop firing it, so a case can never
    pass because of the wrong mechanism (a green suite that would also be green with the rule broken)."""
    dead = dict(rule)
    dead.pop("check", None)
    dead["pattern"] = r"(?!x)x"  # matches nothing
    mutated = [dead if r["id"] == rule["id"] else r for r in RULES]
    for case in (c for c in CASES if c["rule"] == rule["id"] and c["expect"] == "flag"):
        live = {h["id"] for h in wys.evaluate(case["tool"], tool_input(case), shell=case.get("shell", "zsh"), user=USER, rules=RULES)}
        gone = {h["id"] for h in wys.evaluate(case["tool"], tool_input(case), shell=case.get("shell", "zsh"), user=USER, rules=mutated)}
        assert rule["id"] in live and rule["id"] not in gone


def run_hook(payload, *args, env_extra=None):
    env = dict(os.environ, WATCH_YOUR_STEP_SHELL="zsh", WATCH_YOUR_STEP_USER=USER)
    env.update(env_extra or {})
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=data, capture_output=True, text=True, env=env, timeout=20)


def test_hook_blocks_with_deny_and_the_fix():
    p = run_hook({"tool_name": "Bash", "tool_input": {"command": 'echo "a `b` c"'}}, "check")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    reason = out["permissionDecisionReason"]
    assert "\U0001F97E watch-your-step" in reason and "shell/backtick-in-dquote" in reason and "fix:" in reason


def test_hook_warns_without_denying():
    p = run_hook({"tool_name": "Bash", "tool_input": {"command": 'echo "costs $40"'}}, "check")
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert "permissionDecision" not in out
    assert "shell/dollar-digit-in-dquote" in out["additionalContext"]


def test_hook_is_silent_on_a_clean_command():
    p = run_hook({"tool_name": "Bash", "tool_input": {"command": "ls -la ~/x"}}, "check")
    assert p.returncode == 0 and p.stdout == ""


def test_hook_fails_open_on_malformed_input():
    p = run_hook("{not json", "check")
    assert p.returncode == 0 and p.stdout == ""


def test_disable_env_skips_a_rule():
    p = run_hook({"tool_name": "Bash", "tool_input": {"command": 'echo "a `b` c"'}}, "check",
                 env_extra={"WATCH_YOUR_STEP_DISABLE": "shell/backtick-in-dquote"})
    assert p.stdout == ""


def test_help_and_unknown_flag():
    h = run_hook("", "--help")
    assert h.returncode == 0 and "check" in h.stdout and "WATCH_YOUR_STEP_DISABLE" in h.stdout
    bad = run_hook("", "--bogus")
    assert bad.returncode == 2 and "usage" in bad.stderr.lower()


def test_a_check_may_return_a_detail_string(monkeypatch):
    """A builtin check may return a non-empty string instead of True: the string is appended
    to the rendered message, so a stateful rule can say how big the danger is."""
    monkeypatch.setitem(wys.CHECKS, "detail_probe", lambda texts, **k: "3 files stay behind")
    rule = {"id": "t/detail", "severity": "warn", "tools": ["Bash"], "shells": [],
            "context": ["raw"], "check": "detail_probe", "message": "base message.",
            "fix": "f", "incident": "i"}
    hits = wys.evaluate("Bash", {"command": "anything"}, shell="zsh", user=USER, rules=[rule])
    assert "base message. 3 files stay behind" in wys.render(hits)


def test_test_mode_surfaces_a_raising_check(monkeypatch, capsys):
    """Hook mode fails open, but `test` is the probe people trust: a check that RAISES must not read
    as 'clean' there (cross-family review D1, confirmed 2026-10-29: it printed clean)."""
    def boom(*a, **k):
        raise RuntimeError("planted")
    monkeypatch.setitem(wys.CHECKS, "truncate_before_read", boom)
    rc = wys.main(["test", "sort f > f"])
    err = capsys.readouterr().err
    assert rc != 0
    assert "planted" in err and "shell/truncate-before-read" in err


def test_hook_mode_still_fails_open_on_a_raising_check(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("planted")
    monkeypatch.setitem(wys.CHECKS, "truncate_before_read", boom)
    assert wys.evaluate("Bash", {"command": "sort f > f"}, shell="zsh", user=USER, rules=RULES) == []


def test_split_command_literal_view_keeps_simple_quoted_words():
    view, parts = wys.split_command("git branch -D \"feat/x\" 'y' \"a b\" \"$v\" # wys:allow r/x")
    assert parts["lit"] == ["git branch -D feat/x y Q Q "]
    assert parts["comment"] == ["# wys:allow r/x"]
    assert "feat/x" not in view          # the bare view is unchanged: quoted spans stay Q


def test_non_bash_checks_receive_the_whole_tool_input(monkeypatch):
    seen = {}
    monkeypatch.setitem(wys.CHECKS, "detail_probe", lambda texts, **k: seen.update(k) or False)
    rule = {"id": "t/probe", "severity": "warn", "tools": ["Read"], "shells": [],
            "context": ["raw"], "check": "detail_probe", "message": "m", "fix": "f", "incident": "i"}
    wys.evaluate("Read", {"file_path": "/x/a.tsx", "pages": "2"}, shell="zsh", user=USER, rules=[rule])
    assert seen.get("tool_input") == {"file_path": "/x/a.tsx", "pages": "2"}


def test_an_empty_detail_string_is_no_hit(monkeypatch):
    monkeypatch.setitem(wys.CHECKS, "detail_probe", lambda texts, **k: "")
    rule = {"id": "t/detail", "severity": "warn", "tools": ["Bash"], "shells": [],
            "context": ["raw"], "check": "detail_probe", "message": "base message.",
            "fix": "f", "incident": "i"}
    assert wys.evaluate("Bash", {"command": "anything"}, shell="zsh", user=USER, rules=[rule]) == []
