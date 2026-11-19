"""tool/shell-tool-casing: the shell tool is 'Bash' (capital B); a lowercase 'bash' call
fails with 'No such tool available: bash'. Blocked at PreToolUse; primer line ships in
every session. RED first (2026-11-19: recurred on free lanes despite CLAUDE.md)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wys_casing", ROOT / "bin" / "watch-your-step.py")
assert spec and spec.loader
wys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wys)
RULES = json.loads((ROOT / "rules" / "rules.json").read_text())["rules"]


def fired(cmd, tool="Bash"):
    return "tool/shell-tool-casing" in {h["id"] for h in wys.evaluate(
        tool, {"command": cmd}, shell="zsh", user="t", rules=RULES, cwd="/tmp")}


def test_lowercase_bash_tool_name_is_blocked():
    assert fired("ls", tool="bash")


def test_capital_bash_is_clean():
    assert not fired("ls", tool="Bash")


def test_other_tools_unaffected():
    assert not fired("ls", tool="Read")


def test_rule_has_primer_and_fix():
    e = next(r for r in RULES if r["id"] == "tool/shell-tool-casing")
    assert e["primer"] and "Bash" in e["primer"]
    assert e["fix"] and "Bash" in e["fix"]
    assert e["severity"] == "block"
