# Incidents behind the rules

Each rule exists because the trap happened in real work, usually more than once, and a written reminder
did not stop it. The common thread: **the command exited 0**, so nothing looked wrong until someone re-read
the stored result.

## shell/backtick-in-dquote

A to-do bullet was written with a double-quoted argument containing a code span:

    --point "Needs a READER or it is write-only data nobody can use: `tool journal [--id <id>]`"

zsh command-substituted the span, tried to run `tool journal [--id <id>]`, and stored the bullet
**truncated at the colon**. The only tell was one easily missed line, `bad pattern: [--id` (zsh failing to
glob the `[`), while the command printed its normal success message. A *partial* corruption is what made it
subtle: an empty bullet would have looked wrong at once.

Before 0.1.0 shipped, replaying 2,184 real tool calls found the trap had struck again in a live session. A note
written as `"... fire in a headless `claude -p` run ..."` was stored as `"... in a headless  run ..."`, and
the substitution **actually ran `claude -p`** while writing it.

## shell/dollar-digit-in-dquote

A bullet in double quotes said "…an NVIDIA error, $0." zsh expanded `$0` to the shell's name and stored
"…error, /bin/zsh." The same replay found "contributes $0 of savings" in another session. Dollar amounts are
routine in cost work, which makes this the more likely of the quoting traps to bite. It is a **warning**,
because `"$1"` inside a double-quoted script is often intended.

## path/home-typo

A session on a free model typed absolute paths with the user name mangled twice in one day (a digit
doubled, a letter swapped). The tool's only signal was "file does not exist", which reads as a missing FILE
rather than a wrong DIRECTORY. Seeing the right name hundreds of times in context does not prevent this:
a model rebuilds a path from token pieces every time it writes one. The robust fix is to never type the
home directory at all (`~`, `$HOME`); the rule blocks the typo when it happens anyway. It flags a
`/Users/<name>` only when `<name>` is neither the login user nor an existing directory, so real second
accounts and `/Users/Shared` pass.

## zsh/equals-word

`echo ====` as a visual separator inside a multi-stage probe: zsh's EQUALS expansion looked up a command named
`===`, printed `=== not found`, and aborted the rest of the command line, so the later stages never ran and
the empty output nearly read as "nothing found". This happened twice in a month to the same author.
`[ "$a" == "b" ]` fails the same way in zsh; `[[ … == … ]]` is safe.

## zsh/unmatched-glob

A `find A /tmp/la-*` probe: the glob matched nothing, zsh printed its only output —
`(eval):1: no matches found: /tmp/la-*` — and aborted the ENTIRE command line before any
stage ran. The empty output nearly read as "no logs exist"; nothing had executed at all. A
compound probe is worse: stages that DID run coexist with stages that silently never did,
so a "no results" verdict gets manufactured from nothing. `2>/dev/null` does not rescue the
line, and neither does `||` — zsh refuses before they are ever reached. Bash is unaffected
(it passes the literal pattern on to the tool), which is why the rule is zsh-only. Because
a glob that matches something is harmless, the rule checks the payload's actual `cwd`
(following the command's own `cd`) instead of trusting the text, and stays quiet when
expansion would succeed. The 0.3.0 replay over 1,059 real commands found the shape again:
a ledger-existence probe written as `ls .../cost-ledger/deadbeef* || echo "no ledger file
written"` — whose fallback echo could never have run.

## shell/truncate-before-read

`open(p, "w").write(open(p).read().replace(...))` opens for writing first, which truncates the file, so the
read returns an empty string and the file is destroyed while the line reports success. `sed … f > f` and
`cat f | … > f` do the same in the shell: the redirect empties `f` before the command reads it.

## git/staged-conflict-markers

During a rebase, staging a still-conflicted file records the marker content as the resolution: git marks it
resolved, `--continue` succeeds, and nothing complains. A plugin's `plugin.json` reached a public main branch
this way. Every version-fetching installer then read garbage at the HEAD manifest and quietly fell back
to the old version, so the fresh release looked like an unrelated updater bug. The tag was immutable;
it was fixed forward. The usual shape was `git add plugin.json && git rebase --continue` in ONE command,
which is why the rule also reads the files a preceding `git add` names: they aren't staged yet when the
hook runs. Only `<<<<<<<` and `>>>>>>>` lines count, since a bare `=======` is a valid markdown heading underline.

## git/worktree-remove-ignored

"Clean and merged" means `git status --porcelain` is empty, and that view hides ignored files. During a cleanup,
three merged worktrees had ignored content; one held the ONLY copy of a plan's progress ledger
(`.superpowers/sdd/<plan>/progress.md`). Removing a worktree deletes such files for good. Caches
(`__pycache__`, `.venv`, `node_modules`, `dist/`…) are excluded, so the warning only appears when something
might be unique.

## zsh/pipestatus, zsh/unquoted-var-for, shell/grep-F-anchor

Three ways a check can pass without checking anything, each met while shipping a release:
`${PIPESTATUS[0]}` is empty under zsh, so an exit-status gate built on it always passed. `for f in $files` ran
once in zsh with the whole list as one argument, so a per-file loop "processed one file". And `grep -F '^## [0.4'`
could never match, because `-F` makes `^` a literal character, so a version probe reported "not found" for a
version that was there.

## git/worktree-add-dirty

The standard workflow here creates one worktree per change and assumes the source checkout is clean:
"repo on `main`, clean" is the precondition of every handoff. Nothing guarded it. A `git worktree add` from a
checkout with staged, unstaged or untracked files succeeds, and the new tree silently starts from HEAD — the
uncommitted work stays behind, and a session that continues in the new tree believes it is editing work that
isn't there. A 2026-10-25 coverage grep across 16 plugin roots found no rule (memory, instruction file, hook or
skill) covering the shape, while the session that found it executes in exactly this pattern every day. The
message names the count in `git status --porcelain` paths — a wholly untracked directory counts once, which
keeps the hook-time git call cheap — so a many-path checkout reads differently from a one-path one, and cache
noise (`.DS_Store`, `__pycache__`…) is excluded by path component (`venv/` is cache, `myvenv/plan.md` is
not) so the warning only appears when real work stays behind.

