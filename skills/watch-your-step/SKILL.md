---
name: watch-your-step
description: "Use when a 🥾 watch-your-step message blocks or warns about a tool call, when a shell command silently lost text or did nothing (a backtick or $ inside double quotes, zsh aborting on '====' or on a glob that matches nothing, a mistyped home path, a file emptied by its own redirect), or when the user asks to add, tune, override or list a command-trap rule."
---

# 🥾 watch-your-step

A PreToolUse hook that reads each Bash command (and the path of each Read/Write/Edit) BEFORE it runs and
checks it against `rules/rules.json`. A **block** rule refuses the call; a **warn** rule lets it run and
adds a note. Every message names the rule, the fix and the incident that produced the rule.

## When a call is blocked

1. Read the `fix:` line and apply it: it is the whole remedy. Do not retry the same command with small
   variations; the rule matches the trap, not the wording.
2. If the command is genuinely intended (for example a backtick that IS meant to run), end it with
   `# wys:allow <rule-id>` so the override is visible in the transcript. Never set
   `WATCH_YOUR_STEP_DISABLE` to get past one command.
3. If the block is wrong (a false positive), say so to the user and add the command as a `pass` case in
   `tests/cases.json` of the plugin's SOURCE repo, never in the installed cache.

## Adding a rule (in the source repo)

1. Add the rule to `rules/rules.json`: `id`, `severity` (block only when running the command is never what
   was meant, otherwise warn), `tools`, `shells`, `context`, `pattern` or a builtin `check`, `message`,
   `fix`, `incident`.
2. Add at least one `flag` and one `pass` case to `tests/cases.json`, written from the real incident; the
   suite refuses a rule without both, and its mutation test proves each flag case is caught by its own rule.
3. Replay real commands for false positives before shipping: run the engine over recent transcripts and
   read every hit.

`watch-your-step.py test '<command>'` tries a command by hand; `watch-your-step.py rules` lists the rules.
