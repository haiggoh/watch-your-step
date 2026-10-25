# 🥾 watch-your-step

**Catches command traps before a tool call runs.** Some shell mistakes don't fail. The command exits 0 and the damage
is silent: a code span inside double quotes runs as a command and its text disappears, a `$40` becomes an
empty string, `sed … f > f` empties the file it was meant to edit, and zsh aborts a whole command line at an
unquoted `====`. A rule written in a notes file does not stop any of this, because the model has to remember
it at the exact moment it types the command. A hook doesn't have to remember.

watch-your-step is a Claude Code **PreToolUse** hook. It reads each Bash command (and the path of each
Read/Write/Edit) **before it runs**, checks it against a rules file, and then:

- **blocks** the call when running it is never what you meant (silent corruption), with the exact fix;
- **warns** when the command may be fine but often isn't, and lets it run;
- stays **silent** on a clean command.

```text
🥾 watch-your-step: ✖ BLOCKED — a backtick inside a double-quoted string (or an unquoted <<EOF heredoc)
is command substitution: the code span is replaced by the output of running it, and the command still exits 0.
  fix: single-quote the prose, use a quoted heredoc (<<'EOF'), or escape each backtick as \`
  rule: shell/backtick-in-dquote · incident 2026-09-27, two waypoint bullets silently corrupted
  (intended? end the command with  # wys:allow <rule-id>)
```

## Rules

| Rule | Severity | Catches |
|---|---|---|
| `shell/backtick-in-dquote` | block | a backtick inside `"…"` or an unquoted `<<EOF` heredoc |
| `path/home-typo` | block | an absolute path naming a home directory that is not yours, the way models mistype a user name |
| `shell/truncate-before-read` | block | `cmd f > f`, and python `open(p,"w").write(open(p).read()…)` |
| `zsh/equals-word` | block (zsh) | an unquoted word starting with `=` (`echo ====`, `[ a == b ]`) |
| `shell/dollar-digit-in-dquote` | warn | `"$40"`, `"$0"` in quoted prose |
| `zsh/pipestatus` | warn (zsh) | `${PIPESTATUS[0]}`, which is always empty under zsh |
| `shell/grep-F-anchor` | warn | `grep -F '^…'`, which can never match |
| `zsh/unquoted-var-for` | warn (zsh) | `for f in $files`: zsh doesn't word-split it |
| `git/staged-conflict-markers` | block | a commit or `rebase/merge/cherry-pick --continue` that would record `<<<<<<<` / `>>>>>>>` lines, including files named by a `git add` earlier in the same command |
| `git/worktree-remove-ignored` | warn | `git worktree remove` on a worktree holding gitignored files that aren't caches, which removal deletes for good |
| `zsh/unmatched-glob` | warn (zsh) | a bare glob matching nothing, which aborts the whole command line before anything runs |
| `git/worktree-add-dirty` | warn | `git worktree add` from a checkout with uncommitted files: the new tree starts from HEAD and they stay behind |
| `secrets/ps-argv` | warn | `ps aux` / `-ef` / `-ww` / `-o args` / `-o command`, `pgrep -a` / `-l` / `-fl`: full process arguments can carry API keys into the transcript |
| `git/stash-untracked` | warn | a bare `git stash` (no `-u` / `-a`) with untracked files present: they are not stashed, and a later clean or checkout can destroy them |
| `zsh/unquoted-var-arglist` | warn (zsh) | `cmd $files 2>/dev/null`: zsh passes the whole list as ONE argument, and the failure reads as a clean empty result |

The `git/` rules and `zsh/unmatched-glob` are **state-aware**: instead of matching text, they look at the
repository or the filesystem at hook time (the staged diff, the worktree's ignored files, the checkout's dirty
paths, whether each glob expands), following `cd X` and `git -C X`, so they only speak when the trap is real. If
they can't decide (no git, a quoted path, a timeout, a variable inside the pattern), they say nothing.

Rules are **data** (`rules/rules.json`), and every rule ships with should-flag and should-pass cases
(`tests/cases.json`; the state-aware rules are proven in `tests/test_git_rules.py` against real temporary
repositories). A mutation test proves that each flag case is caught by its own rule. Before 0.1.0 shipped,
the engine was replayed over 2,184 real tool calls from four working sessions: no false block, and it found a
waypoint bullet that had silently lost a `` `claude -p` `` span (that command ran when the note was written).

## How it reads a command

It does **not** grep the raw command. A small shell tokenizer splits it into contexts (unquoted text, single
quotes, double quotes, quoted and unquoted heredoc bodies, comments), and each rule names the contexts it
applies to. So `'run `make` first'` (single quotes) and a commit message in `"$(cat <<'EOF' … EOF)"` pass:
in both, the text is literal. A `$( … )` inside double quotes is parsed as its own command line.

## Overrides, visible by design

- One command: end it with `# wys:allow <rule-id>` (comma-separate several). The override stays in the
  transcript, so a reader can see what was waved through.
- Everywhere (temporary): `WATCH_YOUR_STEP_DISABLE=<rule-id>,…` or `all`.
- The hook **fails open**: if it crashes, the call goes through and the error goes to stderr. A bug in
  this plugin must never stall a session.

## Install

From the haiggoh marketplace (see [get-haiggoh](https://github.com/haiggoh/get-haiggoh)):

```sh
claude plugin install watch-your-step@haiggoh
```

Hooks load when a session starts, so open a new session after installing.

## Try it

```sh
bin/watch-your-step.py test 'echo "run `make` first"'     # blocked
bin/watch-your-step.py test "echo 'run \`make\` first'"   # clean
bin/watch-your-step.py rules                              # list the rules
bin/watch-your-step.py --help
```

Environment: `WATCH_YOUR_STEP_DISABLE`, `WATCH_YOUR_STEP_SHELL` (default: your login shell),
`WATCH_YOUR_STEP_USER` (default: your login name), `WATCH_YOUR_STEP_RULES` (an alternative rules file).

## Why not hookify?

[hookify](https://github.com/anthropics/claude-plugins-official) is a fine rule engine, but it loads rules from
`.claude/hookify.*.local.md` **relative to the current directory**, so a rule only exists in the project that holds
the file. Command traps follow you into every directory and worktree, so they need machine-wide rules. They
also need a shell-aware matcher, because a plain regex over the raw command flags single-quoted text. Use hookify
for project-specific rules and watch-your-step for traps that come with your shell.

## Development

```sh
python3 -m pytest -q tests          # add -n auto with pytest-xdist
bash tests/test_version_consistency.sh
```

Standard library only; no dependencies. MIT licensed.
