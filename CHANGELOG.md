## [0.7.2] — 2026-10-31

### Changed
- The plugin description (plugin.json, and the marketplace listing it feeds) is rewritten to describe
  the plugin's full current capabilities.

## [0.7.1] - 2026-10-31

- **Fix `path/home-typo` false positive on project-dir arguments**: the `-Users-…` capture class was missing
  the `$ ; : )` exclusions the `/Users-…` alternative has, so a trailing `$` in an arg like
  `--project "<project-dir>$"` was captured INTO the name, which then neither equalled the login user nor
  existed as a directory — a false BLOCK on a legitimate `~/.claude/projects/` dir name (caught live
  2026-10-30 when an `audit-scan --project "<project-dir>$"` call was denied). Regression cases added:
  should-pass for the own-user-`$` arg (mutation-verified red with the old class) and should-flag for a
  near-miss user-`$` arg, so typo detection is unchanged. Suite 308/308.

## [0.7.0] - 2026-10-29

- **Three new warning rules, migrated out of always-on memory** (the lesson stays in `docs/incidents.md`, the
  barrier is the hook): `claude/headless-loads-mcp` (a headless `claude -p` without `--strict-mcp-config`
  loads every MCP server, ~5 min startup), `git/add-all` (`git add -A`/`--all`/`.` stages the user's unsaved
  work), `shell/awk-nr1-batched-find` (`NR==1` in a batched `find -exec awk … {} +` sees only the first file).
- Each rule has should-flag and should-pass cases and was mutation-checked (disabling its pattern turns 4, 5 and
  3 cases red). A replay over 6,136 real Bash commands: 0 hits for the first and third, 7 for `git/add-all`, all
  in throwaway `mktemp` repos, so it is a warning with a documented `# wys:allow git/add-all`.
- Not migrated: a `python3 <script>` whose shebang names another interpreter needs a file read, which the
  pattern engine has no check for yet.
- Docs addendum (commit 4849983, earlier the same day, docs-only): the README h1 carries the
  About-verbatim pair 🛘🥾 per the branding plan's stated default (plugin.json keeps single 🥾
  for stability — that divergence is by design, not drift). Recorded here rather than in its own
  release entry — no version bump.
- Tests: 306 pass.

## [0.6.0] - 2026-10-29

- **New: a git-level branch guard.** A PreToolUse hook only sees the command an agent types, and a branch
  deletion also arrives through `$VAR`, `bash -c`, `git update-ref -d`, scripts and Python subprocesses — all
  measured CLEAN on 0.5.0. Git's own `reference-transaction` hook is the step every route passes through, so
  watch-your-step now installs it (`hook.wys-branch-guard`) plus a `pre-push` guard (`hook.wys-push-guard`) as
  **config-based hooks in the global git config**: they add to a repo's `.git/hooks` and never replace them,
  and `git hook list --show-scope` shows them. `git-guard ensure` runs at session start, installs them once
  with a one-line announcement, refreshes a stale script path, and never touches them again after
  `git-guard uninstall` (kept as `enabled=false`). Active only under Claude (`CLAUDECODE`); overrides:
  `git -c hook.wys-branch-guard.enabled=false …` or `WYS_ALLOW_BRANCH_DELETE=1`. Fails open on every error.
- **Three traps found while building it, all pinned by tests.** Git appends `"$@"` to a one-line hook, so a
  "fail-open" one-liner ending in `exit 0` ran as `exit 0 prepared`, errored, and **aborted an ordinary commit**;
  the hook now ends in a command that ignores arguments. `git branch -m` deletes the old ref BEFORE
  creating the new one, so the refs alone cannot tell a rename from a loss; the parent git's argv decides.
  And outside a repository `git hook list` sees no config hooks at all, so the install's feature probe read
  every git as unsupported for a session started in `~`; it now probes inside a throwaway repo.
- **`git/branch-delete-unrecoverable`:** judges quoted names and every name of a multi-name delete, sees
  through `git -c k=v`, and its push-delete half finally fires: the holder exclusion named `origin/x` while refs
  print as `refs/remotes/origin/x`, so the deleted ref always held itself (dead code since 0.4.2). It queries the
  remote the command names (not always `origin`), with one `ls-remote` per target inside a 4 s budget.
- **New rule `tool/read-pages-non-pdf` (block):** `Read` with `pages` on a non-PDF, which silently returns line 1.
- **New rule `tool/edit-anchor-mid-indent` (block):** an indentation-led `old_string` matching inside a deeper
  indentation, which re-nests the block while targeted tests stay green.
