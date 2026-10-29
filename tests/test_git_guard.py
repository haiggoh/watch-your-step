"""The git-level guard (0.6.0): reference-transaction + pre-push handlers, proven on REAL repos.

The handlers are called with the stdin lines git would send; the end-to-end tests further down
install the guard into the suite's temp GIT_CONFIG_GLOBAL and drive real git commands."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wys_guard", ROOT / "bin" / "watch-your-step.py")
assert spec and spec.loader
wys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wys)
Z = "0" * 40


def sh(cwd, *args, check=True, env=None):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True, env=env)


def rev(cwd, ref):
    return sh(cwd, "rev-parse", ref).stdout.strip()


@pytest.fixture
def guarded(tmp_path):
    """A repo with a bare 'origin'; `lonely` holds a commit nowhere else, `merged` sits on main."""
    bare = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", str(bare))
    r = tmp_path / "repo"
    sh(tmp_path, "init", "-q", "-b", "main", str(r))
    (r / "f").write_text("a\n")
    sh(r, "add", "f")
    sh(r, "commit", "-q", "-m", "a")
    sh(r, "remote", "add", "origin", str(bare))
    sh(r, "push", "-q", "-u", "origin", "main")
    sh(r, "branch", "merged")
    sh(r, "switch", "-q", "-c", "lonely")
    (r / "f").write_text("only here\n")
    sh(r, "commit", "-q", "-am", "only here")
    sh(r, "switch", "-q", "main")
    return r


# ---- reference-transaction ------------------------------------------------------------------

def test_ref_refuses_a_prepared_deletion_of_an_unmerged_branch(guarded):
    code, msg = wys.git_hook_ref("prepared", [f"{Z} {Z} refs/heads/lonely"], str(guarded))
    assert code == 1
    assert "lonely" in msg
    assert "git -c hook.wys-branch-guard.enabled=false branch -D lonely" in msg
    assert "WYS_ALLOW_BRANCH_DELETE=1" in msg


def test_ref_allows_a_merged_branch(guarded):
    assert wys.git_hook_ref("prepared", [f"{Z} {Z} refs/heads/merged"], str(guarded))[0] == 0


def test_ref_acts_only_in_the_prepared_phase(guarded):
    for phase in ("preparing", "committed", "aborted"):
        assert wys.git_hook_ref(phase, [f"{Z} {Z} refs/heads/lonely"], str(guarded))[0] == 0


def test_ref_uses_the_real_old_value_when_git_passes_one(guarded):
    """`branch -m` passes the real old oid; `branch -D` passes zeros — both must resolve."""
    tip = rev(guarded, "lonely")
    assert wys.git_hook_ref("prepared", [f"{tip} {Z} refs/heads/lonely"], str(guarded))[0] == 1


def test_ref_counts_refs_set_in_the_same_transaction_as_holders(guarded):
    tip = rev(guarded, "lonely")
    lines = [f"{tip} {Z} refs/heads/lonely", f"{Z} {tip} refs/heads/renamed"]
    assert wys.git_hook_ref("prepared", lines, str(guarded))[0] == 0


def test_ref_ignores_non_branch_refs(guarded):
    assert wys.git_hook_ref("prepared", [f"{Z} {Z} refs/tags/v1"], str(guarded))[0] == 0
    assert wys.git_hook_ref("prepared", [f"{Z} {Z} refs/remotes/origin/x"], str(guarded))[0] == 0


def test_ref_in_an_empty_repo_is_silent(tmp_path):
    sh(tmp_path, "init", "-q", "e")
    assert wys.git_hook_ref("prepared", [f"{Z} {Z} refs/heads/x"], str(tmp_path / "e"))[0] == 0


# ---- pre-push -------------------------------------------------------------------------------

def test_push_refuses_deleting_a_remote_only_branch(guarded):
    sh(guarded, "push", "-q", "origin", "lonely")
    tip = rev(guarded, "lonely")
    sh(guarded, "branch", "-D", "lonely")
    sh(guarded, "fetch", "-q", "origin")
    code, msg = wys.git_hook_push("origin", [f"(delete) {Z} refs/heads/lonely {tip}"], str(guarded))
    assert code == 1
    assert "git -c hook.wys-push-guard.enabled=false push origin --delete lonely" in msg


def test_push_allows_deleting_a_branch_held_locally(guarded):
    sh(guarded, "push", "-q", "origin", "merged")
    tip = rev(guarded, "merged")
    assert wys.git_hook_push("origin", [f"(delete) {Z} refs/heads/merged {tip}"], str(guarded))[0] == 0


def test_push_refuses_when_the_remote_commit_is_not_here(guarded):
    ghost = "1234567890abcdef1234567890abcdef12345678"
    assert wys.git_hook_push("origin", [f"(delete) {Z} refs/heads/gone {ghost}"], str(guarded))[0] == 1


def test_push_refuses_deleting_a_remote_default_branch(guarded):
    tip = rev(guarded, "main")
    assert wys.git_hook_push("origin", [f"(delete) {Z} refs/heads/main {tip}"], str(guarded))[0] == 1


def test_push_ignores_ordinary_updates(guarded):
    tip = rev(guarded, "main")
    assert wys.git_hook_push("origin", [f"refs/heads/main {tip} refs/heads/main {tip}"], str(guarded))[0] == 0


# ---- the command wrapper: env gates and fail-open ----------------------------------------------

def test_wrapper_fails_open_on_an_internal_error(guarded, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("planted")
    monkeypatch.setattr(wys, "git_hook_ref", boom)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert wys.cmd_git_hook(["ref", "prepared"], f"{Z} {Z} refs/heads/lonely\n", str(guarded)) == 0
    assert "planted" in capsys.readouterr().err


def test_wrapper_needs_claudecode(guarded, monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    assert wys.cmd_git_hook(["ref", "prepared"], f"{Z} {Z} refs/heads/lonely\n", str(guarded)) == 0
    monkeypatch.setenv("CLAUDECODE", "1")
    assert wys.cmd_git_hook(["ref", "prepared"], f"{Z} {Z} refs/heads/lonely\n", str(guarded)) == 1


def test_wrapper_honours_the_env_override(guarded, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("WYS_ALLOW_BRANCH_DELETE", "1")
    assert wys.cmd_git_hook(["ref", "prepared"], f"{Z} {Z} refs/heads/lonely\n", str(guarded)) == 0


def test_wrapper_rejects_an_unknown_kind(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert wys.cmd_git_hook(["bogus"], "", ".") == 0     # fail-open, never blocks git


# ---- git-guard ensure / install / uninstall / status (B4) --------------------------------------
# Each test gets its OWN global config: the suite-wide temp file is shared by every test in a
# worker, and a guard installed there would start refusing other tests' `git branch -D` setup.

KEYS = ("hook.wys-branch-guard.event", "hook.wys-branch-guard.command",
        "hook.wys-push-guard.event", "hook.wys-push-guard.command")


@pytest.fixture
def own_global(tmp_path, monkeypatch):
    g = tmp_path / "own-gitconfig"
    g.write_text("[user]\n\tname = t\n\temail = t@t\n[core]\n\tquotepath = off\n[alias]\n\tst = status\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(g))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("WYS_ALLOW_BRANCH_DELETE", raising=False)
    return g


def gcfg(*args):
    p = subprocess.run(["git", "config", "--global", *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


SCRIPT = str((ROOT / "bin" / "watch-your-step.py").resolve())


def test_ensure_installs_both_hooks_and_announces(own_global):
    code, out = wys.git_guard("ensure", SCRIPT)
    assert code == 0
    assert "installed a git branch-deletion guard" in out and "git-guard uninstall" in out
    assert gcfg("hook.wys-branch-guard.event") == "reference-transaction"
    assert gcfg("hook.wys-push-guard.event") == "pre-push"
    assert SCRIPT in gcfg("hook.wys-branch-guard.command")
    # git appends "$@": the ref one-liner must END in a command that ignores arguments (`true`),
    # the push one-liner in the exec that consumes them — never in `exit 0` (fail-closed, measured)
    ref_cmd = gcfg("hook.wys-branch-guard.command")
    assert "git-hook ref prepared" in ref_cmd and ref_cmd.rstrip().endswith("; true")
    assert gcfg("hook.wys-push-guard.command").rstrip().endswith('exec python3 "$f" git-hook push')


def test_ensure_twice_is_silent_and_idempotent(own_global):
    wys.git_guard("ensure", SCRIPT)
    code, out = wys.git_guard("ensure", SCRIPT)
    assert code == 0 and out == ""
    assert gcfg("--get-all", "hook.wys-branch-guard.event").splitlines() == ["reference-transaction"]


def test_uninstall_is_respected_by_later_ensures(own_global):
    wys.git_guard("ensure", SCRIPT)
    code, out = wys.git_guard("uninstall", SCRIPT)
    assert code == 0 and "disabled" in out
    before = own_global.read_text()
    assert wys.git_guard("ensure", SCRIPT) == (0, "")
    assert own_global.read_text() == before
    assert gcfg("hook.wys-branch-guard.enabled") == "false"
    assert wys.git_guard("install", SCRIPT)[0] == 0
    assert gcfg("hook.wys-branch-guard.enabled") == "true"


def test_ensure_rewrites_a_stale_script_path(own_global):
    wys.git_guard("ensure", "/old/cache/0.5.9/bin/watch-your-step.py")
    code, out = wys.git_guard("ensure", SCRIPT)
    assert "updated" in out
    assert SCRIPT in gcfg("hook.wys-branch-guard.command")


def test_ensure_leaves_every_other_key_alone(own_global):
    before = gcfg("--get-regexp", r"^(user|core|alias)\.")
    wys.git_guard("ensure", SCRIPT)
    wys.git_guard("uninstall", SCRIPT)
    assert gcfg("--get-regexp", r"^(user|core|alias)\.") == before


def test_ensure_refuses_a_path_with_a_single_quote(own_global):
    code, out = wys.git_guard("ensure", "/tmp/it's/bin/watch-your-step.py")
    assert code == 0 and "not installed" in out
    assert all(gcfg(k) is None for k in KEYS)


def test_ensure_skips_a_git_without_config_hooks(own_global, tmp_path, monkeypatch):
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\nexit 0\n")      # lists no hooks, writes nothing
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{__import__('os').environ['PATH']}")
    assert wys.git_guard("ensure", SCRIPT) == (0, "")
    monkeypatch.undo()


def test_ensure_runs_concurrently_without_duplicates(own_global):
    import os
    import sys
    env = dict(os.environ)
    procs = [subprocess.Popen([sys.executable, SCRIPT, "git-guard", "ensure"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
    for p in procs:
        p.communicate(timeout=30)
    assert gcfg("--get-all", "hook.wys-branch-guard.event").splitlines() == ["reference-transaction"]
    assert gcfg("--get-all", "hook.wys-push-guard.event").splitlines() == ["pre-push"]


def test_status_shows_gits_own_view(own_global):
    wys.git_guard("ensure", SCRIPT)
    code, out = wys.git_guard("status", SCRIPT)
    assert code == 0 and "wys-branch-guard" in out and "wys-push-guard" in out and "global" in out


def test_git_guard_rejects_an_unknown_action(own_global):
    assert wys.main(["git-guard", "bogus"]) == 2


# ---- end to end: the installed guard against REAL git commands (B1-B3) ---------------------------

def g(repo, *args, env_extra=None, unset=()):
    import os
    env = {k: v for k, v in os.environ.items() if k not in unset}
    env.update(env_extra or {})
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env)


def exists(repo, branch):
    return g(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0


@pytest.fixture
def live(own_global, guarded):
    wys.git_guard("ensure", SCRIPT)
    return guarded


def test_e2e_branch_D_of_an_unmerged_branch_is_refused(live):
    p = g(live, "branch", "-D", "lonely")
    assert p.returncode != 0 and "refused deleting branch lonely" in p.stderr
    assert exists(live, "lonely")


def test_e2e_merged_branch_deletes(live):
    assert g(live, "branch", "-D", "merged").returncode == 0 and not exists(live, "merged")


def test_e2e_rename_passes(live):
    assert g(live, "branch", "-m", "lonely", "renamed").returncode == 0
    assert exists(live, "renamed") and not exists(live, "lonely")


def test_e2e_update_ref_d_is_refused(live):
    assert g(live, "update-ref", "-d", "refs/heads/lonely").returncode != 0 and exists(live, "lonely")


def test_e2e_python_subprocess_route_is_refused(live):
    import sys
    subprocess.run([sys.executable, "-c", "import subprocess; subprocess.run(['git', 'branch', '-D', 'lonely'])"],
                   cwd=live, capture_output=True)
    assert exists(live, "lonely")


def test_e2e_both_overrides_delete(live):
    g(live, "branch", "copy", "lonely")
    assert g(live, "-c", "hook.wys-branch-guard.enabled=false", "branch", "-D", "lonely").returncode == 0
    assert not exists(live, "lonely")
    g(live, "branch", "-m", "copy", "lonely")
    assert g(live, "branch", "-D", "lonely", env_extra={"WYS_ALLOW_BRANCH_DELETE": "1"}).returncode == 0


def test_e2e_outside_claude_the_guard_is_inert(live):
    assert g(live, "branch", "-D", "lonely", unset=("CLAUDECODE",)).returncode == 0


def test_e2e_ordinary_work_is_unaffected(live):
    (live / "f").write_text("b\n")
    assert g(live, "commit", "-qam", "b").returncode == 0
    assert g(live, "fetch", "-q", "origin").returncode == 0
    assert g(live, "merge", "-q", "lonely", "-m", "m").returncode in (0, 1)  # conflict is fine; no hook abort
    g(live, "merge", "--abort")
    assert g(live, "switch", "-q", "lonely").returncode == 0
    assert g(live, "rebase", "-q", "main").returncode in (0, 1)
    g(live, "rebase", "--abort")


def test_e2e_a_vanished_script_never_blocks_git(own_global, guarded, tmp_path):
    """The fail-CLOSED regression: a one-liner whose safety exit came last aborted an ordinary
    commit in the probe (git appends "$@"), so a missing script must still let git through."""
    wys.git_guard("ensure", str(tmp_path / "gone" / "bin" / "watch-your-step.py"))
    (guarded / "f").write_text("c\n")
    assert g(guarded, "commit", "-qam", "c").returncode == 0
    assert g(guarded, "branch", "-D", "lonely").returncode == 0     # no script: no verdict


def test_e2e_script_path_with_a_space(own_global, guarded, tmp_path):
    import shutil
    d = tmp_path / "dir with space" / "bin"
    d.mkdir(parents=True)
    shutil.copy(SCRIPT, d / "watch-your-step.py")
    shutil.copytree(ROOT / "rules", tmp_path / "dir with space" / "rules")
    wys.git_guard("ensure", str(d / "watch-your-step.py"))
    assert g(guarded, "branch", "-D", "lonely").returncode != 0 and exists(guarded, "lonely")


def test_e2e_pre_push_refuses_deleting_a_remote_only_branch(live):
    g(live, "push", "-q", "origin", "lonely")
    g(live, "-c", "hook.wys-branch-guard.enabled=false", "branch", "-D", "lonely")
    g(live, "fetch", "-q", "origin")
    p = g(live, "push", "origin", "--delete", "lonely")
    assert p.returncode != 0 and "refused deleting origin/lonely" in p.stderr
    assert "lonely" in g(live, "ls-remote", "--heads", "origin").stdout


def test_e2e_pre_push_allows_deleting_a_merged_remote_branch(live):
    g(live, "push", "-q", "origin", "merged")
    assert g(live, "push", "-q", "origin", "--delete", "merged").returncode == 0


def test_e2e_overhead_per_transaction_is_small(live):
    import time
    t = time.monotonic()
    for i in range(10):
        g(live, "branch", f"t{i}")
    per = (time.monotonic() - t) / 10
    assert per < 0.5, f"{per:.3f}s per branch creation with the guard installed"


def test_e2e_rename_of_a_branch_nothing_else_holds_passes(live):
    """The rename deletes the old ref BEFORE creating the new one (measured), so the refs cannot
    tell it from a loss; the parent's argv can. A plain -D of the same branch must still refuse."""
    assert g(live, "branch", "-M", "lonely", "moved").returncode == 0 and exists(live, "moved")
    assert g(live, "branch", "-D", "moved").returncode != 0 and exists(live, "moved")


