"""watch-your-step: run-5 §C memory primers (plan 2026-11-25-memory-plugin-primer, Task 1).

One parametrised test per primer line migrated from the MEMORY.md index: the SessionStart
`primer` command must carry each memory's literal trigger vocabulary in its
hookSpecificOutput.additionalContext, on BOTH lanes (localhost free proxy and a gateway
base URL). RED until the lines land in rules.json `primer_notes`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "watch-your-step.py"

# memory slug -> exact token that must appear in the primer output
TOKENS = {
    "worktree-gitdir-is-a-file": "rev-parse --absolute-git-dir",
    "claudeworkspace-folder-of-repos": "folder of repos",
    "wys-home-path-guard-any-literal": "path/home-typo",
    "gh-keyring-oauth-fallback": "env -u GH_TOKEN",
    "bsd-sed-s-delimiter-clash": "bad flag in substitute command",
    "gh-api-logs": "--allow-escape-sequences",
    "gh-api-fields": "--method GET",
    "macos-bash-versions": "declare -A",
    "path-differs": "env -i",
    "file-rewrite-exit0-noops": "no-ops",
}

LANES = ["http://127.0.0.1:4147", "https://api.anthropic.com"]


def run_primer(env_extra=None, home=None):
    env = dict(os.environ, WATCH_YOUR_STEP_SHELL="zsh", CLAUDE_PLUGIN_ROOT=str(ROOT))
    env.pop("WATCH_YOUR_STEP_DISABLE", None)
    env.pop("WATCH_YOUR_STEP_RULES", None)
    if home:
        env["HOME"] = home
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "primer"],
        capture_output=True, text=True, env=env, timeout=20,
    )


def primer_context(p):
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("slug,token", list(TOKENS.items()), ids=list(TOKENS))
def test_memory_primer_token_present(slug, token, tmp_path):
    ctx = primer_context(run_primer(home=str(tmp_path)))
    assert token in ctx, f"{slug}: token {token!r} missing from primer output"


@pytest.mark.parametrize("base_url", LANES)
def test_memory_primer_tokens_on_lane(base_url, tmp_path):
    ctx = primer_context(run_primer(env_extra={"ANTHROPIC_BASE_URL": base_url}, home=str(tmp_path)))
    missing = [t for t in TOKENS.values() if t not in ctx]
    assert not missing, f"lane {base_url}: missing tokens {missing}"