- **Fixes:** `shell/truncate-before-read` no longer fires on `git show HEAD:X > X` or `echo a > a` (the two real
  false positives); the `git stash -u` escape of `worktree-add-dirty` and `discards-uncommitted` stops at a
  newline; a `# wys:allow` marker counts only in a real comment, not inside a commit message; `test` exits 1
  when a check crashes instead of printing "clean".
- Tests: 282 pass, plus a 17-mutation matrix (each safeguard removed alone turns its test red) and a suite-wide
  tripwire that fails the run if the real `~/.gitconfig` changes.

## [0.5.0] - 2026-10-29

- **New rule `git/discards-uncommitted` (block).** `git checkout -- <path>` restores the worktree from the
  index and never refuses — unlike a branch switch, which carries the changes or errors out. A session lost
  uncommitted work to exactly this form (2026-10-28, echoprism: a checkout meant to revert a test mutation
  also discarded the uncommitted backfill in the same file, with `2>/dev/null` hiding the only tell). The
  rule fires when the invocation would actually discard: the pathspec form of checkout/restore when the
  paths carry uncommitted edits (the `<rev> --` form also counts staged-only edits, because the worktree is
  rewritten from the rev), a forced branch switch (`checkout -f`, `switch --discard-changes`) on any dirty
  tree, `git reset --hard` with tracked edits, and `git clean -f` with untracked files (`-x` adds ignored
  ones). It stays silent where nothing is lost: a clean or untracked-only pathspec (git errors rather than
  loses), `restore --staged` (the worktree copy keeps the work), a plain branch switch, a same-command
  `git stash -u`, quoted or variable pathspecs (no verdict — they may hold the trap), and cache-only noise.
- **The mutation matrix found one test gap**: dropping the untracked-row guard stayed green because the
  from-the-index branch already excludes untracked rows via the column test — the guard is load-bearing
  only for the rev form, and that case is now pinned directly. Two real defects were caught by the flag
  tests on the way to green: a "0 paths" detail string is truthy (a cache-only tree fired on
  `checkout -f main`), and `git restore` without `--` never collected its paths. The plugin.json
  description also names `git/branch-delete-unrecoverable` now, which 0.4.2 omitted from it.
- Tests: 193 pass (17 new, plus the two assertions the matrix forced).

## [0.4.2] - 2026-10-28

- **New rule `git/branch-delete-unrecoverable` (block).** `git branch -D` overrides git's own
  unmerged-branch refusal, and `git push --delete` refuses only the branch the *remote's* HEAD
  points at — a protection that stops applying the moment `origin/HEAD` points elsewhere, which is
  the shape of a repo that renamed its default after cloning. Neither case is reachable by the
  existing `worktree` rules, which look at FILES rather than at commit reachability. The rule fires
  when a deletion would leave the commits reachable from no other ref, and never where git itself
  refuses the deletion (a warning over a git refusal is noise, not protection).
  Found while porting run-to-completion's ship cleanup into `rtc-ship.sh`, where the same
  reachability gap is what its own `in_wt` guard exists to close — see its 0.9.0 CHANGELOG.
- **Two defects the new tests caught in the new rule, both found by mutation.** (1) A
  `git([...])` call in each remote helper was missing its `cwd`, so both raised `TypeError` —
  invisible, because `evaluate()` wraps every check in `try/except` and turns a crash into
  "did not fire". A totality test at *that* seam cannot see a crash; it must call the check
  directly, which is what caught it once rewritten. (2) `git branch -D <nonexistent>` fired,
  because an unresolvable revision is indistinguishable from an unrecoverable one; a branch that
  does not exist locally is now no verdict.
- **The remote's HEAD is queried (`git ls-remote --symref`), not read from
  `refs/remotes/origin/HEAD`.** That ref is written by `git clone` and `git remote set-head`, and is
  absent in a `git init` + `git remote add` repo — reading it made the rule treat the remote's own
  answer as the local repo's convention, which silently disabled the default-branch protection.
