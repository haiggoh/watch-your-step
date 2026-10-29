# 🛘🥾 watch-your-step

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
| `secrets/ps-argv` | warn | `ps aux`/`ax` / `-ef` or `-e -f` / `-ww` / `-o args`/`command`/`cmd`, `pgrep -a` / `-fl` / `--list-full`: full process arguments can carry API keys into the transcript |
| `git/stash-untracked` | warn | a bare `git stash` (no `-u` / `-a`) with untracked files present: they are not stashed, and a later clean or checkout can destroy them |
| `zsh/unquoted-var-arglist` | warn (zsh) | `cmd $files 2>/dev/null`: zsh passes the whole list as ONE argument, and the failure reads as a clean empty result |
| `git/discards-uncommitted` | block | `git checkout`/`git restore` on paths carrying uncommitted edits (the `<rev> --` form counts staged-only too), a forced switch (`checkout -f`, `switch --discard-changes`), `git reset --hard` with tracked edits, or `git clean -f` with untracked files — when uncommitted work would actually be lost |
| `git/branch-delete-unrecoverable` | block | `git branch -d/-D` (every name, quoted or not, through `-C`/`-c` options) or `git push <remote> --delete`/`:<name>` when the branch's commits would be reachable from no other ref, and deleting `main`/`master` |
| `tool/read-pages-non-pdf` | block | a `Read` with `pages` on a file that is not a PDF: `pages` is ignored there and the call silently returns line 1 again |
| `tool/edit-anchor-mid-indent` | block | an `Edit`/`MultiEdit` whose `old_string` starts with indentation but matches inside a deeper-indented line, which silently re-nests the block |

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
  this plugin must never stall a session. (`watch-your-step.py test` is the exception: a probe that
  hid a crashing check would vouch for a command it never judged, so there it exits 1 and says so.)
- The marker counts only in a real comment: inside a commit message or a heredoc body it is text.

## The git guard (since 0.6.0)

A PreToolUse hook only sees the command line an agent **types**. A branch deletion can also come from a
`$VAR`, `bash -c`, `git update-ref -d`, a script, or a Python subprocess, and none of those reach it. Git
itself has one step every route passes through: the **reference transaction**. So watch-your-step also
installs two of git's own hooks:

| git hook | name in `~/.gitconfig` | refuses |
|---|---|---|
| `reference-transaction` | `hook.wys-branch-guard` | deleting a local branch whose commits no other branch, tag or remote-tracking ref holds (renames pass) |
| `pre-push` | `hook.wys-push-guard` | deleting a remote branch whose commits exist nowhere else in the repo, or a remote `main`/`master` |

- **Installed automatically, and announced.** At session start, `watch-your-step.py git-guard ensure` adds these
  keys to your **global** git config (and only these) and prints one line saying so. They are git's own
  config-based hooks, so they add to a repository's `.git/hooks` and never replace them. See them in git's own view:
  `git hook list --show-scope reference-transaction`.
- **Only under Claude.** The hooks act only when git runs under a Claude Code session (`CLAUDECODE` is set).
  Your own `git branch -D` in a plain terminal behaves exactly as before.
- **Deliberate deletion, both ways:** `git -c hook.wys-branch-guard.enabled=false branch -D <b>`
  (pre-push: `hook.wys-push-guard`), or `WYS_ALLOW_BRANCH_DELETE=1 git …`. The refusal prints both with your branch name.
- **Off for good:** `watch-your-step.py git-guard uninstall` sets `hook.wys-*-guard.enabled=false`, and from then on
  session starts leave the keys alone (`git hook list` shows them as `disabled`). `git-guard install` turns them back
  on, and `git-guard status` shows the current state.
- **Cost:** outside Claude, one shell test per ref update. Inside Claude, Python starts only for a branch deletion.
  Measured on git 2.56: 9 ms per ref transaction with no guard, 25 ms with the guard outside Claude, and 63 ms in a
  mix of half creations and half deletions inside Claude. **Fails open:** a missing script, or any error, lets git through.
- Needs a git with config-based hooks (`hook.<name>.command`); on an older git, `ensure` does nothing and `status` says so.

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
