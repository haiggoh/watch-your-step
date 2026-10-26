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


def git_ignored_paths(wt):
    """What `git status --porcelain --ignored` reports there, minus the '!! ' marker."""
    p = subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=str(wt),
                       capture_output=True, text=True)
    return [ln[3:] for ln in p.stdout.splitlines() if ln.startswith("!! ")]


def git_status_paths(wt, untracked_all=False):
    """Every path git reports there — '!! ' ignored AND '?? ' untracked — marker-stripped.

    With untracked_all=False (the default, and what the guard uses) git collapses untracked
    directories to 'sub/'; that collapsed form is the interesting one for the guard, because the
    name alone cannot tell junk from a ledger.
    """
    cmd = ["git", "status", "--porcelain", "--ignored"]
    if untracked_all:
        cmd.append("--untracked-files=all")
    p = subprocess.run(cmd, cwd=str(wt), capture_output=True, text=True)
    return [ln[3:] for ln in p.stdout.splitlines() if ln.startswith(("!! ", "?? "))]


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


def test_a_directory_of_macos_noise_does_not_block(repo):
    """git collapses 'sub/' when sub/ holds only a .DS_Store, so the filename is invisible to a
    suffix/substring filter. A block rule that fires here stops legitimate work — measured: this
    case DID deny before the fix."""
    (repo / ".gitignore").write_text(".DS_Store\n")
    sh(repo, "add", ".gitignore")
    sh(repo, "commit", "-q", "-m", "ignore")
    wt = repo.parent / "wt-noise"
    sh(repo, "worktree", "add", "-q", str(wt), "-b", "n1")
    (wt / "sub").mkdir()
    (wt / "sub" / ".DS_Store").write_bytes(b"finder\n")
    out = git_ignored_paths(wt)
    assert any(p.rstrip("/").endswith("sub") for p in out), out  # the collapse actually happened
    assert not fired(f"git worktree remove {wt} --force", repo, "git/worktree-remove-ignored")


def test_a_real_file_beside_the_noise_still_blocks(repo):
    """The complement: 'sub/' containing a .DS_Store AND a real file is not cache noise."""
    (repo / ".gitignore").write_text(".DS_Store\n.superpowers/\n")
    sh(repo, "add", ".gitignore")
    sh(repo, "commit", "-q", "-m", "ignore")
    wt = repo.parent / "wt-mixed"
    sh(repo, "worktree", "add", "-q", str(wt), "-b", "n2")
    (wt / "sub").mkdir()
    (wt / "sub" / ".DS_Store").write_bytes(b"finder\n")
    (wt / "sub" / "the-only-copy.md").write_text("a plan ledger\n")
    assert fired(f"git worktree remove {wt} --force", repo, "git/worktree-remove-ignored")


def test_an_untracked_dir_with_a_real_file_blocks(repo):
    """A real file inside an untracked dir must block, and the COLLAPSED form is the case to pin.

    Measured: `--untracked-files=all` names '?? sub/the-only-copy.md' — an ordinary file that
    blocks trivially. The fragile route is WITHOUT it, where git collapses to '?? sub/' and only
    the directory INSPECTION can tell junk from a ledger. This asserts the collapsed form is what
    git reports (so the test is exercising that route, not the easy one), then that the rule fires.
    """
    (repo / ".gitignore").write_text(".DS_Store\n")
    sh(repo, "add", ".gitignore")
    sh(repo, "commit", "-q", "-m", "ignore")
    wt = repo.parent / "wt-untracked"
    sh(repo, "worktree", "add", "-q", str(wt), "-b", "n3")
    (wt / "sub").mkdir()
    (wt / "sub" / ".DS_Store").write_bytes(b"finder\n")
    (wt / "sub" / "the-only-copy.md").write_text("a plan ledger\n")
    paths, collapsed = git_status_paths(wt), None
    for p in paths:
        if p.rstrip("/").endswith("sub"):
            collapsed = p
    assert collapsed == "sub/", (
        f"git must collapse the dir for this test to pin the dir-inspection route: {paths}")
    assert collapsed in [p for p in paths if p == "sub/"]
    assert fired(f"git worktree remove {wt} --force", repo, "git/worktree-remove-ignored")


def test_the_collapsed_dir_route_is_what_carries_it(repo, monkeypatch):
    """Dropping --untracked-files=all must still block, via the collapsed '?? sub/'.

    Measured: with -uall git names '?? sub/the-only-copy.md' and ANY file-shaped path blocks
    trivially. Only WITHOUT it does the collapsed '?? sub/' have to be judged by INSPECTING the
    directory — so this is the route a future refactor could silently break, and it is the one
    test that catches it: blinding the inspection still passes every other test here.
    """
    (repo / ".gitignore").write_text(".DS_Store\n")
    sh(repo, "add", ".gitignore")
    sh(repo, "commit", "-q", "-m", "ignore")
    wt = repo.parent / "wt-collapsed-only"
    sh(repo, "worktree", "add", "-q", str(wt), "-b", "n4")
    (wt / "sub").mkdir()
    (wt / "sub" / ".DS_Store").write_bytes(b"finder\n")
    (wt / "sub" / "the-only-copy.md").write_text("a plan ledger\n")

    real_git = wys.git

    def collapsed_git(args, cwd=None):
        """The module's own git wrapper, with --untracked-files=all dropped from the args."""
        return real_git([a for a in args if a != "--untracked-files=all"], cwd)

    monkeypatch.setattr(wys, "git", collapsed_git)
    assert fired(f"git worktree remove {wt} --force", repo, "git/worktree-remove-ignored")


