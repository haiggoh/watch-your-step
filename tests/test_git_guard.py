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
