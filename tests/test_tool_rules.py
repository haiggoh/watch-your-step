"""Stateful tool rules: their verdict depends on the target FILE, so they are proven on real temp files."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wys_tool", ROOT / "bin" / "watch-your-step.py")
assert spec and spec.loader
wys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wys)
RULES = json.loads((ROOT / "rules" / "rules.json").read_text())["rules"]
MID_INDENT_RULE = "tool/edit-anchor-mid-indent"

# The 2026-10-28 incident shape: an 8-space anchor inside a 12-space line moved a block out of its if.
SOURCE = "def f(x):\n    if x:\n        a = 1\n            b = 2\n    return a\n"


def edit_fires(tool, tool_input):
    return MID_INDENT_RULE in {h["id"] for h in wys.evaluate(tool, tool_input, shell="zsh", user="t", rules=RULES)}


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(SOURCE)
    return p


def test_anchor_matching_inside_deeper_indentation_blocks(src):
    assert edit_fires("Edit", {"file_path": str(src), "old_string": "        b = 2", "new_string": "        b = 3"})


def test_anchor_at_line_start_passes(src):
    assert not edit_fires("Edit", {"file_path": str(src), "old_string": "        a = 1", "new_string": "        a = 2"})


def test_substring_after_code_on_the_line_passes(src):
    """Replacing ' 1' inside 'a = 1' is an ordinary mid-line edit, not an indentation anchor."""
    assert not edit_fires("Edit", {"file_path": str(src), "old_string": " 1\n", "new_string": " 7\n"})


def test_anchor_starting_with_a_newline_passes(src):
    assert not edit_fires("Edit", {"file_path": str(src), "old_string": "\n    return a", "new_string": "\n    return 0"})


def test_missing_file_gives_no_verdict(tmp_path):
    assert not edit_fires("Edit", {"file_path": str(tmp_path / "nope.py"), "old_string": "    x", "new_string": "    y"})


def test_multiedit_with_one_mid_indent_entry_blocks(src):
    edits = [{"old_string": "def f(x):", "new_string": "def f(y):"},
             {"old_string": "        b = 2", "new_string": "        b = 3"}]
    assert edit_fires("MultiEdit", {"file_path": str(src), "edits": edits})


def test_the_rule_is_a_stateful_block_rule():
    r = next(r for r in RULES if r["id"] == MID_INDENT_RULE)
    assert r["severity"] == "block" and r.get("stateful") is True
    assert set(r["tools"]) == {"Edit", "MultiEdit"}
