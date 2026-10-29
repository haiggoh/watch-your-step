# watch-your-step 0.6.0 — git guard + rule fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> This run: executing-plans (inline, autopilot), one cross-family review of the whole branch at the end.

**Goal:** Ship 0.6.0: a config-based git `reference-transaction` + `pre-push` branch guard
(auto-installed, Claude-only), the re-measured command-line rule fixes, and two new tool rules.

**Architecture:** Everything lives in the one engine `bin/watch-your-step.py` (the repo's
convention: rules are data in `rules/rules.json`, checks are functions in `CHECKS`). New
subcommands `git-hook` and `git-guard` join `check`/`test`/`rules`. One pure reachability
predicate serves the PreToolUse rule and both git hooks.

**Tech Stack:** Python 3 stdlib, git ≥ config-based hooks (feature-probed), pytest + xdist.

**Spec:** `docs/superpowers/specs/2026-10-29-git-guard-and-rule-fixes-design.md`

## Global Constraints

- Version 0.6.0 on every surface (`tests/test_version_consistency.sh` must agree).
- Hook mode is fail-open everywhere: only a positive verdict refuses.
- Tests never read or write the real `~/.gitconfig` (session fixture + tripwire, Task 1).
- Only `hook.wys-branch-guard.*` and `hook.wys-push-guard.*` keys are ever written.
- The git one-liner keeps its shape: early exits first, `exec python3 "$f" git-hook <ref|push>` last.
- Stage by path; work in a dedicated worktree on branch `feat/git-guard`.

## Review Focus

1. A transaction mixing a deletion with a creation of the same tip (rename, `update-ref --stdin`
   delete+create) → never refused. Pinned in Task 8 (`test_rename_passes`, `test_batch_delete_and_create_same_tip_passes`).
2. A branch name with a slash (`feat/x`) in every layer → judged like any other. Task 3 and 8.
3. A repo with no commits / detached HEAD / a bare repo → the git hook exits 0. Task 8 (`test_hook_in_empty_and_bare_repo_is_silent`).
4. `HOME` containing a space → the installed one-liner still resolves the script. Task 9 (`test_install_path_with_space`).
5. Two Claude sessions running `ensure` at once → config stays valid, no duplicate `event` values. Task 9 (`test_ensure_is_idempotent_no_duplicate_events`).

---

### Task 1: Hermetic git config for the whole suite

**Files:** Create `tests/conftest.py`.

- [ ] Autouse session fixture: set `GIT_CONFIG_GLOBAL` to a temp file (with `user.name`/`user.email`)
      and `GIT_CONFIG_NOSYSTEM=1` for every test; record `sha256` of the real `~/.gitconfig` (or
      absence) at session start and fail the session at teardown if it changed.
- [ ] Run the full suite: 195 still pass. Commit.

### Task 2: Engine plumbing — literal view, tool_input, comment-only allow, strict test mode

**Files:** Modify `bin/watch-your-step.py` (`split_command`, `evaluate`, `cmd_test`); Test `tests/test_rules.py`, `tests/test_git_rules.py`.

**Produces:** `split_command(cmd) -> (bare_view, parts)` where `parts["lit"] == [lit_view]` and
`parts["comment"]` lists comment texts; checks receive `lit_view=`, `tool_input=` kwargs;
`evaluate(..., strict=False)` — strict re-raises a check exception and a missing check id.

- [ ] Failing tests: `test_allow_marker_inside_a_commit_message_does_not_disable_the_rule`
      (`git commit --allow-empty -m "see # wys:allow git/branch-delete-unrecoverable" && git branch -D unmerged` fires);
      `test_allow_marker_as_trailing_comment_still_works`;
      `test_test_mode_surfaces_a_raising_check` (patch a check to raise; `main(["test", …])` returns non-zero, stderr names the exception);
      `test_every_rule_check_exists_in_CHECKS`.
