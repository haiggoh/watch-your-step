# watch-your-step: a branch-deletion guard for commits that would be unrecoverable

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `git` rule that blocks `git branch -D/-d` and `git push origin --delete` when the deletion
would leave the branch's commits reachable from **no other ref** — the hazard watch-your-step's
existing `worktree remove`/`worktree add` rules do not cover.

**Architecture:** A new stateful check `check_branch_delete_unrecoverable` bound through `CHECKS` and
declared in `rules/rules.json` with `severity: "block"` — matching the sibling git rule
`git/staged-conflict-markers` and the promotion `0.4.1` just made to `git/worktree-remove-ignored`.
The predicate is reachability: a deletion is safe when the tip is an ancestor of the default branch,
or when some other ref (local branch or remote-tracking) already contains it. It deliberately does
NOT fire where git itself refuses the deletion, because that would be a warning over a refusal.

**Tech Stack:** Python 3, pytest, the repo's existing `bin/watch-your-step.py` check protocol.

**Spec:** 🧭 waypoint `watch-your-step-guard-branch` (created 2026-10-27 from run-to-completion's
`rtc-ship.sh` 0.9.0 work), plus the measured evidence recorded in it.

## Global Constraints

- **Built on `fix/block-worktree-remove-ignored`** (0.4.1, verified: 165 tests green, version surfaces
  agree, no WIP). That branch is shipped FIRST, unchanged, as 0.4.1 — this change becomes 0.4.2 on
  top of the published 0.4.1. Do not fold the two into one release.
- Rules live in `rules/rules.json`; the function lives in `bin/watch-your-step.py` and is registered
  in `CHECKS`. A rule without its `CHECKS` entry is dead.
- `severity: "block"` — a destructive, non-trivially-recoverable operation. A `warn` here is the
  exact defect 0.4.1 fixed in the sibling rule (face 20 of memory [[functional-verification]]).
- The check receives `(texts, bare_view, cwd)` and returns bool. It must not read anything outside
  the repo at `cwd`, and must fail **closed** — an exception means "cannot tell", so the hook must
  not treat a crash as a pass.
- The default branch is resolved from `origin/HEAD`, falling back to `main`, then `master`. Never a
  hardcoded single name (echoprism ships `master`).
- Do NOT fire where git already refuses: `git branch -d/-D` will not delete a branch checked out in
  another worktree, and `git push --delete` will not delete the branch the remote's `HEAD` points
  at. Those are belt-and-braces only; record that in a comment so nobody reads them as covered.

## Review Focus

For each line: the test that pins it, in the owning task's step style.

1. **`git branch -D <unmerged>` with the commits nowhere else** — the primary case; must block.
2. **`git branch -D <merged>`** must NOT block (a normal cleanup would otherwise be unusable).
3. **`git push origin --delete <default>` when `origin/HEAD` points elsewhere** — the case git does
   not protect; must block. Proven recoverable-only-by-re-push.
4. **A branch whose tip another ref also holds is NOT unrecoverable** — must not block.
5. **A check that CRASHES must not become a pass** — the hook fails open by design, so a None-path
   or bad argv must be pinned.

---

### Task 1: The failing tests

