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


# ---- a glob in a LATER statement is judged when it runs, not when the hook looks ----
# Real zsh (measured 2026-11-05): `echo A; ls nomatch*; echo C` runs A and C and only the
# glob's own command fails, while `echo A | ls nomatch*` kills the whole pipeline. So an
# earlier statement runs BEFORE a later glob is expanded and may create the files it needs.
# False positives 2026-11-05 (both warned, neither command was affected):
#   python3 digest.py --outdir D; grep -c x D/batch-*.txt     (a script writes the files)
#   for i in 1 2 3; do ... > "task-$i.json"; done; ls task-*.json   (a loop writes them)

def test_glob_over_files_a_script_creates_earlier_in_the_line_is_not_flagged(tmp_path):
    out = tmp_path / "out"
    assert not fired("python3 digest.py --outdir %s; grep -c x %s/batch-*.txt" % (out, out), tmp_path)


def test_glob_over_files_a_loop_creates_earlier_in_the_line_is_not_flagged(tmp_path):
    assert not fired('for i in 1 2 3; do echo x > "task-$i.json"; done; ls task-*.json', tmp_path)


def test_glob_over_files_a_redirect_creates_earlier_in_the_line_is_not_flagged(tmp_path):
    assert not fired("echo x > made.md && cat *.md", tmp_path)


def test_glob_over_files_mkdir_touch_create_earlier_in_the_line_is_not_flagged(tmp_path):
    assert not fired("mkdir out && touch out/a.md; cat out/*.md", tmp_path)


def test_read_only_earlier_statements_still_let_a_later_absent_glob_warn(tmp_path):
    # echo/ls/cd create nothing, so the hook's view of the filesystem is still the truth
    assert fired("echo hi; ls *.zzz-absent", tmp_path)
    assert fired("ls; pwd && cat *.zzz-absent", tmp_path)


def test_first_statement_glob_warns_even_when_a_writer_follows_it(tmp_path):
    # the glob is expanded BEFORE the later writer runs, so the later writer cannot rescue it
    assert fired("ls *.zzz-absent; touch made.zzz-absent", tmp_path)


def test_pipeline_stage_glob_warns_even_after_an_earlier_pipe_stage(tmp_path):
    # pipeline stages start together: an earlier stage in the SAME pipeline creates nothing in time
    assert fired("echo hi | cat *.zzz-absent", tmp_path)


def test_unknown_earlier_command_gives_no_verdict(tmp_path):
    # fail open: a command the rule cannot classify might write anything
    assert not fired("some-unknown-tool --go; cat *.zzz-absent", tmp_path)


def test_find_with_exec_counts_as_a_writer_but_plain_find_does_not(tmp_path):
    assert not fired("find . -name x -exec touch {}.md \\; ; cat *.md", tmp_path)
    assert fired("find . -name x; cat *.zzz-absent", tmp_path)
