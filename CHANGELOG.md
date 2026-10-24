# Changelog

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
