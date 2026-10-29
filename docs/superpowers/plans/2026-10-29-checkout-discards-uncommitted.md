# watch-your-step: a guard for git commands that discard uncommitted work

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one stateful git rule, `git/discards-uncommitted` (block), that refuses `git checkout`,
`git switch`, `git restore`, `git reset --hard` and `git clean -f` when the invocation would silently
discard uncommitted changes in the repo the command itself runs in.

**Architecture:** One new builtin check `check_discards_uncommitted` bound through `CHECKS` and
declared in `rules/rules.json`. Like its siblings it parses the *bare* view (quoted spans collapsed,
comments stripped — so `# wys:allow` works by construction), resolves the repo via `resolve_dir`
(following `cd X` and `git -C X`), reads real repo state through the existing `git()` helper, and
returns a count detail string in the `worktree_add_dirty` style. Severity is `block`: 0.4.1 already
learned that a warn on this loss class does not stop it.

**Tech Stack:** Python 3 (stdlib only), pytest, the repo's existing check protocol.

**Spec:** The user-approved design in session 3b501f28 (2026-10-28): scope = checkout/restore + forced
switch + reset --hard + clean -fd in ONE rule; severity = block; ship through merge + tag v0.5.0 +
refreshed install. Incident measured from the 2026-10-28 echoprism session transcript (session
`a4f07e93`, the project dir for the echoprism checkout); the loss command was
`git checkout -- src-tauri/src/db/queries.rs 2>/dev/null`.

## Global Constraints

- Work in a worktree (`~/ClaudeWorkspace/.wt/checkout-discards`, branch `feat/discards-uncommitted`);
  `main` in the canonical checkout stays clean and checked out.