- Tests: 176 pass. The mutation matrix — each safeguard removed or degraded in turn — is what
  found the two defects above, and then found two more in itself:
  - a **dead branch**: the local "merged into the default branch" escape. If a branch's tip is an
    ancestor of the default branch, the default branch *is* a holder, so `_holders` already covers
    it. Deleting it left the suite green, which is how it was found; removed.
  - a **test passing for the wrong reason**: `push --delete <merged>` was never given a remote
    branch to delete, so `ls-remote` found nothing and the remote path never ran. It passes now,
    and deleting the escape it covers turns it red.
  The remaining seven safeguards are each pinned by a named test, so the suite is green because
  the guards work, not because nothing was exercised.

## [0.4.1] - 2026-10-26

- `git/worktree-remove-ignored` promoted from **warn to block**. It detected the doomed worktree
  correctly and the removal still happened: `bin/watch-your-step.py`'s emit path gives
  `permissionDecision: deny` only for `severity: "block"`, and this rule was `warn`, so it
  produced additionalContext while the destructive call proceeded. Measured on 2026-10-26: a
  session removed a worktree whose `.superpowers/sdd/` plan ledger existed nowhere else — the
  second time this exact loss happened, after the 2026-10-19 incident the rule already cited.
  Five sibling rules (including the other git rule, `git/staged-conflict-markers`) already block,
  so the block path is proven here; this rule was simply never promoted.
- Tests: the compound and piped form that caused the loss now asserts the **deny** is emitted,
  not merely that the rule fires — that second assertion is the one that would have stopped it.
  The matcher needed no change: it already scans the whole command string, verified by running
  the real command shape through the module.
  The matcher was never the problem, and neither was the `.DS_Store` handling: both were
  already correct. What the investigation DID find, by testing them:
  - a directory git collapses (`?? sub/`) was judged on its NAME alone, so a real file
    beside a `.DS_Store` could be missed. Collapsed dirs are now INSPECTED, which also
    fixes `venv/` — the last component is the directory itself, so `parts[:-1]` skipping it
    was wrong for the stash rule's untracked-list filter too (a latent bug this exposed).
  - `--untracked-files=all` names the file directly, but the collapsed route is what would
    carry the rule if that flag were ever dropped, so a test pins THAT route specifically:
    with the flag removed, blinding the directory inspection is caught.

# Changelog

## [0.4.0] - 2026-10-25

- Four warn rules, two of them state-aware (they inspect the repository at hook time like
  the earlier git rules, following `cd X` and `git -C X`):
  - `git/worktree-add-dirty`: `git worktree add` from a checkout with uncommitted files —
    the new tree starts from HEAD, and the message names how many files stay behind
    (builtin checks may now return a detail string that is appended to their message).
  - `git/stash-untracked`: a bare `git stash` / `git stash push` without
    `-u` / `--include-untracked` / `-a` while untracked files exist — they are left in the
    working tree while the stash looks like it saved everything.
  - `secrets/ps-argv`: `ps aux`/`ax` / `-ef` or `-e -f` / `-ww` / `-o args`/`command`/`cmd`
    and `pgrep -a` / `-fl` / `--list-full` — full process arguments can carry API keys from
    a command line into the transcript; the fix names the argv-free forms
    (`ps -o pid,lstart,comm -p <pids>`, `pgrep -x <name>`). Bare `pgrep -l` is NOT flagged:
    it prints the process name only.
  - `zsh/unquoted-var-arglist` (zsh-only, from observation 0046): an unquoted `$var` (or
    `${var}`) passed as an argument to a file-consuming command whose command word it is,
    together with a hidden stderr (`2>/dev/null`, `&>`, `>/dev/null 2>&1`, `2>&-`) — zsh
    passes the whole list as ONE argument and the failure reads as a clean empty result.
    Loop variables (from their loop onward), same-command array assignments, `${=var}`,
    `$arr[@]` and bash scripts are exempt.
- Proven like the earlier rules: red-first cases in `tests/cases.json` (the two stateful
  rules in `tests/test_git_rules.py`, against real temporary repositories), mutation-checked
  (each flag case is caught by its own rule; each new git check monkeypatched to False turns
  its tests red).
- Hardened by a cross-family adversarial review (DeepSeek V4.1 Flash; 20 findings, each
  verified against the engine before fixing): the pgrep `-l` false positive; `ps -e -f` /
  `ps ax` / top-level `$(ps …)` / wrapper (`time`, `sudo -u root`) false negatives; `${var}`,
  bare-array and command-position fixes in the arglist rule; cache-noise matched by path
  component (`venv/` is cache, `myvenv/plan.md` is not); `git stash store` safe; a preceding
  `git stash -u` in the same command accepted; a cd inside `( )` / `$( )` no longer moves the
  resolved repo. Each fix red-first; suite 135 → 158.