- [ ] Implement: lit view (simple quoted span matching `[\w./@:+=-]+` keeps its content, else `Q`); ALLOW read only from `parts["comment"]`; strict path in `cmd_test` exits 1.
- [ ] Green + full suite. Commit.

### Task 3: Branch-delete rule fixes (A1 A2 A3 A6 A8 A9 A10) + shared predicate

**Files:** Modify `bin/watch-your-step.py` (branch-delete section); Test `tests/test_git_rules.py`.

**Produces:** `unrecoverable(d: str, tip: str, exclude: set[str], extra_holders: set[str] = frozenset()) -> bool`
(True when no ref outside `exclude` contains `tip` and `tip` not in `extra_holders`; refs are
compared in `git for-each-ref` full form, e.g. `refs/remotes/origin/x`); `GIT_PREFIX` regex
fragment for `git` + any `-C <dir>` / `-c <k=v>`.

- [ ] Failing tests (real repos, local bare remote): quoted `"unmerged"` / `'unmerged'` fire;
      `merged unmerged` fires; `push origin --delete lonely` and `push origin :lonely` fire on a
      remote-only commit, pass on a merged one; push to a second remote `upstream` judges `upstream`;
      `git -c core.x=y branch -D unmerged` fires; both overrides (`WYS_ALLOW_BRANCH_DELETE=1 git …`,
      `git -c hook.wys-branch-guard.enabled=false branch -D …`) pass; `feat/x` slash name fires.
      Keep the existing default-branch cases green.
- [ ] Implement using the lit view; one `ls-remote --symref <remote> HEAD refs/heads/<name>` per
      (remote, name) cached per evaluation, total network budget ≤ 4 s.
- [ ] Green + full suite. Commit.

### Task 4: truncate-before-read false positives (A7)

**Files:** Modify `check_truncate_before_read`; Test `tests/cases.json`.

- [ ] Cases: pass `cp X /tmp/X.new && git show HEAD:X > X`, `echo a > a && git add -A && git commit -qm a`,
      `printf x > x`; flag `cat X | sort > X`, `sort X > X`, `grep p X > X; echo done`. Watch the passes RED.
- [ ] Implement: lookbehind adds `:`; skip a segment whose `_cmd_word` is `echo`/`printf`/`print`
      (judged per pipeline stage, so `echo x | tee X > X` style still sees later stages).
- [ ] Green + full suite. Commit.

### Task 5: stash escape stops at a newline (Part C)

- [ ] RED tests: `git worktree add ../wt -b f\necho git stash -u` on a dirty repo fires
      `git/worktree-add-dirty`; same shape for `git/discards-uncommitted` with `git checkout -- ok.txt`.
- [ ] Add `\n` to both classes; replace the line-681 forward reference with the fact. Green. Commit.

### Task 6: `tool/read-pages-non-pdf` (Part D)

**Files:** `rules/rules.json` (new block rule, tools `["Read"]`, check `read_pages_non_pdf`), engine, `tests/cases.json`.

- [ ] Cases: flag Read `{"file_path": "/x/a.tsx", "pages": "2"}`; pass `{"file_path": "/x/a.PDF", "pages": "1-3"}`, pass `{"file_path": "/x/a.tsx", "offset": 40}`. Extend the case runner for extra tool_input keys if needed.
- [ ] Implement; green. Commit.

### Task 7: `tool/edit-anchor-mid-indent` (Part E)

**Files:** `rules/rules.json` (block, tools `["Edit", "MultiEdit"]`, check `edit_anchor_mid_indent`), engine, Test `tests/test_tool_rules.py` (stateful: real temp files; register in the TESTED-elsewhere set).

- [ ] Tests: 8-space anchor inside a 12-space line fires; same anchor at line start passes;
      ` b` inside `a = b` passes; anchor starting `\n` passes; missing file passes; a MultiEdit
      with one bad entry fires.
- [ ] Implement; green. Commit.

### Task 8: `git-hook ref` and `git-hook push` (B1 B2)

