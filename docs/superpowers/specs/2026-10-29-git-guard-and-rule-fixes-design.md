# watch-your-step: a git-level branch guard + the confirmed rule fixes — design

Date: 2026-10-29 · Repo: haiggoh/watch-your-step · Base: v0.5.0 (24d4e9c)
Waypoints addressed: `watch-your-step-git-branch`, `watch-your-step-check`
(the reference-transaction half of `decide-mutual-test-fixture-vs` option O6).

## Intent

watch-your-step is a PreToolUse hook: it judges the command line an agent TYPES. For branch
deletion that is structurally too weak. It was measured missing quoted names, multi-name deletes,
`$VAR`, `$(…)`, `bash -c`, `git update-ref -d`, scripts and Python subprocesses, and its
push-delete half never fires. Git has a choke point that every one of those routes passes through:
the `reference-transaction` hook. This release adds a **git-level guard** at that choke point,
plus a `pre-push` guard for remote deletions, and fixes the confirmed defects of the command-line rules.

Success = an unmerged branch's commits cannot be lost through a deletion started from a Claude
session, by any route, while ordinary git work stays exactly as fast and as reliable as before.

## Decisions taken by the user (2026-10-29)

1. **Activation: auto-install, announced.** The guard installs itself on session start (no command
   to remember) and says so in one line, including how to remove it.
2. **Coverage: only under Claude.** The guard acts only when the git process descends from a
   Claude Code session. A hand-typed `git branch -D` in a plain terminal is untouched.
3. **Override: both** git's native per-command switch and an env var.
4. **Remote: yes**, a `pre-push` guard for remote branch deletion, in the same install.
5. Scope: everything on the two waypoints, including the hook. Shipped through the full loop
   (merge, tag, release, refreshed install, dogfood) as ONE release.

## Measured facts this design rests on (git 2.56.0, macOS)

- **Config-based hooks exist and are additive.** `hook.<name>.event` + `hook.<name>.command` in
  `~/.gitconfig` fire in every repo and do not disable a repo's own `.git/hooks`, unlike a global
  `core.hooksPath`. `git hook list --show-scope reference-transaction` shows `global wys-…`, or
  `global disabled wys-…` when `hook.<name>.enabled=false`: git's own UI reflects the exact state.
- **A global reference-transaction hook blocks `git branch -D`.** Exiting non-zero in the `prepared`
  phase aborts it (exit 128) and the branch survives. The same holds for `update-ref -d`, `$VAR` and
  a Python subprocess (measured 2026-10-28).
- **For `git branch -D`, git passes old = zeros**; `branch -m` passes the real old value. The hook
  must resolve the tip itself (`git rev-parse refs/heads/<n>` in `prepared` — the ref still exists).
- **Git APPENDS `"$@"` to a one-line hook command.** A one-liner ending in `exit 0` therefore runs as
  `exit 0 preparing`, which errors, and **git aborts the transaction: an ordinary commit failed
  this way in the probe.** Every early exit must come before a final `exec … ` that absorbs the
  appended arguments. Git does not pass the event name, so each event gets its own friendly name.
- **A hook that exits without reading stdin is safe** (200-ref `update-ref --stdin` batch, exit 0).
- **`git branch -m feat feat2`** surfaced only the deletion of `refs/heads/feat` to the hook.
  The rename must pass (see tests); do not assume the transaction shape.
- **`CLAUDECODE=1`** is set in the Claude Code Bash environment and inherited by child bash and
  Python processes.
- No repo under `~/ClaudeWorkspace` (31 scanned) has a non-sample git hook. No `core.hooksPath` is
  set at any scope, so the install has nothing to collide with on this machine.

## Part A — fixes to the existing command-line rules (all confirmed by probe on v0.5.0)