**Files:**
- Modify: `tests/test_git_rules.py` (append a section for the new rule, following the file's fixtures)

**Interfaces:**
- Consumes: the existing `repo`/`wt` fixtures and the `evaluate`-based assertions used by
  `test_worktree_remove_ignored`'s tests (read them first — do not invent a second harness).
- Produces: nothing yet; every test must FAIL against current code.

- [ ] **Step 1: Write the tests** — six cases, each naming the expected verdict:

  - `test_branch_D_of_unmerged_blocked`: a branch with a commit not on the default, held nowhere
    else → `git branch -D topic` → verdict `block`.
  - `test_branch_D_of_merged_allowed`: a branch whose tip is an ancestor of the default →
    `git branch -D topic` → no hit.
  - `test_branch_d_not_blocked_when_duplicated`: tip contained in another local branch or in
    `origin/topic` → no hit.
  - `test_push_delete_default_branch_blocked_when_head_points_elsewhere`: `origin/HEAD` set to an
    unmerged decoy, `git push origin --delete main` → `block`. (This is the shape the rtc-ship
    `u11` rehearsal proved: git only refuses the branch `HEAD` points at.)
  - `test_push_delete_merged_allowed`: merged topic branch on the remote → no hit.
  - `test_a_crash_does_not_become_a_pass`: the check raises on a malformed command / unreadable repo
    → the caller still fails closed. Pin it at the `evaluate` seam, not by catching the exception
    inside the check.

- [ ] **Step 2: Run and confirm RED**

Run: `pytest -q tests/test_git_rules.py -k branch_delete or push_delete`
Expected: FAIL — the rule id does not exist in `rules/rules.json` yet. A GREEN here means the test
is not wired to the new rule.

### Task 2: Implement the rule

**Files:**
- Modify: `bin/watch-your-step.py` — add `check_branch_delete_unrecoverable`, register it in `CHECKS`
- Modify: `rules/rules.json` — declare `git/branch-delete-unrecoverable`
- Test: `tests/test_git_rules.py`

**Interfaces:**
- Consumes: `git()` helper, `split_command`, the `evaluate` loop.
- Produces: `check_branch_delete_unrecoverable(texts, bare_view, cwd=None, **_) -> bool`.

- [ ] **Step 1: Implement the matcher and predicate**

Signature: `check_branch_delete_unrecoverable(texts, bare_view, cwd=None, **_)`. Body determines:
(1) which deletion form was used and the branch name(s); (2) whether it is local
(`git branch -d/-D`) or remote (`git push ... --delete` / `-d` / `:name`); (3) the default branch;
(4) whether the commits survive elsewhere.

Precedence, so a crash cannot read as a pass: every step that cannot resolve (not a git repo,
unknown branch, unreadable origin) returns **False** only when the command is provably not a
deletion, and **True** when it is a deletion of something whose reachability cannot be established.

- [ ] **Step 2: Register and declare it** — `CHECKS["branch_delete_unrecoverable"]` and the
`rules.json` entry with `severity: "block"`, `stateful: true`, `context: ["raw"]`, `check:
"branch_delete_unrecoverable"`, plus `message` (what is lost), `fix` (how to check before deleting:
`git branch -a --contains <sha>`), and `incident` (the measured origin/HEAD divergence case).

- [ ] **Step 3: Run and confirm GREEN**

Run: `pytest -q tests` — 165 existing + the new ones, all passing.

### Task 3: Prove each guard can FAIL

**Files:** none (mutations are manual, reverted after)

- [ ] **Step 1–N: mutate, watch red, restore** — one at a time, each with the named test that must
go red: remove the "merged" escape; remove the "contained elsewhere" escape; remove the `origin/HEAD`
resolution (hardcode `main`) and watch the `master`-shaped case fail; make the check return False on
an unreadable repo and watch the fail-closed test fail.

Run after all: `pytest -q tests` → 0 failures, and `cmp` the restored file against the snapshot.

### Task 4: Ship 0.4.1 — the verified work that is already sitting unreleased

**Files:** none (workflow only). `fix/block-worktree-remove-ignored`, 165 tests green.

- [ ] **Step 1: dogfood pre-merge** — run the installed copy? No: the installed copy is 0.4.0, which
  does not carry the fix. Dogfood by exercising the branch's rules through `evaluate` on the real
  command shape (already covered by its tests) plus one live run of `bin/watch-your-step.py --help`
  to confirm the shipped entry point parses.
- [ ] **Step 2: ship** `bin/rtc-ship.sh <worktree> 0.4.1 "<title>"`.
- [ ] **Step 3: verify the tag is visible** — `get-haiggoh plan`; re-check once after ~60 s before
  treating a negative as real.
- [ ] **Step 4: confirm the installed copy is 0.4.1** and that the rule is now `block` there.

### Task 5: Ship 0.4.2 — the new rule

- [ ] **Step 1:** rebase this worktree's branch onto the published 0.4.1 `main`.
- [ ] **Step 2:** bump `.claude-plugin/plugin.json` to `0.4.2` and add the CHANGELOG entry naming
  what it blocks and the incident it came from.
- [ ] **Step 3:** `bash bin/rtc-ship.sh <this worktree> 0.4.2 "<title>"`, then verify visibility, the
  installed copy, and the rule's presence there.

### Task 6: Close the waypoint

- [ ] **Step 1:** `waypoints.py done watch-your-step-guard-branch --evidence "watch-your-step 0.4.2
  adds git/branch-delete-unrecoverable (block); 0.4.1 shipped first. commit <sha>, N tests green"` —
  and note in the same call that the *separate* `truncate-before-read` false positive recorded on
  the item still stands (split it out rather than closing it under this one).