def test_worktree_with_only_caches_passes(repo):
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    sh(repo, "add", ".gitignore")
    sh(repo, "commit", "-q", "-m", "ignore")
    wt = repo.parent / "wt2"
    sh(repo, "worktree", "add", "-q", str(wt), "-b", "f2")
    (wt / "__pycache__").mkdir()
    (wt / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"\0")
    assert not fired(f"git worktree remove {wt}", repo, "git/worktree-remove-ignored")


# --- this rule must BLOCK, not warn ----------------------------------------------------------
# Incident 2026-10-26: a session removed a worktree holding a plan's progress ledger
# (.superpowers/sdd/…, gitignored). The rule DID detect it — and the removal went ahead, because
# severity 'warn' yields additionalContext while 'block' yields permissionDecision=deny. A guard
# that fires and does not stop is indistinguishable from one that never fired. Five sibling rules
# already block, including the other git rule, so the block path is proven here.
@pytest.fixture
def ledger_worktree(repo):
    (repo / ".gitignore").write_text("*.log\n.superpowers/\n__pycache__/\n")
    sh(repo, "add", ".gitignore")
    sh(repo, "commit", "-q", "-m", "ignore")
    wt = repo.parent / "wt-ledger"
    sh(repo, "worktree", "add", "-q", str(wt), "-b", "feature-ledger")
    (wt / ".superpowers").mkdir()
    (wt / ".superpowers" / "progress.md").write_text("the only copy\n")
    return wt


def test_worktree_remove_ignored_is_a_block_rule():
    r = next(r for r in RULES if r["id"] == "git/worktree-remove-ignored")
    assert r["severity"] == "block", (
        "severity 'warn' lets the destructive call proceed — the loss this rule exists to prevent")


def test_a_fired_hit_denies_rather_than_comments(ledger_worktree, tmp_path):
    """The emit path, asserted: a fired rule must produce permissionDecision=deny."""
    hits = wys.evaluate("Bash", {"command": f"git worktree remove {ledger_worktree} --force"},
                        shell="zsh", user="t", rules=RULES, cwd=str(tmp_path))
    assert any(h["id"] == "git/worktree-remove-ignored" for h in hits), "the rule did not fire"
    out = wys.hook_output(hits)["hookSpecificOutput"]
    assert out.get("permissionDecision") == "deny", (
        f"a fired rule that only adds context does not stop anything: {sorted(out)}")
    assert "permissionDecisionReason" in out
    assert "additionalContext" not in out, (
        "a denied call must not ALSO be narrated as context — one or the other, not both")


def test_the_compound_form_that_caused_the_loss_still_fires(ledger_worktree, tmp_path):
    # The real command was a compound: cd …; …; git worktree remove <path> --force 2>&1 | tail -2
    cmd = (f"cd {tmp_path}; L=/tmp/x; echo hi >> \"$L\"; "
           f"git worktree remove {ledger_worktree} --force 2>&1 | tail -2; "
           f"git worktree list | head -3")
    hits = wys.evaluate("Bash", {"command": cmd}, shell="zsh", user="t",
                        rules=RULES, cwd=str(tmp_path))
    assert any(h["id"] == "git/worktree-remove-ignored" for h in hits), (
        "the matcher must see the pattern anywhere in a compound command, not only at its start")


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
    assert "2 uncommitted paths stay behind" in wys.render([h])


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


def test_stash_follows_cd_and_dash_C(repo, tmp_path):
    (repo / "new.txt").write_text("untracked\n")
    assert fired(f"cd {repo} && git stash", tmp_path, "git/stash-untracked")
    assert fired(f"git -C {repo} stash", tmp_path, "git/stash-untracked")


def test_stash_store_passes(repo):
    (repo / "new.txt").write_text("untracked\n")
    assert not fired("git stash store HEAD@{0}", repo, "git/stash-untracked")


def test_worktree_add_after_stash_untracked_in_same_command_passes(repo):
    (repo / "new.txt").write_text("untracked\n")
    assert not fired("git stash -u && git worktree add ../wt -b f", repo, "git/worktree-add-dirty")


def test_real_cache_dir_stays_silent_but_cache_like_name_warns(repo):
    (repo / "venv").mkdir()
    (repo / "venv" / "x.py").write_text("cache\n")
    assert not fired("git stash", repo, "git/stash-untracked")
    (repo / "myvenv").mkdir()
    (repo / "myvenv" / "plan.md").write_text("the only copy\n")
    assert fired("git stash", repo, "git/stash-untracked")


def test_worktree_add_count_is_singular_and_path_worded(repo):
    (repo / "wip.txt").write_text("uncommitted\n")
    hits = wys.evaluate("Bash", {"command": "git worktree add ../wt-s -b feature"}, shell="zsh",
                        user="t", rules=RULES, cwd=str(repo))
    h = next(h for h in hits if h["id"] == "git/worktree-add-dirty")
    assert "1 uncommitted path stays behind" in wys.render([h])


def test_subshell_cd_does_not_move_the_repo(repo):
    (repo / "wip.txt").write_text("uncommitted\n")
    assert fired("x=$(cd /tmp && pwd); git worktree add ../wt-p -b f", repo, "git/worktree-add-dirty")