| # | Defect | Required behaviour |
|---|---|---|
| A1 | `git branch -D "x"` / `'x'` passes (quoted names skipped) | unquote simple quoted names, then judge them; names containing `$`/backticks stay no-verdict |
| A2 | `git branch -D merged unmerged` passes (only the first name judged) | judge EVERY name; block if any is unrecoverable |
| A3 | `git push origin --delete lonely` / `:lonely` pass: `_holders` excludes `origin/<n>` but `git branch -a --contains` prints `remotes/origin/<n>`, so the ref being deleted always holds itself | exclude `remotes/<remote>/<name>`; both forms must block a remote-only branch; deleting the remote default branch stays blocked (existing behaviour, keep it) |
| A4 | `# wys:allow <id>` anywhere in the raw command disables the rule, even inside a commit message | the marker counts only as a trailing comment of the command: outside any quoted string, at the end of a line |
| A5 | `test` mode prints "clean" when a check raises; a typo'd check id silently disables a rule | `test` mode prints the exception to stderr and exits non-zero; a suite test asserts every rule's `check` exists in `CHECKS`. Hook mode stays fail-open |
| A6 | push-delete: up to two `ls-remote` calls of 3 s each per refspec can exceed the 10 s hook timeout, and then the harness kills the hook and the command runs unevaluated | ONE `ls-remote --symref <remote> HEAD refs/heads/*` per remote per evaluation, cached; total network budget ≤ 4 s, over budget = no verdict |
| A7 | `shell/truncate-before-read` false-positives when the target is read or merely named in an EARLIER command of a `;`/`&&`/`\|\|`/newline chain. Real incidents: `cp X /tmp/X.new && git show HEAD:X > X` (twice, 2026-10-27) and `echo a > a && git add -A && git commit -qm a` | judge reads only within the SAME pipeline as the redirect (`cat X \| sort > X`, `sort X > X` must still block) |
| A8 | the branch-delete matcher handles `git -C dir` but not `git -c k=v` prefixes | accept any number of `-C <dir>` / `-c <k=v>` global options before the subcommand |
| A9 | the command-line rule knows neither override of Part B | `WYS_ALLOW_BRANCH_DELETE=1 git …` and `git -c hook.wys-branch-guard.enabled=false …` (pre-push: `wys-push-guard`) also count as a deliberate override for `git/branch-delete-unrecoverable` |

## Part B — the git-level guard

### B1. `watch-your-step.py git-hook ref <phase>` (reference-transaction)

- Acts only in phase `prepared`; any other phase exits 0 immediately.
- For each stdin line `<old> <new> <ref>` with `new` = zeros and `ref` under `refs/heads/`:
  resolve the tip, then ask whether that commit is reachable from any OTHER ref: local heads other
  than the ones being deleted in this transaction, tags, `refs/remotes/*`. The one predicate is
  shared with the command-line rule, so both layers agree on what "unrecoverable" means.
- Refs that THIS transaction creates or updates (`new` ≠ zeros) count as holders, so a rename or a
  delete-and-recreate inside one transaction never blocks.
- Unrecoverable → print the refusal to stderr (what was refused, why, both override forms, verbatim)
  and exit 1. Anything else → exit 0.
- **Fail-open on every internal error**: an exception, a missing git, a timeout → one stderr note,
  exit 0. Only a positive "unrecoverable" verdict refuses.

### B2. `watch-your-step.py git-hook push <remote> <url>` (pre-push)

- stdin lines `<local ref> <local oid> <remote ref> <remote oid>`; a deletion has local oid = zeros.
- For a deleted `refs/heads/<n>`: is `<remote oid>` present locally and reachable from any ref
  other than `refs/remotes/<remote>/<n>`? Yes → allow. No → refuse (exit 1), same message style.
  An object absent locally means the commits exist nowhere here: that is a refusal, not an error.
- Deleting the remote's default branch: refuse (mirrors A3's kept behaviour).
- Same fail-open policy as B1 for errors.

### B3. The command git runs (the one-liner written into ~/.gitconfig)

```sh
[ -n "$CLAUDECODE" ] || exit 0; [ "${WYS_ALLOW_BRANCH_DELETE:-}" = 1 ] && exit 0; [ "$1" = prepared ] || exit 0; f='<abs path>/bin/watch-your-step.py'; [ -f "$f" ] || exit 0; exec python3 "$f" git-hook ref
```

(the push variant omits the phase test and ends `git-hook push`). The shape is load-bearing: early
exits first, the `exec` last so git's appended `"$@"` lands on it. Outside Claude, in any phase but
`prepared`, and when the plugin path has vanished, Python never starts. A path containing a single
quote is not installed: report it, never mis-quote it.

### B4. Install / update / remove — `watch-your-step.py git-guard ensure|install|uninstall|status`

- `ensure` runs from a new SessionStart entry in `hooks/hooks.json`. It writes ONLY its own keys,
  via `git config --global` (so `GIT_CONFIG_GLOBAL` redirects it in tests):
  `hook.wys-branch-guard.{event=reference-transaction,command}` and
  `hook.wys-push-guard.{event=pre-push,command}`.
  - Feature probe, not a version check: if `git -c hook.wysprobe.event=reference-transaction
    -c hook.wysprobe.command=true hook list reference-transaction` does not list `wysprobe`, do
    nothing and say once that this git cannot host the guard.
  - Keys absent → install + one announce line:
    `🥾 watch-your-step: installed a git branch-deletion guard in ~/.gitconfig (hook.wys-branch-guard, hook.wys-push-guard) — remove: watch-your-step.py git-guard uninstall`.
  - Keys present with `enabled=false` → **the user opted out: never touch them, print nothing.**
  - Keys present, command points at another plugin version's path → rewrite the command to the
    current path, one short line saying so.
  - Already current → print nothing. Any failure (read-only config, sandbox) → one line, exit 0.