- Replay-checked over 1,788 real commands from ten recent transcripts: 27 hits, all true
  catches — 23 argv-revealing ps/pgrep invocations and four runs of the observation-0046
  `grep … $roots 2>/dev/null` probe itself. Zero false positives, and the safe twins in the
  same corpus (`ps -o lstart=`, `pgrep -f` alone, a quoted `"$pat"`, loop variables) stayed
  silent. Five replay-shaped regression cases pin those shapes. Re-replayed after the review
  fixes over the ten most recent transcripts (771 commands): 24 hits, all classified — ten
  argv-revealing ps/pgrep forms (several caught by the new `ax` and `$( )` shapes, from
  concurrent live sessions' real work), a confirmed dirty-source worktree add, and twelve
  pre-existing unmatched-glob aborts; zero false positives, and the widened arglist patterns
  produced no hits.

## [0.3.0] - 2026-10-25

- `zsh/unmatched-glob` (warn, zsh-only): a bare glob that matches nothing aborts the whole
  command line BEFORE anything runs — the only output is "no matches found", and earlier or
  simultaneous stages (including a `||` fallback) silently never execute. State-aware like
  the git rules: it expands each bare glob against the payload's `cwd`, following the
  command's own `cd X` through the same resolver, and stays quiet when the glob matches, so
  it speaks only when zsh would really refuse. `[[ ]]` and `case` patterns, quoted spans,
  variables, brace words, `(N)`-qualified globs and `setopt NULL_GLOB` are all left alone.
- Proven in `tests/test_unmatched_glob.py` against real directories (the stateful-rule
  convention), plus cwd-independent cases in `tests/cases.json`; mutation-checked both ways
  (a never-fire check and a qualifier-blind check each turn the right tests red).
- Replay-checked over 1,059 real commands from four recent sessions: the first pass found
  two false-positive classes (a glob after `cd X`, a `case` pattern), both fixed with
  replay-shaped regression cases; every remaining hit is either a true catch of the exact
  incident shape (a `2>/dev/null`-guarded probe whose `||` fallback zsh never runs) or a
  correct verdict against today's filesystem.
- `rtc-ship.json` added so `rtc-ship.sh` can ship this repo (its own
  `test_version_consistency.sh` plus the pytest suite as the gate).

## [0.2.0] - 2026-10-24

- Two state-aware git rules. They inspect the repository at hook time instead of matching text, following
  `cd X` and `git -C X`:
  - `git/staged-conflict-markers` (block): a commit or `rebase/merge/cherry-pick --continue` that would record
    `<<<<<<<` / `>>>>>>>` lines, including files that a `git add` earlier in the SAME command is about to stage.
  - `git/worktree-remove-ignored` (warn): `git worktree remove` on a worktree that holds gitignored files other
    than caches, which removal deletes for good.
- Tested against real temporary repositories (`tests/test_git_rules.py`), including a mutation check. A check
  that cannot decide (no git, a quoted path, a timeout) never blocks.

## [0.1.1] - 2026-10-24

- `docs/incidents.md`: the real incident behind each rule, moved here from the author's notes so the rule
  and its evidence live in one place. No behaviour change.

## [0.1.0] - 2026-10-24

First release. A PreToolUse hook that checks each Bash command, and each Read/Write/Edit path, before it runs.

- Eight rules as data (`rules/rules.json`). Four BLOCK silent-corruption traps: a backtick inside double quotes
  or an unquoted heredoc, a mistyped home directory, a file truncated before it is read, and zsh's
  `====` / `==` EQUALS expansion. Four WARN: `"$40"` in quoted prose, `PIPESTATUS` under zsh, `grep -F '^…'`,
  and an unsplit `for f in $var` under zsh.
- A shell-aware tokenizer instead of a raw regex. Single-quoted text, quoted heredocs, comments and
  `"$(cat <<'EOF' … EOF)"` commit messages are read as literal, and a subshell inside double quotes is
  parsed as its own command line.
- Each rule has should-flag and should-pass cases, plus a mutation test proving each flag case is caught by
  its own rule. The engine was replayed over 2,184 real tool calls with no false block.
- Visible overrides: `# wys:allow <rule-id>` on one command, or `WATCH_YOUR_STEP_DISABLE`. The hook fails
  open on its own errors.
- `watch-your-step.py test '<command>'`, `rules`, `--help`.