def test_parent_detection_rejects_a_non_move(monkeypatch):
    monkeypatch.setattr(wys.os, "getppid", lambda: __import__("os").getpid())   # ps shows pytest
    assert wys._parent_is_branch_move() is False


def test_session_start_runs_ensure():
    import json
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    cmds = [h["command"] for entry in hooks.get("SessionStart", []) for h in entry["hooks"]]
    assert any(c.endswith("watch-your-step.py\" git-guard ensure") for c in cmds), cmds


def test_python_starts_only_for_a_branch_deletion(own_global, guarded, tmp_path):
    """Inside Claude every `prepared` phase reaches the one-liner; only a refs/heads DELETION may
    start Python (measured before the fast path: 74 ms per transaction vs 9 ms without a guard)."""
    marker = tmp_path / "ran"
    fake = tmp_path / "fake" / "bin" / "watch-your-step.py"
    fake.parent.mkdir(parents=True)
    fake.write_text(f"import pathlib, sys\npathlib.Path({str(marker)!r}).write_text('x')\nsys.stdin.read()\n")
    wys.git_guard("ensure", str(fake))
    g(guarded, "branch", "fresh")
    (guarded / "f").write_text("d\n")
    g(guarded, "commit", "-qam", "d")
    assert not marker.exists(), "Python started for a creation or an update"
    g(guarded, "branch", "-D", "fresh")
    assert marker.exists(), "the deletion never reached the script"


def test_an_uninstalled_guard_survives_a_plugin_update_untouched(own_global):
    """After `uninstall`, a later ensure from a NEW plugin path (an update) must not rewrite the
    disabled hooks either — 'never touch them' covers the path, not only the enabled flag."""
    wys.git_guard("ensure", "/cache/0.6.0/bin/watch-your-step.py")
    wys.git_guard("uninstall", "/cache/0.6.0/bin/watch-your-step.py")
    before = own_global.read_text()
    assert wys.git_guard("ensure", "/cache/0.6.1/bin/watch-your-step.py") == (0, "")
    assert own_global.read_text() == before


def test_status_says_when_git_cannot_host_the_guard(own_global, tmp_path, monkeypatch):
    import os
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\nexit 0\n")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    code, out = wys.git_guard("status", SCRIPT)
    assert code == 0 and "no config-based hooks" in out