- `uninstall` sets `hook.wys-*.enabled=false` (kept, so `git hook list` shows `disabled` and `ensure`
  respects it). `install` sets them back to true and refreshes the commands. `status` prints git's
  own `git hook list --show-scope` for both events plus the resolved script path and whether it exists.
- Every change is visible in git's own UI and documented in the README (no-hidden-changes).

### B5. Overrides (both, per the user)

- Native: `git -c hook.wys-branch-guard.enabled=false branch -D x`,
  `git -c hook.wys-push-guard.enabled=false push origin --delete x`.
- Env: `WYS_ALLOW_BRANCH_DELETE=1 git …` (covers both hooks).
- The refusal message prints both forms with the real branch name. The command-line rule accepts
  the same forms (A9).

## Non-goals

`git worktree remove` (not a ref). Protecting tags. Reflog-based recovery or `gc` settings. GitHub
rulesets. Guarding a plain terminal outside Claude (decision 2). Detecting `CLAUDECODE` stripped by
a script that runs `env -i` (accepted gap).

## Anti-goals (a failure even if the guard "works")

- Any state in which the hook makes an ordinary commit, fetch, rebase, pull or merge fail or
  visibly slow down, in or out of Claude. Outside Claude the cost must be one shell test; inside
  Claude, Python starts only for `prepared` and stays under ~100 ms per transaction.
- Touching any git config key that is not `hook.wys-*`.
- Re-installing after an `uninstall`.
- Tests that read or write the real `~/.gitconfig`.

## Testing requirements (the builder writes them; these are the behaviours they must pin)

- Every case in Part A as a must-block / must-pass pair, using the real incident commands for A7.
- End to end through a REAL git with the guard installed into a temp `GIT_CONFIG_GLOBAL` and
  `CLAUDECODE=1`: `branch -D` of an unmerged branch refused and the branch survives; merged branch
  deletes; rename passes; `update-ref -d` refused; a Python-subprocess deletion refused; both
  overrides work; with `CLAUDECODE` unset the unmerged branch deletes; a plain commit, fetch,
  rebase and merge succeed with the guard installed; **with the script path removed, a commit still
  succeeds** (the fail-closed regression); `pre-push` refuses deleting a remote-only branch on a
  local bare remote and allows a merged one.
- `ensure`: fresh install announces; second run silent; after `uninstall` it stays disabled and
  silent; a stale path gets rewritten; non-wys keys are byte-identical before and after.
- A session-wide fixture pins `GIT_CONFIG_GLOBAL` to a temp file for EVERY test, plus a tripwire
  that fails the suite if the real `~/.gitconfig` changed.
- Mutation matrix as in 0.5.0: each load-bearing guard (phase gate, holder exclusion, in-transaction
  holders, fail-open wrapper ordering, opt-out respect) removed one at a time must turn a named
  test red.

## Builder-check resolutions (local Laguna S 2.1, 2026-10-29) — all my calls, none needed the user

- **CLI wiring:** `git-hook` and `git-guard` are new top-level subcommands in `main()` (the
  `check`/`test`/`rules` pattern), each listed in `USAGE`; `git-guard` takes
  `ensure|install|uninstall|status` as its second argument and rejects anything else non-zero.
- **SessionStart command:** `python3 "${CLAUDE_PLUGIN_ROOT}/bin/watch-your-step.py" git-guard ensure`,
  timeout 10. `ensure` writes the path it computes from `os.path.realpath(__file__)` into the one-liner.
- **Shared predicate:** extract one pure function (repo dir, tip oid, refs to exclude, extra holder
  oids) that both `check_branch_delete_unrecoverable` and the hook handlers call. The pre-push handler
  feeds it fields already parsed from stdin, with no `ls-remote` (git hands it the remote oid).
- **`CLAUDECODE` reaches git's hook processes — MEASURED:** 27 hook invocations (reference-transaction
  and pre-push) all saw `CLAUDECODE=1`; with it removed (`env -u`), the hook saw it unset. Note the
  count: a handful of git commands fired 27 transactions, so the outside-`prepared` fast path matters.

## Left to the builder

Internal function names and module layout; the refusal wording beyond the required content; how
`git-guard status` lays out its output; how the shared reachability predicate is factored.

## Version

**0.6.0 — my call, flagged for review.** The release question offered "a single 0.5.1", but this adds
a feature (new subcommands, a SessionStart hook, global git config), which is a minor bump under
semver. It is still ONE release.