## secrets/ps-argv

`ps aux`, `ps -ef` (or `-e -f`), `ps -o args` and `pgrep -a` / `-fl` / `--list-full` print the full command
line of every matching process — including any API key a tool was handed on its argv. Bare `pgrep -l` does
NOT: on BSD and Linux alike it prints the process name only, and an earlier draft of this rule warned on it
until the cross-family review caught the false positive (the check now requires `-a`, or `-l` together with
`-f`). The replay that proved this rule is its own evidence: a single day of ordinary session work contained
23 such invocations in 1,788 commands, most of them probing claude/remote-session/litellm processes. A
warning is the right severity — the command is often legitimate — but the fix names the argv-free forms
(`ps -o pid,lstart,comm -p <pids>`, `pgrep -x <name>`), which the session-liveness work that shares these
transcripts already uses.

## git/stash-untracked

`git stash` without `-u` stashes tracked changes only and leaves untracked files in the working tree while
reporting success — the stash looks like "everything is saved". The memory rule "tracked ≠ recoverable" was
written after uncommitted work was lost to exactly this blind spot, and worktrees here additionally share one
stash stack between every concurrent session, which is why the rule suggests the more visible alternative: a
named WIP checkpoint branch. The rule is state-aware — it fires only when untracked files actually exist,
cache noise excluded — so a stash on a clean tree stays silent.

## zsh/unquoted-var-arglist

`roots=$(cat list | tr '\n' ' ')`, then `grep -rliE "$pat" $roots 2>/dev/null`: zsh does not word-split an
unquoted parameter, so grep received ONE nonexistent path ("a.md b.md c.md"), `2>/dev/null` swallowed
"No such file", and all 17 patterns printed nothing — including a planted positive control known to be
present. The failure was first misattributed to a grep-rewriting hook; only isolating single-file vs list
invocation exposed the cause. The rule requires the `2>/dev/null` in the same command (the trap is the SILENT
empty result, not the expansion itself), exempts `for`-loop variables, `${=var}` and array forms, and the fix
reminds that every negative search needs a planted positive control (observation 0046).

## git/discards-uncommitted

`git checkout -- <path>` restores the worktree file from the index, never refuses, and exits 0. During the
echoprism live-test batch, a session ran

    git checkout -- src-tauri/src/db/queries.rs 2>/dev/null

to revert a deliberately mutated test file — and discarded the *uncommitted* backfill work that lived in the
same file (a test plus two code blocks). The loss surfaced only when the session had to redo the work; the
`2>/dev/null` had hidden the only output that could have hinted at it. A branch switch behaves differently —
git either carries the changes or refuses — which is why a plain `git checkout <branch>` gets no verdict and
only the forced forms (`checkout -f`, `switch --discard-changes`) fire. The same rule covers the other two
commands that destroy the same class of work in place: `git reset --hard` (staged and unstaged tracked
edits) and `git clean -f` (untracked files; `-x` adds ignored ones). The rule reads the real repo state at
the command's own cwd (following `cd X` and `git -C X`), so a clean path, an untracked pathspec (git errors
on those rather than losing them), an index-only `restore --staged` (the worktree copy keeps the work), a
same-command `git stash -u`, quoted or variable pathspecs, and cache-only noise all stay silent.
