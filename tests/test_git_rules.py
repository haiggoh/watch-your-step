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
TESTED = {"git/staged-conflict-markers", "git/worktree-remove-ignored", "git/worktree-add-dirty", "git/stash-untracked"}
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


def test_worktree_add_from_dirty_checkout_warns_with_count(repo):
    (repo / "wip.txt").write_text("uncommitted\n")
    (repo / "ok.txt").write_text("changed\n")  # tracked file, modified, not staged
    hits = wys.evaluate("Bash", {"command": "git worktree add ../wt-a -b feature"}, shell="zsh",
                        user="t", rules=RULES, cwd=str(repo))
    h = next(h for h in hits if h["id"] == "git/worktree-add-dirty")
    assert "2 uncommitted files stay behind" in wys.render([h])


def test_worktree_add_from_clean_checkout_passes(repo):
    assert not fired("git worktree add ../wt-b -b feature", repo, "git/worktree-add-dirty")


def test_worktree_add_follows_cd_and_dash_C(repo, tmp_path):
    (repo / "wip.txt").write_text("uncommitted\n")
    assert fired(f"cd {repo} && git worktree add ../wt-c -b feature", tmp_path, "git/worktree-add-dirty")
    assert fired(f"git -C {repo} worktree add ../wt-d -b feature", tmp_path, "git/worktree-add-dirty")


def test_worktree_add_ignores_cache_noise(repo):
    (repo / ".DS_Store").write_bytes(b"\0")
    assert not fired("git worktree add ../wt-e -b feature", repo, "git/worktree-add-dirty")


def test_stash_with_untracked_warns(repo):
    (repo / "new.txt").write_text("untracked\n")
    assert fired("git stash", repo, "git/stash-untracked")
    assert fired('git stash push -m "wip"', repo, "git/stash-untracked")


def test_stash_with_untracked_but_safe_flags_pass(repo):
    (repo / "new.txt").write_text("untracked\n")
    for cmd in ("git stash push -u", "git stash -u", "git stash push --include-untracked", "git stash -a"):
        assert not fired(cmd, repo, "git/stash-untracked"), cmd


def test_stash_readonly_subcommands_pass(repo):
    (repo / "new.txt").write_text("untracked\n")
    for cmd in ("git stash list", "git stash show", "git stash pop", "git stash drop", "git stash clear"):
        assert not fired(cmd, repo, "git/stash-untracked"), cmd


def test_stash_on_clean_tree_passes(repo):
    assert not fired("git stash", repo, "git/stash-untracked")


def test_stash_ignores_cache_noise(repo):
    (repo / ".DS_Store").write_bytes(b"\0")
    assert not fired("git stash", repo, "git/stash-untracked")


def test_mutation_new_git_checks_are_what_fire(repo, monkeypatch):
    (repo / "new.txt").write_text("untracked\n")
    monkeypatch.setitem(wys.CHECKS, "worktree_add_dirty", lambda *a, **k: False)
    assert not fired("git worktree add ../wt-x -b f", repo, "git/worktree-add-dirty")
    monkeypatch.setitem(wys.CHECKS, "stash_untracked", lambda *a, **k: False)
    assert not fired("git stash", repo, "git/stash-untracked")