- Stage by path; never `git add -A`. Commit trailer order: Claude Opus 5.5 line, then
  `Co-Authored-By: GLM 5.3 <noreply@z.ai>` (this session's running model).
- The check must never raise: the hook fails open, so an exception reads as "allow". Every input it
  cannot judge returns no verdict (False), never a crash.
- Version 0.4.2 → 0.5.0 in BOTH surfaces (`plugin.json`, top CHANGELOG entry); `tests/test_version_consistency.sh` must pass.
- No new dependencies; stdlib only, like the rest of the engine.

## Review Focus

1. **The incident's compound shape** — `cd <repo> && git checkout -- <file> 2>/dev/null; …`: the
   redirect token must not corrupt the path parse, and the rule must see the checkout mid-compound.
   Pinned by `test_the_incident_compound_form_fires`.
2. **Staged-only vs unstaged distinction** — `git checkout -- f` on a file with ONLY staged changes
   must NOT fire (the worktree copy holds the work); `git checkout HEAD -- f` on the same state MUST
   (the worktree is rewritten from the rev). Pinned by `test_staged_only_index_restore_passes`.
3. **Untracked-only dirt never fires the path form** — `git checkout -- <untracked>` errors, it does
   not lose; only `??` rows must be excluded from the path-form loss set. Pinned by
   `test_untracked_only_path_restore_passes`.
4. **Unresolvable pathspecs give no verdict, not a crash** — quoted (`Q`) or `$var` paths skip the
   match; the totality test calls the check directly because `evaluate` launders exceptions into
   "did not fire". Pinned by `test_the_check_is_total_and_never_raises`.
5. **Cache-only noise stays silent** — a tree whose only dirt is `.DS_Store`/`__pycache__` must not
   block any of the five commands. Pinned by `test_cache_only_dirt_stays_silent`.

---

### Task 1: The failing tests

**Files:**
- Modify: `tests/test_git_rules.py` — add `"git/discards-uncommitted"` to `TESTED`; append a section
  following the file's fixture style (`repo` fixture, `fired()`, `sh()`)

**Interfaces:**
- Consumes: the existing `repo` fixture and `fired(cmd, cwd, rule)` helper — do not invent a second harness.
- Produces: nothing yet; every test must FAIL against current code.

- [ ] **Step 1: Write the tests** — each names the expected verdict:

  - `test_checkout_dash_dash_on_dirty_path_blocks`: tracked `wip.txt` modified →
    `git checkout -- wip.txt` fires.
  - `test_the_incident_compound_form_fires`:
    `cd {repo} && git checkout -- wip.txt 2>/dev/null; git status --porcelain -- wip.txt` fires.
  - `test_checkout_rev_form_blocks_staged_only`: file staged, worktree clean →
    `git checkout HEAD -- wip.txt` fires.
  - `test_staged_only_index_restore_passes`: same state → `git checkout -- wip.txt` does NOT fire;
    `git restore --staged wip.txt` does NOT fire.
  - `test_restore_forms_block_unstaged_dirt`: `git restore wip.txt` and
    `git restore --source=HEAD wip.txt` fire; `git restore wip.txt` on a clean path does not.
  - `test_forced_switch_blocks_dirty_tree`: `git checkout -f main`, `git switch --discard-changes main`
    fire; `git checkout main` and `git checkout -b feature` do not.
  - `test_reset_hard_blocks_tracked_dirt`: staged OR unstaged → `git reset --hard` fires; clean →
    no; untracked-only → no.
  - `test_clean_force_blocks_untracked`: `git clean -fd` and `git clean -f` fire; `git clean -n`,
    bare `git clean` do not.
  - `test_stash_u_escape_in_same_command_passes`: `git stash -u && git checkout -- wip.txt` does not.
  - `test_untracked_only_path_restore_passes`: `wip.txt` untracked → `git checkout -- wip.txt` no fire.
  - `test_unresolvable_paths_give_no_verdict`: `git checkout -- "$f"` and `git checkout -- $f` no fire.
  - `test_cd_and_dash_C_are_followed`: both forms fire from another cwd.
  - `test_cache_only_dirt_stays_silent`: only a `.DS_Store` dirty → none of the five commands fire.
  - `test_a_fired_discard_hit_denies_rather_than_comments` (distinct name — sibling lesson): emit
    path asserts `permissionDecision == "deny"`.
  - `test_the_discard_check_is_total_and_never_raises`: call `wys.CHECKS["discards_uncommitted"]`
    DIRECTLY (the `evaluate` seam launders exceptions) over malformed commands in a repo and a
    non-repo — must return False, never raise.
  - `test_mutation_discards_check_is_what_fires`: monkeypatch the CHECKS entry → not fired.

- [ ] **Step 2: Run and confirm RED**

Run: `pytest -q tests/test_git_rules.py -k discards`
Expected: FAIL — the rule id does not exist in `rules/rules.json` yet. GREEN here means the test is
not wired to the new rule.

### Task 2: Implement the check

**Files:**
- Modify: `bin/watch-your-step.py` — add `check_discards_uncommitted` + helpers, register in `CHECKS`
- Modify: `rules/rules.json` — declare `git/discards-uncommitted`
- Test: `tests/test_git_rules.py`

**Interfaces:**
- Consumes: `git(args, cwd)`, `resolve_dir(before, git_c, cwd)`, `dirty_paths(d, untracked_only)`,
  `_is_cache_path(p)`, `subshell_end` semantics already in the engine.
- Produces: `check_discards_uncommitted(texts, bare_view, cwd=None, **_) -> bool | str` — a count
  detail string when it fires, False otherwise. `CHECKS["discards_uncommitted"]`.

- [ ] **Step 1: Implement the matcher and classification**

Match `git [-C X] (checkout|switch|restore|reset|clean) <args-to-next-;&|>` per segment of
`bare_view`. Per match, in order:

1. `d = resolve_dir(...)`; None → skip.
2. Same-command `git stash [-u|-a]` earlier in the command → skip (mirror `worktree_add_dirty`).
3. Drop redirect tokens (`2>/dev/null`, `>f`, `>>f`, `2>`) from the args before parsing.
4. Classify:
   - **checkout/switch, force, no pathspec** (`-f`/`--force`/`--discard-changes`, no `--`, no
     positional): loss = all `dirty_paths(d)`; fire when non-empty.
   - **pathspec form** (`--` present, or restore/checkout positionals): paths = tokens after `--`
     (or all non-flag positionals). ANY token containing `Q` or `$` → skip the whole match (no
     verdict). Run `git status --porcelain -- <paths>`; `None` → skip. Written-trees rule:
     index+worktree written (checkout with a rev before `--`, or restore `--source` with
     `--worktree`/default) → an entry counts when X or Y is a real status (not `?`, not space);
     worktree written only (checkout `--` with no rev, restore without `--staged`, `--source` with
     `--worktree` only) → only Y counts; index written only (`restore --staged`) → never fires —
     the worktree copy preserves the work (document WHY in a comment).
   - **reset --hard**: fire when any non-`??`, non-cache porcelain entry exists.
   - **clean**: fire when a force flag is present and no `-n`/`--dry-run`; loss =
     `dirty_paths(d, untracked_only=True)`; with `-x` also count non-cache `!!` rows.
5. Return `f"{n} uncommitted path{'s' if n != 1 else ''} would be discarded."` when firing.

- [ ] **Step 2: Register and declare it**

`CHECKS["discards_uncommitted"] = check_discards_uncommitted`. The `rules.json` entry: `id`
`git/discards-uncommitted`, `severity` `block`, `stateful` `true`, `tools` `["Bash"]`, `shells` `[]`,
`context` `["raw"]`, `check` `discards_uncommitted`, and:

- message: `this git command discards uncommitted changes instead of refusing: git checkout/git restore replace working-tree files from the index or a revision, git reset --hard drops staged and unstaged edits, and git clean -f deletes untracked files — each exits 0.`
- fix: `commit the work, or park it first (git stash -u, or a WIP branch — a branch is more visible); when discarding really is intended, end the command with  # wys:allow git/discards-uncommitted`
- incident: `2026-10-28, echoprism Task 10: a git checkout -- src-tauri/src/db/queries.rs 2>/dev/null meant to revert a test mutation also discarded the uncommitted backfill work in the same file; the loss surfaced only when the work had to be redone.`

- [ ] **Step 3: Run and confirm GREEN**

Run: `pytest -q tests`
Expected: all existing + new tests pass, including `test_every_stateful_rule_has_state_based_tests`.

### Task 3: Prove each escape can FAIL

**Files:** none (mutations are manual, reverted after; snapshot the file first)

- [ ] Mutate one at a time, watch the named test go red, restore, `cmp` against the snapshot:
  remove the stash escape → `test_stash_u_escape_in_same_command_passes` red; count `??` rows in the
  path form → `test_untracked_only_path_restore_passes` red; treat index-only restore as firing →
  `test_staged_only_index_restore_passes` red; drop the cache filter on the clean/reset paths →
  `test_cache_only_dirt_stays_silent` red; make an unresolvable path return False-for-the-match
  only → `test_unresolvable_paths_give_no_verdict` red.
- [ ] Full suite green after restore.

### Task 4: Docs and version 0.5.0

**Files:** Modify: `README.md` (rules table row + state-aware paragraph), `docs/incidents.md` (new
section `## git/discards-uncommitted`), `.claude-plugin/plugin.json` (version 0.5.0; description
gains the new trap phrase), `CHANGELOG.md` (top entry `## [0.5.0] - 2026-10-29`, modelled on the
0.4.2 entry: what it blocks, why block, what the mutations caught)

- [ ] **Step 1:** write the four surfaces; keep the README table row one line.
- [ ] **Step 2:** `bash tests/test_version_consistency.sh` → versions agree: 0.5.0. Full suite green.
- [ ] **Step 3:** commit (by path).

### Task 5: Ship through merge + tag + refreshed install

**Files:** none (workflow)

- [ ] **Step 1:** pre-flight: confirm `~/.local/bin/pytest` has pytest-xdist (rtc-ship.json's gate
  uses `-n 12`); if absent, install via pipx/uv BEFORE shipping, or the gate fails mid-loop.
- [ ] **Step 2:** dogfood pre-merge from the worktree: `bin/watch-your-step.py test` over the
  incident command and a clean checkout; confirm block vs silence.
- [ ] **Step 3:** `rtc-ship.sh <worktree> 0.5.0 "<title>"` (locate it first: `command -v rtc-ship.sh`
  or run-to-completion's plugin bin). Verify tag visibility with `get-haiggoh plan` (re-check once
  after ~60 s before treating a negative as real).
- [ ] **Step 4:** refresh the installed copy (`claude plugin update watch-your-step@haiggoh`) and
  confirm a cache dir for 0.5.0 exists under the plugin's installed path (resolve the NEWEST
  `watch-your-step/*` at call time — never a pinned version); dogfood the NEW rule through the
  INSTALLED binary (`…/bin/watch-your-step.py test 'git checkout -- f'` against a
  scratch dirty repo → block; clean repo → silent).

### Task 6: Close the record

- [ ] **Step 1:** session-level reconciliation (audit-scan, waypoints) — the task's own evidence:
  commit sha, test count, shipped version, live confirmation.
