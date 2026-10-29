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
TESTED = {"git/staged-conflict-markers", "git/worktree-remove-ignored", "git/worktree-add-dirty",
          "git/stash-untracked", "git/branch-delete-unrecoverable", "git/discards-uncommitted"}
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


# --- git/branch-dedelete-unrecoverable: the commits that would vanish --------------------------
# Measured 2026-10-27 while porting run-to-completion's ship cleanup: a branch deletion is only
# protected by git's own refusals, and those stop applying in two places that matter. `git branch -d`
# refuses an unmerged branch — but `-D` overrides it. `git push --delete` refuses the branch the
# remote's HEAD points at — and stops refusing the moment origin/HEAD points elsewhere, which is the
# shape of a repo that renamed its default after cloning (echoprism ships `master`). Neither case is
# reachable by the existing worktree rules, which look at FILES, not at commit reachability.
RULE = "git/branch-delete-unrecoverable"


@pytest.fixture
def origin_repo(tmp_path):
    """A repo with a local bare 'origin', plus a TOPIC branch whose commit exists nowhere else."""
    bare = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", str(bare))
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    (r / "ok.txt").write_text("fine\n")
    sh(r, "add", "ok.txt")
    sh(r, "commit", "-q", "-m", "init")
    sh(r, "remote", "add", "origin", str(bare))
    sh(r, "push", "-q", "-u", "origin", "main")
    sh(r, "checkout", "-q", "-b", "topic")
    (r / "unique.txt").write_text("the only copy\n")
    sh(r, "add", "unique.txt")
    sh(r, "commit", "-q", "-m", "unique")
    sh(r, "checkout", "-q", "main")
    return r


def test_branch_D_of_unmerged_blocks(origin_repo):
    """The primary case: `-D` overrides git's own merged-branch refusal, and nothing else holds it."""
    assert fired("git branch -D topic", origin_repo, RULE)
    assert fired("git branch -d topic", origin_repo, RULE)


def test_branch_delete_of_merged_passes(origin_repo):
    # A branch whose tip IS the default branch's tip: an ancestor, so every commit survives.
    sh(origin_repo, "branch", "merged-tip")
    assert not fired("git branch -D merged-tip", origin_repo, RULE)


def test_branch_D_of_the_local_default_branch_blocks(origin_repo):
    """The default branch is protected by NAME, not by reachability: every other branch holds it,
    so the reachability test alone would wave it through."""
    assert fired("git branch -D main", origin_repo, RULE)


def test_branch_v_is_not_a_deletion(origin_repo):
    """`git branch -v topic` SHOWS a branch; only the flag distinguishes it from `-D topic`. Without
    the flag filter the flag group is just "some flags" and a read-only command fires."""
    assert fired("git branch -D topic", origin_repo, RULE)          # the control: -D does fire
    assert not fired("git branch -v topic", origin_repo, RULE)
    assert not fired("git branch --show-current", origin_repo, RULE)


def test_branch_delete_passes_when_another_ref_holds_it(origin_repo):
    """The commits are not gone — another local branch still points at them."""
    sh(origin_repo, "branch", "also-topic", "topic")
    assert not fired("git branch -D topic", origin_repo, RULE)


def test_push_delete_default_branch_blocks_when_head_points_elsewhere(origin_repo, tmp_path):
    """The case git does NOT protect: origin/HEAD points at an unmerged decoy, so git's refusal to
    delete 'the current branch' applies to the decoy, not to `main`."""
    bare = origin_repo.parent / "origin.git"
    sh(origin_repo, "checkout", "-q", "-b", "decoy")
    (origin_repo / "decoy.txt").write_text("unreleasable\n")
    sh(origin_repo, "add", "decoy.txt")
    sh(origin_repo, "commit", "-q", "-m", "decoy")
    sh(origin_repo, "push", "-q", "origin", "decoy")
    sh(origin_repo, "checkout", "-q", "main")
    sh(bare, "symbolic-ref", "HEAD", "refs/heads/decoy")
    assert fired("git push origin --delete main", origin_repo, RULE)


def test_push_delete_merged_topic_passes(origin_repo):
    # topic is merged into the remote's default branch, so origin/topic's commits all survive there.
    # It must be PUSHED first: without a remote branch to delete, `ls-remote` finds nothing and this
    # test passes for the wrong reason — the remote-deletion path never runs at all.
    sh(origin_repo, "push", "-q", "origin", "topic")
    sh(origin_repo, "merge", "-q", "topic")
    sh(origin_repo, "push", "-q", "origin", "main")
    assert not fired("git push origin --delete topic", origin_repo, RULE)