**Files:** engine (`cmd_git_hook(args, stdin) -> int`); Test `tests/test_git_guard.py`.

- [ ] Unit tests on real repos, feeding stdin: prepared deletion of unmerged → 1 with both override
      forms in stderr; merged → 0; other phases → 0; rename/batch holders → 0; error (git missing via
      PATH) → 0; empty/bare repo → 0. Push: remote-only oid → 1, held oid → 0, oid absent locally → 1,
      remote default branch → 1.
- [ ] Implement on `unrecoverable`. Green. Commit.

### Task 9: `git-guard ensure|install|uninstall|status` + SessionStart (B3 B4 B5)

**Files:** engine (`cmd_git_guard`), `hooks/hooks.json` (SessionStart entry), Test `tests/test_git_guard.py`.

- [ ] Tests (temp `GIT_CONFIG_GLOBAL`): fresh `ensure` writes 4 keys + announce line; second run
      silent; `uninstall` → `enabled=false`, later `ensure` silent and untouched; stale path rewritten
      with one line; unrelated keys byte-identical; probe-negative git (fake `git` on PATH) → one line,
      no keys; idempotent under two concurrent runs; path with a space works; path with `'` refused.
- [ ] End-to-end through real git with the guard installed and `CLAUDECODE=1`: `branch -D` unmerged
      refused and survives; merged deletes; rename passes; `update-ref -d` refused; Python subprocess
      refused; both overrides delete; `CLAUDECODE` unset deletes; commit/fetch/rebase/merge succeed;
      **script path removed → commit still succeeds**; pre-push refuses remote-only deletion to a local
      bare remote, allows merged. Measure per-transaction overhead (< 100 ms in Claude).
- [ ] Implement; green + full suite. Commit.

### Task 10: Mutation matrix

**Files:** `.superpowers/sdd/2026-10-29-git-guard/mutations.py` (gitignored; same harness as 0.5.0).

- [ ] Mutations, each must turn its named test red: phase gate removed; holder exclude reverted
      to `{remote}/{name}`; in-transaction holders ignored; one-liner `exit 0` moved after `exec`
      shape (fail-closed); opt-out respect removed; `:` dropped from truncate lookbehind; echo skip
      removed; comment-only ALLOW reverted; Read pdf test inverted; mid-indent whitespace test dropped.
- [ ] All red, engine restored byte-identical (`cmp`).

### Task 11: Docs + version 0.6.0

**Files:** `README.md` (rules table rows, a "git guard" section: what is installed, where, how to see
it in `git hook list`, both overrides, uninstall), `CHANGELOG.md`, `docs/incidents.md`,
`.claude-plugin/plugin.json` (version + description).

- [ ] Write; `bash tests/test_version_consistency.sh` agrees on 0.6.0; full suite green. Commit.

### Task 12: Cross-family review + fix pass

- [ ] Stateless free dispatch (role validator, `avoid_family` of this session's family) with the
      diff and the engine attached; triage findings (verify each by probe), fix TDD-first, re-run matrix.

### Task 13: Ship 0.6.0

- [ ] Pre-merge dogfood from the worktree in a scratch repo (temp `GIT_CONFIG_GLOBAL`).
- [ ] `rtc-ship.sh <the feat/git-guard worktree> 0.6.0 "<title>"`; on a stale-CDN refresh miss,
      re-check once, then `get-haiggoh apply --only watch-your-step`.
- [ ] Back up `~/.gitconfig` (timestamped copy), run the INSTALLED `git-guard ensure`, confirm
      `git hook list --show-scope reference-transaction` shows `global wys-branch-guard`; in a scratch
      repo under this Claude session, `git branch -D unmerged` is refused and a merged one deletes.

### Task 14: Close the record

- [ ] Waypoints done with evidence (git-branch, check, worktree-add, edit-guard; read-pages edited to
      leave its Edit half open), audit-scan, redact-secret over touched files.
