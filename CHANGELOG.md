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
  - `secrets/ps-argv`: `ps aux` / `-ef` / `-ww` / `-o args` / `-o command` and
    `pgrep -a` / `-l` / `-fl` — full process arguments can carry API keys from a command
    line into the transcript; the fix names the argv-free forms
    (`ps -o pid,lstart,comm -p <pids>`, `pgrep -x <name>`).
  - `zsh/unquoted-var-arglist` (zsh-only, from observation 0046): an unquoted `$var` passed
    as an argument to a file-consuming command together with `2>/dev/null` — zsh passes the
    whole list as ONE argument and the failure reads as a clean empty result. Loop variables,
    `${=var}`, arrays and bash scripts are exempt.
- Proven like the earlier rules: red-first cases in `tests/cases.json` (the two stateful
  rules in `tests/test_git_rules.py`, against real temporary repositories), mutation-checked
  (each flag case is caught by its own rule; each new git check monkeypatched to False turns
  its tests red).
- Replay-checked over 1,788 real commands from ten recent transcripts: 27 hits, all true
  catches — 23 argv-revealing ps/pgrep invocations and four runs of the observation-0046
  `grep … $roots 2>/dev/null` probe itself. Zero false positives, and the safe twins in the
  same corpus (`ps -o lstart=`, `pgrep -f` alone, a quoted `"$pat"`, loop variables) stayed
  silent. Five replay-shaped regression cases pin those shapes.

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
