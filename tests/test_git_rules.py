"""Stateful git rules: proven against REAL temporary repos, because their verdict depends on repo state."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wys_git", ROOT / "bin" / "watch-your-step.py")
assert spec and spec.loader
wys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wys)
RULES = json.loads((ROOT / "rules" / "rules.json").read_text())["rules"]
TESTED = {"git/staged-conflict-markers", "git/worktree-remove-ignored"}
TESTED_ELSEWHERE = {"zsh/unmatched-glob"}  # proven in tests/test_unmatched_glob.py
MARKED = "a\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n"


def sh(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    (r / "ok.txt").write_text("fine\n")
    sh(r, "add", "ok.txt")
    sh(r, "commit", "-q", "-m", "init")
    return r


def fired(cmd, cwd, rule):
    return rule in {h["id"] for h in wys.evaluate("Bash", {"command": cmd}, shell="zsh", user="t", rules=RULES, cwd=str(cwd))}


def test_every_stateful_rule_has_state_based_tests():
    assert {r["id"] for r in RULES if r.get("stateful")} == TESTED | TESTED_ELSEWHERE


def test_commit_with_staged_markers_is_blocked(repo):
    (repo / "plugin.json").write_text(MARKED)
    sh(repo, "add", "plugin.json")
    assert fired('git commit -m "merge"', repo, "git/staged-conflict-markers")
    assert fired("git rebase --continue", repo, "git/staged-conflict-markers")


def test_add_then_continue_in_one_command_reads_the_file(repo):
    (repo / "plugin.json").write_text(MARKED)  # not staged yet: the add runs in the same command
    assert fired("git add plugin.json && git rebase --continue", repo, "git/staged-conflict-markers")


def test_cd_and_dash_C_are_followed(repo, tmp_path):
    (repo / "plugin.json").write_text(MARKED)
    sh(repo, "add", "plugin.json")
    assert fired(f"cd {repo} && git commit -m x", tmp_path, "git/staged-conflict-markers")
    assert fired(f"git -C {repo} commit -m x", tmp_path, "git/staged-conflict-markers")


def test_clean_commit_and_setext_heading_pass(repo):
    (repo / "README.md").write_text("Title\n=======\n\ntext\n")  # a markdown H1 underline is not a marker
    sh(repo, "add", "README.md")
    assert not fired('git commit -m "docs"', repo, "git/staged-conflict-markers")
    assert not fired("git add README.md && git rebase --continue", repo, "git/staged-conflict-markers")


def test_unresolvable_directory_gives_no_verdict(repo):
    assert not fired('cd "$SOMEWHERE" && git commit -m x', repo, "git/staged-conflict-markers")


def test_worktree_with_unique_ignored_file_warns(repo):
    (repo / ".gitignore").write_text("*.log\n.superpowers/\n__pycache__/\n")
    sh(repo, "add", ".gitignore")
    sh(repo, "commit", "-q", "-m", "ignore")
    wt = repo.parent / "wt"
    sh(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
    (wt / ".superpowers").mkdir()
    (wt / ".superpowers" / "progress.md").write_text("the only copy\n")
    assert fired(f"git worktree remove {wt}", repo, "git/worktree-remove-ignored")
    assert fired("git worktree remove --force ../wt", repo, "git/worktree-remove-ignored")


def test_worktree_with_only_caches_passes(repo):
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    sh(repo, "add", ".gitignore")
    sh(repo, "commit", "-q", "-m", "ignore")
    wt = repo.parent / "wt2"
    sh(repo, "worktree", "add", "-q", str(wt), "-b", "f2")
    (wt / "__pycache__").mkdir()
    (wt / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"\0")
    assert not fired(f"git worktree remove {wt}", repo, "git/worktree-remove-ignored")


def test_mutation_git_checks_are_what_fires(repo, monkeypatch):
    (repo / "plugin.json").write_text(MARKED)
    sh(repo, "add", "plugin.json")
    monkeypatch.setitem(wys.CHECKS, "staged_conflict_markers", lambda *a, **k: False)
    assert not fired('git commit -m "merge"', repo, "git/staged-conflict-markers")