def test_push_delete_of_the_remote_head_branch_passes(origin_repo):
    """git refuses the branch the remote's HEAD points at, so nothing would be lost: no verdict.

    This is the belt-and-braces the check deliberately leaves out — a warning over a git refusal is
    noise, not protection. The local sweep has the same shape (`git branch -d` refuses a checked-out
    branch), which is why neither case deserves a rule."""
    bare = origin_repo.parent / "origin.git"
    sh(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    assert not fired("git push origin --delete main", origin_repo, RULE)


def test_the_rule_is_a_block_rule():
    r = next(r for r in RULES if r["id"] == RULE)
    assert r["severity"] == "block", (
        "severity 'warn' lets the destructive call proceed — the loss this rule exists to prevent")


def test_a_fired_branch_delete_hit_denies_rather_than_comments(origin_repo, tmp_path):
    """The emit path, asserted — the same shape the sibling worktree rule learned to pin.

    Named distinctly from the worktree rule's test of the same claim on purpose: two functions
    sharing a name means the second definition silently replaces the first, and the sibling's
    deny-path coverage would vanish without a single test failing.
    """
    hits = wys.evaluate("Bash", {"command": "git branch -D topic"}, shell="zsh", user="t",
                        rules=RULES, cwd=str(origin_repo))
    assert any(h["id"] == RULE for h in hits), "the rule did not fire"
    out = wys.hook_output(hits)["hookSpecificOutput"]
    assert out.get("permissionDecision") == "deny", (
        f"a fired rule that only adds context does not stop anything: {sorted(out)}")
    assert "permissionDecisionReason" in out


def check(cmd, cwd):
    """The check called DIRECTLY, not through evaluate().

    evaluate() wraps every check in `try/except Exception: fired = False`, so at that seam 'the rule
    did not fire' and 'the rule RAISED' are the same observation. A totality test that goes through
    evaluate() therefore cannot see a crash — which is exactly how a missing-cwd TypeError in both
    remote helpers sat behind a green suite, with the test written to catch precisely that.
    """
    return wys.CHECKS["branch_delete_unrecoverable"]([], bare_view=cmd, user="t", cwd=str(cwd))


def test_the_check_is_total_and_never_raises(origin_repo, tmp_path):
    """The hook fails OPEN on an exception, so a None-path or unreadable repo inside this check is a
    silent ALLOW. Every input it cannot judge must return False, not raise.

    The remote-path commands are listed explicitly because they are the ones that reach
    `_remote_head`/`_remote_sha`. Asserted through `check()`, not `fired()`, for the reason in its
    docstring — an exception must FAIL here, not be laundered into 'did not fire'."""
    not_a_repo = tmp_path / "nowhere"
    not_a_repo.mkdir()
    for cmd in ("git branch -D", "git branch -D nonexistent-branch", "git push origin --delete",
                "git push origin --delete nonexistent-branch", "git push origin :", "git push -d",
                "echo hello", "git status"):
        for cwd in (origin_repo, not_a_repo):
            assert check(cmd, cwd) is False, f"{cmd!r} in {cwd.name} raised or fired"


# --- git/discards-uncommitted: a git command that silently discards uncommitted work -----------
# Measured 2026-10-28 (echoprism Task 10): `git checkout -- src-tauri/src/db/queries.rs 2>/dev/null`,
# meant to revert a test mutation, also discarded the uncommitted backfill in the same file and
# exited 0. The pathspec form of checkout/restore restores from the index or a revision and never
# refuses — unlike a branch switch, which either carries the changes or errors out. `reset --hard`
# and `clean -f` destroy the same class of work; one rule, because the same-command escape
# (`git stash -u`) and the state read are shared.
DISCARD_RULE = "git/discards-uncommitted"


def test_checkout_dash_dash_on_dirty_path_blocks(repo):
    (repo / "ok.txt").write_text("changed\n")     # unstaged: the worktree copy differs from the index
    assert fired("git checkout -- ok.txt", repo, DISCARD_RULE)


def test_the_incident_compound_form_fires(repo, tmp_path):
    """The real command ran mid-compound with its own stderr hidden; both shapes must survive."""
    (repo / "ok.txt").write_text("changed\n")
    cmd = f"cd {repo} && git checkout -- ok.txt 2>/dev/null; git status --porcelain -- ok.txt"
    assert fired(cmd, tmp_path, DISCARD_RULE)


def test_checkout_rev_form_blocks_staged_only(repo):
    """checkout <rev> -- <path> rewrites the worktree from the rev: a STAGED-only edit (the worktree
    matches the index) is destroyed too, so it must fire where the index form does not."""
    (repo / "ok.txt").write_text("changed\n")
    sh(repo, "add", "ok.txt")                     # X=M, Y=' '
    assert fired("git checkout HEAD -- ok.txt", repo, DISCARD_RULE)


def test_staged_only_index_restore_passes(repo):
    """`git checkout -- <path>` restores the worktree FROM the index: a staged-only edit already
    matches the index, so nothing is lost. Firing here would block a no-op."""
    (repo / "ok.txt").write_text("changed\n")
    sh(repo, "add", "ok.txt")
    assert not fired("git checkout -- ok.txt", repo, DISCARD_RULE)
    assert not fired("git restore --staged ok.txt", repo, DISCARD_RULE)


def test_index_only_restore_never_fires(repo):
    """`git restore --staged` rewrites only the index: the worktree copy keeps every edit, so the
    work survives as an unstaged change. An index-only write is recoverable — no verdict."""
    (repo / "ok.txt").write_text("changed\n")     # unstaged only
    assert not fired("git restore --staged ok.txt", repo, DISCARD_RULE)


def test_restore_forms_block_unstaged_dirt(repo):
    (repo / "ok.txt").write_text("changed\n")
    assert fired("git restore ok.txt", repo, DISCARD_RULE)
    assert fired("git restore --source=HEAD ok.txt", repo, DISCARD_RULE)
    assert fired("git restore -s HEAD ok.txt", repo, DISCARD_RULE)   # the separated source value is a rev, not a path
    assert not fired("git restore other.txt", repo, DISCARD_RULE)    # a clean path loses nothing


def test_forced_switch_blocks_dirty_tree(repo):
    (repo / "ok.txt").write_text("changed\n")
    assert fired("git checkout -f main", repo, DISCARD_RULE)
    assert fired("git switch --discard-changes main", repo, DISCARD_RULE)
    assert not fired("git checkout main", repo, DISCARD_RULE)        # no force: git carries or refuses
    assert not fired("git checkout -b feature", repo, DISCARD_RULE)  # a new branch carries the work


def test_reset_hard_blocks_tracked_dirt(repo):
    assert not fired("git reset --hard", repo, DISCARD_RULE)          # clean
    (repo / "new.txt").write_text("untracked\n")
    assert not fired("git reset --hard", repo, DISCARD_RULE)          # untracked files survive a hard reset
    (repo / "ok.txt").write_text("changed\n")
    assert fired("git reset --hard", repo, DISCARD_RULE)              # unstaged
    sh(repo, "add", "ok.txt")
    assert fired("git reset --hard", repo, DISCARD_RULE)              # staged-only is destroyed too


def test_clean_force_blocks_untracked(repo):
    assert not fired("git clean", repo, DISCARD_RULE)                 # git refuses without a force flag
    assert not fired("git clean -n", repo, DISCARD_RULE)              # dry run deletes nothing
    (repo / "new.txt").write_text("untracked\n")
    assert fired("git clean -f", repo, DISCARD_RULE)
    assert fired("git clean -fd", repo, DISCARD_RULE)


def test_clean_x_counts_ignored_files(repo):
    """`-x` extends clean to IGNORED files, and the docs claim it — so the positive direction needs
    its own pin; the cache-noise test only exercises the negative one."""
    (repo / ".gitignore").write_text("*.log\n")
    sh(repo, "add", ".gitignore")
    sh(repo, "commit", "-q", "-m", "ignore logs")
    (repo / "ignored.log").write_text("the only copy\n")
    assert not fired("git clean -fd", repo, DISCARD_RULE)    # -d alone leaves ignored files alone
    assert fired("git clean -fdx", repo, DISCARD_RULE)       # -x deletes them for good


def test_newline_separated_lines_do_not_pollute_the_pathspecs(repo):
    """A newline separates commands like `;` does. Unless the arg capture stops at it, the NEXT
    line's words become pathspecs of the first command — and a dirty filename merely MENTIONED on a
    later line turns a clean restore into a false block (found in cross-family review)."""
    (repo / "keep.txt").write_text("clean\n")
    sh(repo, "add", "keep.txt")
    sh(repo, "commit", "-q", "-m", "keep")
    (repo / "ok.txt").write_text("changed\n")                # dirty, but only mentioned on line 2
    assert not fired("git checkout -- keep.txt\necho ok.txt", repo, DISCARD_RULE)
    assert fired("git checkout -- ok.txt\necho done", repo, DISCARD_RULE)   # the control: still fires


def test_stash_u_escape_in_same_command_passes(repo):
    (repo / "new.txt").write_text("untracked\n")
    (repo / "ok.txt").write_text("changed\n")
    assert not fired("git stash -u && git checkout -- ok.txt", repo, DISCARD_RULE)


def test_untracked_only_path_restore_passes(repo):
    """`git checkout -- <untracked>` is a pathspec git ERRORS on, not a loss: '??' rows are no verdict.
    The rev form needs its own pin — without it, dropping the '??' guard stays green because the
    from-the-index branch already excludes '?' via the column test, and the guard is load-bearing
    only when the worktree is rewritten from a rev (found by the mutation matrix)."""
    (repo / "wip.txt").write_text("untracked\n")
    assert not fired("git checkout -- wip.txt", repo, DISCARD_RULE)
    assert not fired("git checkout HEAD -- wip.txt", repo, DISCARD_RULE)


def test_unresolvable_paths_give_no_verdict(repo):
    (repo / "ok.txt").write_text("changed\n")
    assert not fired('git checkout -- "$f"', repo, DISCARD_RULE)
    assert not fired("git checkout -- $f", repo, DISCARD_RULE)
    # the conservative whole-match rule: one unresolvable pathspec silences even the resolvable
    # one beside it — judging only ok.txt would fire here, which is the mutation that must go red
    assert not fired('git checkout -- "$f" ok.txt', repo, DISCARD_RULE)


def test_discard_follows_cd_and_dash_C(repo, tmp_path):
    (repo / "ok.txt").write_text("changed\n")
    assert fired(f"cd {repo} && git checkout -- ok.txt", tmp_path, DISCARD_RULE)
    assert fired(f"git -C {repo} checkout -- ok.txt", tmp_path, DISCARD_RULE)


def test_cache_only_dirt_stays_silent(repo):
    (repo / ".DS_Store").write_bytes(b"\0")
    for cmd in ("git checkout -- .", "git checkout -f main", "git restore .",
                "git reset --hard", "git clean -fdx"):
        assert not fired(cmd, repo, DISCARD_RULE), cmd


def test_a_fired_discard_hit_denies_rather_than_comments(repo):
    """The emit path, asserted — named distinctly from the two sibling tests of the same claim
    (two functions sharing a name silently replace each other; see the branch-delete lesson)."""
    (repo / "ok.txt").write_text("changed\n")
    hits = wys.evaluate("Bash", {"command": "git checkout -- ok.txt"}, shell="zsh", user="t",
                        rules=RULES, cwd=str(repo))
    assert any(h["id"] == DISCARD_RULE for h in hits), "the rule did not fire"
    out = wys.hook_output(hits)["hookSpecificOutput"]
    assert out.get("permissionDecision") == "deny", (
        f"a fired rule that only adds context does not stop anything: {sorted(out)}")
    assert "permissionDecisionReason" in out


def check_discard(cmd, cwd):
    """The check called DIRECTLY, for the reason check() documents above: evaluate() wraps every
    check in try/except, so at that seam 'did not fire' and 'raised' are one observation."""
    return wys.CHECKS["discards_uncommitted"]([], bare_view=cmd, user="t", cwd=str(cwd))


def test_the_discard_check_is_total_and_never_raises(repo, tmp_path):
    """The hook fails OPEN on an exception, so a crash inside this check is a silent ALLOW. The
    clean repo pins 'no verdict on clean state'; the non-repo pins 'no verdict on unreadable state'."""
    not_a_repo = tmp_path / "nowhere"
    not_a_repo.mkdir()
    for cmd in ("git checkout --", "git checkout -- nonexistent-file", "git restore",
                "git reset --hard", "git clean -fd", "git switch --discard-changes",
                "git checkout -- $UNSET", "echo hello", "git status"):
        for cwd in (repo, not_a_repo):
            assert check_discard(cmd, cwd) is False, f"{cmd!r} in {cwd.name} raised or fired"


def test_mutation_discards_check_is_what_fires(repo, monkeypatch):
    (repo / "ok.txt").write_text("changed\n")
    monkeypatch.setitem(wys.CHECKS, "discards_uncommitted", lambda *a, **k: False)
    assert not fired("git checkout -- ok.txt", repo, DISCARD_RULE)
