"""watch-your-step: the SessionStart primer — every rule carries a one-line reminder, within budget.

The primer is PREVENTION: it tells the model each trap once at session start, so the PreToolUse
guard stays a safety net that rarely fires. This file is the barrier — a rule added without a
primer fails the suite, and so does a primer over budget."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "watch-your-step.py"
HOOKS = ROOT / "hooks" / "hooks.json"
RULES = json.loads((ROOT / "rules" / "rules.json").read_text())["rules"]

spec = importlib.util.spec_from_file_location("wys", SCRIPT)
assert spec and spec.loader
wys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wys)


def run_cli(args=(), env_extra=None):
    env = dict(os.environ, WATCH_YOUR_STEP_SHELL="zsh")
    env.pop("WATCH_YOUR_STEP_DISABLE", None)
    env.pop("WATCH_YOUR_STEP_RULES", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=20)

PRIMER_MAX_LINE = 110    # chars per rule's primer
PRIMER_MAX_TOTAL = 2400  # chars for all primers together (the injected block's size budget)


def test_every_rule_has_a_primer_within_budget():
    total = 0
    for rule in RULES:
        primer = rule.get("primer")
        assert isinstance(primer, str) and primer.strip(), f"{rule['id']} has no primer"
        assert len(primer) <= PRIMER_MAX_LINE, \
            f"{rule['id']}: primer is {len(primer)} chars (max {PRIMER_MAX_LINE}): {primer!r}"
        total += len(primer)
    assert total <= PRIMER_MAX_TOTAL, f"primers total {total} chars (budget {PRIMER_MAX_TOTAL})"


def test_primer_lists_every_enabled_rule_for_the_shell():
    text = wys.build_primer(RULES, shell="zsh", disabled=set())
    assert text
    for rule in RULES:  # zsh excludes no rule (shells == [] means all shells)
        assert rule["primer"] in text, rule["id"]
    assert "zsh/" in text and "git/" in text  # grouped by prefix


def test_primer_cli_emits_session_start_json():
    p = run_cli(["primer"])
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    for rule in RULES:
        assert rule["primer"] in out["additionalContext"]


def test_disable_all_prints_nothing():
    assert wys.build_primer(RULES, shell="zsh", disabled="all") == ""
    p = run_cli(["primer"], env_extra={"WATCH_YOUR_STEP_DISABLE": "all"})
    assert p.returncode == 0 and p.stdout == ""


def test_disabled_ids_are_omitted():
    disabled = {"git/add-all", "shell/backtick-in-dquote"}
    text = wys.build_primer(RULES, shell="zsh", disabled=disabled)
    for rid in disabled:
        primer = next(r["primer"] for r in RULES if r["id"] == rid)
        assert primer not in text, rid
    assert len([ln for ln in text.splitlines() if ln.startswith("  - ")]) == len(RULES) - len(disabled)
    p = run_cli(["primer"], env_extra={"WATCH_YOUR_STEP_DISABLE": "git/add-all,shell/backtick-in-dquote"})
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    primer = next(r["primer"] for r in RULES if r["id"] == "git/add-all")
    assert primer not in ctx


def test_bash_shell_omits_zsh_only_rules():
    text = wys.build_primer(RULES, shell="bash", disabled=set())
    zsh_only = [r for r in RULES if r["shells"] == ["zsh"]]
    assert len(zsh_only) == 5, "expected the 5 zsh-only rules"
    for r in zsh_only:
        assert r["primer"] not in text, r["id"]
    for r in RULES:
        if not r["shells"]:
            assert r["primer"] in text, r["id"]
    p = run_cli(["primer"], env_extra={"WATCH_YOUR_STEP_SHELL": "bash"})
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert zsh_only[0]["primer"] not in ctx


def test_corrupt_rules_file_prints_nothing_exit_0(tmp_path):
    bad = tmp_path / "rules.json"
    bad.write_text("{not json")
    p = run_cli(["primer"], env_extra={"WATCH_YOUR_STEP_RULES": str(bad)})
    assert p.returncode == 0 and p.stdout == ""


def test_primer_help_runs_without_output_json():
    for args in (["--help"], ["primer", "--help"]):
        p = run_cli(args)
        assert p.returncode == 0 and "hookSpecificOutput" not in p.stdout
    bad = run_cli(["primer", "--bogus"])
    assert bad.returncode == 2 and "usage" in bad.stderr.lower()


def test_hooks_json_registers_primer_on_session_start():
    hooks = json.loads(HOOKS.read_text())["hooks"]
    primer_entries = [h for e in hooks["SessionStart"] for h in e["hooks"]
                      if "watch-your-step.py" in h["command"] and " primer" in h["command"]]
    assert len(primer_entries) == 1
    assert primer_entries[0].get("timeout") == 5
