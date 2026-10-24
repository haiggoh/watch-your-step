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

## shell/truncate-before-read

`open(p, "w").write(open(p).read().replace(...))` opens for writing first, which truncates the file, so the
read returns an empty string and the file is destroyed while the line reports success. `sed … f > f` and
`cat f | … > f` do the same in the shell: the redirect empties `f` before the command reads it.

## zsh/pipestatus, zsh/unquoted-var-for, shell/grep-F-anchor

Three ways a check can pass without checking anything, each met while shipping a release:
`${PIPESTATUS[0]}` is empty under zsh, so an exit-status gate built on it always passed. `for f in $files` ran
once in zsh with the whole list as one argument, so a per-file loop "processed one file". And `grep -F '^## [0.4'`
could never match, because `-F` makes `^` a literal character, so a version probe reported "not found" for a
version that was there.
