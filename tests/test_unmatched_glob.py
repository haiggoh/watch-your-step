"""zsh/unmatched-glob: the verdict depends on the files that exist where the command runs."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wys_glob", ROOT / "bin" / "watch-your-step.py")
assert spec and spec.loader
wys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wys)
RULES = json.loads((ROOT / "rules" / "rules.json").read_text())["rules"]


def fired(cmd, cwd):
    return "zsh/unmatched-glob" in {h["id"] for h in wys.evaluate(
        "Bash", {"command": cmd}, shell="zsh", user="t", rules=RULES, cwd=str(cwd))}


def test_absent_glob_warns(tmp_path):
    (tmp_path / "a.log").write_text("x")
    assert fired("cat *.md", tmp_path)


def test_matching_glob_stays_quiet(tmp_path):
    (tmp_path / "b.md").write_text("x")
    assert not fired("cat *.md", tmp_path)


def test_a_matching_stage_keeps_a_compound_command_quiet(tmp_path):
    (tmp_path / "c.md").write_text("x")
    assert not fired("ls; cat *.md", tmp_path)


def test_nullglob_qualifier_is_respected(tmp_path):
    (tmp_path / "a.log").write_text("x")
    assert not fired("rm *.md(N)", tmp_path)


def test_setopt_null_glob_disables_the_check(tmp_path):
    assert not fired("setopt NULL_GLOB; ls *.zzz-absent", tmp_path)


def test_quoted_pattern_never_fires(tmp_path):
    assert not fired("find . -name '*.md' -maxdepth 1", tmp_path)


def test_variable_glob_is_unknowable_and_skipped(tmp_path):
    assert not fired("ls $dir/*.md", tmp_path)


def test_brace_word_is_skipped(tmp_path):
    assert not fired("echo a{b,c}*.md", tmp_path)


def test_double_bracket_pattern_is_not_a_filename_glob(tmp_path):
    assert not fired("[[ $x == *.md ]] && echo ok", tmp_path)


def test_case_pattern_is_not_a_filename_glob(tmp_path):
    # shape taken from a real replay hit: a pgrep/case orphan-killer
    assert not fired('case "$args" in *"/var/folders/"*) echo hit;; *) echo no;; esac', tmp_path)


def test_cd_before_the_glob_is_followed(tmp_path):
    # shape taken from a real replay hit: cd X && for f in $(ls -t *.jsonl ...)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "e.md").write_text("x")
    assert not fired("cd %s && cat *.md" % sub, tmp_path)
    assert fired("cd %s && cat *.zzz-absent" % sub, tmp_path)


def test_unresolvable_cd_leaves_no_verdict(tmp_path):
    assert not fired("cd '$missing'/*.md || true; echo ok", tmp_path)


def test_absolute_glob_checked_in_its_own_tree(tmp_path):
    assert fired("ls %s/*.zzz-absent" % tmp_path, tmp_path)
    (tmp_path / "d.zzz-absent").write_text("x")
    assert not fired("ls %s/*.zzz-absent" % tmp_path, tmp_path)


def test_escaped_star_is_literal_and_skipped(tmp_path):
    assert not fired("ls \\*.md", tmp_path)


def test_bash_shell_never_trips_the_zsh_rule(tmp_path):
    hits = wys.evaluate("Bash", {"command": "ls *.zzz-absent"}, shell="bash", user="t",
                        rules=RULES, cwd=str(tmp_path))
    assert "zsh/unmatched-glob" not in {h["id"] for h in hits}
