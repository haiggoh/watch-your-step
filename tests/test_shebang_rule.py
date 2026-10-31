"""Stateful rule shell/python-shebang-mismatch: `python3 <file>` whose first line names
another interpreter. Proven against REAL temp files — the verdict needs a file read, so a
pure pattern cannot express it. Fail-open cases (missing/unreadable file, flags) must give
no verdict, never a guess."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wys_shebang", ROOT / "bin" / "watch-your-step.py")
assert spec and spec.loader
wys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wys)
RULES = json.loads((ROOT / "rules" / "rules.json").read_text())["rules"]
RULE = "shell/python-shebang-mismatch"


def fired(tmp_path, cmd):
    return RULE in {h["id"] for h in wys.evaluate("Bash", {"command": cmd}, shell="zsh",
                                                  user="t", rules=RULES, cwd=str(tmp_path))}


def write(tmp_path, name, first_line):
    p = tmp_path / name
    p.write_text(first_line + "\nprint('hi')\n")
    return p


# ---- flag cases (the measured incident: a .py shim with #!/bin/sh gave a misleading
# ---- SyntaxError under python3) ----

def test_flags_sh_shim_run_under_python3(tmp_path):
    write(tmp_path, "shim.py", "#!/bin/sh")
    assert fired(tmp_path, "python3 shim.py")


def test_flags_plain_python_with_ruby_shebang(tmp_path):
    write(tmp_path, "tool.rb", "#!/usr/bin/env ruby")
    assert fired(tmp_path, "python tool.rb")


def test_flags_absolute_path(tmp_path):
    p = write(tmp_path, "tool.sh", "#!/bin/bash")
    assert fired(tmp_path, f"python3 {p}")


def test_flags_in_a_chain_and_behind_env_assignment(tmp_path):
    write(tmp_path, "shim.py", "#!/bin/sh")
    assert fired(tmp_path, "echo ok && python3 shim.py")
    assert fired(tmp_path, "FOO=1 python3 shim.py")


def test_flags_extra_args_after_script(tmp_path):
    write(tmp_path, "shim.py", "#!/bin/sh")
    assert fired(tmp_path, "python3 shim.py --flag x")


# ---- pass cases ----

def test_python_shebang_does_not_fire(tmp_path):
    write(tmp_path, "ok.py", "#!/usr/bin/env python3")
    assert not fired(tmp_path, "python3 ok.py")


def test_no_shebang_does_not_fire(tmp_path):
    write(tmp_path, "plain.py", "print('hi')")
    assert not fired(tmp_path, "python3 plain.py")


def test_missing_file_fails_open(tmp_path):
    assert not fired(tmp_path, "python3 nope.py")


def test_dash_c_and_dash_m_do_not_fire(tmp_path):
    write(tmp_path, "shim.py", "#!/bin/sh")
    assert not fired(tmp_path, 'python3 -c "print(1)"')
    assert not fired(tmp_path, "python3 -m json.tool shim.py")


def test_non_python_runner_does_not_fire(tmp_path):
    write(tmp_path, "shim.py", "#!/bin/sh")
    assert not fired(tmp_path, "bash shim.py")


def test_rule_is_block_severity_and_registered():
    rule = next(r for r in RULES if r["id"] == RULE)
    assert rule["severity"] == "block"
    assert rule.get("stateful") is True
    assert rule["check"] in wys.CHECKS
