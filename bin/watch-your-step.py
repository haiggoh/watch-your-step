#!/usr/bin/env python3
"""🥾 watch-your-step — catch command traps BEFORE a tool call runs.

Usage:
  watch-your-step.py check                       PreToolUse hook mode: reads the hook JSON on stdin,
                                                 prints a deny (block rules) or additionalContext
                                                 (warn rules); prints nothing when the call is clean
  watch-your-step.py test [--tool T] [--shell S] COMMAND
                                                 try a command by hand and print every rule it trips
  watch-your-step.py rules                       list the rules and their severity
  watch-your-step.py -h | --help                 this help

Override one rule on one command, visibly: end the command with  # wys:allow <rule-id>[,<rule-id>]

Environment:
  WATCH_YOUR_STEP_DISABLE   comma-separated rule ids to skip everywhere, or "all"
  WATCH_YOUR_STEP_SHELL     shell the rules assume (default: basename of $SHELL)
  WATCH_YOUR_STEP_USER      user name the home-path rule compares against (default: the login user)
  WATCH_YOUR_STEP_RULES     path to an alternative rules.json

The hook FAILS OPEN: any internal error lets the tool call through and is reported on stderr.
"""
import getpass
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RULES = os.path.join(ROOT, "rules", "rules.json")
MARK = "\U0001F97E watch-your-step"
CONTEXTS = ("bare", "squote", "dquote", "heredoc_quoted", "heredoc_unquoted", "raw", "lit", "comment")
LITERAL_WORD = re.compile(r"[\w./@:+=-]+")  # a quoted span this simple means exactly its text
PATH_KEYS = ("file_path", "notebook_path")

HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\2")
ALLOW = re.compile(r"#\s*wys:allow\s+([\w/.,\- ]+)")


DOC = __doc__ or ""
USAGE = "usage: watch-your-step.py check | test [--tool T] [--shell S] COMMAND | rules | --help\n"


def usage(stream):
    stream.write(USAGE)


def subshell_end(cmd, i):
    """Index of the ')' closing a $( ... ) whose body starts at i, honouring quotes, nested
    parentheses and heredoc bodies (a commit message in <<'EOF' may hold any character)."""
    depth, pending, n = 1, [], len(cmd)
    while i < n:
        c = cmd[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = cmd.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and cmd[j] != '"':
                if cmd[j] == "\\":
                    j += 2
                elif cmd.startswith("$(", j):
                    j = subshell_end(cmd, j + 2) + 1
                else:
                    j += 1
            i = j + 1
            continue
        if cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = HEREDOC.match(cmd, i)
            if m:
                pending.append((m.group(3), m.group(1) == "-"))
                i = m.end()
                continue
        if c == "\n" and pending:
            i += 1
            for delim, strip in pending:
                while i < n:
                    j = cmd.find("\n", i)
                    line = cmd[i:] if j < 0 else cmd[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
            pending = []
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if not depth:
                return i
        i += 1
    return n


def split_command(cmd):
    """Split a shell command into quoting contexts.

    Returns (bare_view, parts): bare_view is the command with every quoted span replaced by Q and
    heredoc bodies and comments removed; parts maps each quoted context to its texts. Escaped
    characters are dropped from double-quoted text, since they are literal there.

    parts["lit"] is the same view except that a quoted span made only of LITERAL_WORD characters
    keeps its text ("feat/x" -> feat/x): it can name a branch, a variable or a space cannot.
    parts["comment"] holds the real comments, the only place a `# wys:allow` marker counts.
    """
    parts = {k: [] for k in CONTEXTS}
    bare, lit = [], []
    pending = []  # heredocs opened on the current line: (delimiter, quoted, strip_tabs)
    i, n = 0, len(cmd)
    at_word_start = True
    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n:
            bare.append("_")
            lit.append("_")
            i += 2
            at_word_start = False
            continue
        if c == "'" or (c == "$" and cmd.startswith("$'", i)):
            start = i + (2 if c == "$" else 1)
            j = start
            while j < n and cmd[j] != "'":
                j += 2 if (c == "$" and cmd[j] == "\\") else 1
            parts["squote"].append(cmd[start:j])
            bare.append("Q")
            lit.append(cmd[start:j] if c == "'" and LITERAL_WORD.fullmatch(cmd[start:j]) else "Q")
            i = j + 1
            at_word_start = False
            continue
        if c == '"':
            j, buf, plain = i + 1, [], True
            while j < n:
                d = cmd[j]
                if d == "\\" and j + 1 < n:
                    j += 2  # an escaped character is literal: never seen by a rule
                    plain = False
                    continue
                if cmd.startswith("$(", j):
                    # a subshell is its own command line: parse it on its own terms, so a quoted
                    # heredoc inside "$(cat <<'EOF' ...)" stays literal and its parentheses balance
                    end = subshell_end(cmd, j + 2)
                    _, sub = split_command(cmd[j + 2:end])
                    for k in CONTEXTS:
                        if k != "raw":
                            parts[k].extend(sub[k])
                    buf.append("S")
                    plain = False
                    j = end + 1
                    continue
                if d == '"':
                    break
                buf.append(d)
                j += 1
            parts["dquote"].append("".join(buf))
            bare.append("Q")
            text = "".join(buf)
            lit.append(text if plain and LITERAL_WORD.fullmatch(text) else "Q")
            i = j + 1
            at_word_start = False
            continue
        if c == "#" and at_word_start:
            j = cmd.find("\n", i)
            parts["comment"].append(cmd[i:] if j < 0 else cmd[i:j])
            i = n if j < 0 else j
            continue
        if c == "<" and cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = HEREDOC.match(cmd, i)
            if m:
                pending.append((m.group(3), bool(m.group(2)), m.group(1) == "-"))
                bare.append("<<H")
                lit.append("<<H")
                i = m.end()
                at_word_start = False
                continue
        if c == "\n" and pending:
            bare.append("\n")
            lit.append("\n")
            i += 1
            for delim, quoted, strip in pending:
                body = []
                while i < n:
                    j = cmd.find("\n", i)
                    line = cmd[i:] if j < 0 else cmd[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
                    body.append(line)
                parts["heredoc_quoted" if quoted else "heredoc_unquoted"].append("\n".join(body))
            pending = []
            at_word_start = True
            continue
        if c == "$" and cmd.startswith("$(", i):
            # a top-level command substitution is its own command line too: rules with a bare
            # context also see the inner command in command position. The body itself still
            # streams into this view, so glob/redirect checks keep seeing everything they saw.
            end = subshell_end(cmd, i + 2)
            _, sub = split_command(cmd[i + 2:end])
            for k in CONTEXTS:
                if k != "raw":
                    parts[k].extend(sub[k])
            i += 2
            continue
        bare.append(c)
        lit.append(c)
        at_word_start = c in " \t\n;&|()"
        i += 1
    view = "".join(bare)
    parts["bare"].append(view)
    parts["lit"].append("".join(lit))   # last entry = this command line (subshells come first)
    parts["raw"].append(cmd)
    return view, parts


# ---- builtin checks: each returns True when the rule fires ------------------------------------

def check_home_typo(texts, user, **_):
    for text in texts:
        for m in re.finditer(r"/Users/([^/\s\"'`$;:)]+)|-Users-([^-/\s\"'`]+)", text):
            name = m.group(1) or m.group(2)
            if name != user and not os.path.isdir("/Users/" + name):
                return True
    return False


REDIRECT = re.compile(r"(?<![>&\d])>(?![>&])[ \t]*([^\s;&|<>()]+)")
PY_TRUNCATE = re.compile(r"open\(\s*([\w.\[\]'\"]+)\s*,\s*['\"]w['\"]\s*\)\s*\.write\(\s*open\(\s*\1\s*\)\s*\.read")


NON_READERS = ("echo", "printf", "print")  # print their ARGUMENTS; they never open them as files


def check_truncate_before_read(texts, bare_view, **_):
    for chain in re.split(r"&&|\|\||[;\n]", bare_view):
        for m in REDIRECT.finditer(chain):
            target = m.group(1)
            if target in ("/dev/null", "Q"):
                continue
            before = chain[: m.start()]
            stages = before.split("|")
            if _cmd_word(stages[-1]) in NON_READERS:
                before = "|".join(stages[:-1])   # `echo a > a` reads nothing; earlier stages still can
            # ':' too: the X in a git object spec `HEAD:X` names a blob, not the file on disk
            if re.search(r"(?<![\w./:-])" + re.escape(target) + r"(?![\w./-])", before):
                return True
    return any(PY_TRUNCATE.search(t) for t in texts)


def check_equals_word(texts, **_):
    for text in texts:
        text = re.sub(r"\[\[.*?\]\]", " ", text, flags=re.S)
        if re.search(r"(?:^|(?<=[\s;&|(]))=[^\s;&|)]+", text):
            return True
    return False


# ---- state-aware git checks: they look at the repo itself, so they only fire when the trap is real --

MARKER = re.compile(r"^(?:<{7}|>{7})(?: |$)", re.M)
CACHE_DIRS = ("__pycache__/", ".pytest_cache/", ".ruff_cache/", ".mypy_cache/", ".venv/", "venv/",
              "node_modules/", "dist/", "build/", ".DS_Store")
CACHE_PARTS = {c[:-1] for c in CACHE_DIRS if c.endswith("/")}


def _is_cache_path(p, root=None):
    """True for cache noise only: a .pyc file, .DS_Store, or a path whose DIRECTORY components
    (never the file itself) are a known cache dir — 'venv/x' is cache, 'myvenv/plan.md' is not.

    `root` opts into INSPECTING a collapsed directory: a trailing slash means git judged the whole
    dir ignorable — 'sub/' when sub/ holds only a .DS_Store — so the name alone cannot tell a
    Finder-junk dir from a real one, and the dir must be looked at. Callers that pass no root keep
    the name-only judgement, which is what the stash rule's untracked-list filter needs.
    """
    if not p.endswith("/"):
        parts = p.split("/")
        return (".DS_Store" in parts or p.endswith(".pyc")
                or any(part in CACHE_PARTS for part in parts[:-1]))
    # 'venv/' — the last component IS the directory (git collapsed it), so unlike the file case
    # above it must not be skipped by parts[:-1]; 'myvenv/x' still is not cache.
    name = p[:-1].split("/")[-1]
    if root:
        return _dir_is_all_cache(os.path.join(root, p[:-1]))
    return name in CACHE_PARTS


def _dir_is_all_cache(d):
    """True when a directory holds nothing but cache noise (or is empty)."""
    try:
        entries = [e for e in os.listdir(d) if e not in (".", "..")]
    except OSError:
        return False
    for e in entries:
        full = os.path.join(d, e)
        if os.path.isdir(full):
            if not _dir_is_all_cache(full):
                return False
        elif not _is_cache_path(e):      # the FILE branch, not the dir branch
            return False
    return True


def git(args, cwd, timeout=3):
    import subprocess
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.stdout if p.returncode == 0 else None


def resolve_dir(before, git_c, cwd):
    """The directory a git invocation runs in: the payload cwd, moved by the last unquoted `cd X`
    before it, then by `git -C X`. None when a path is quoted or does not exist (then: no verdict).
    A cd inside ( ) or $( ) cannot outlive its subshell, so those spans are ignored."""
    d = cwd or os.getcwd()
    stripped, i, n = [], 0, len(before)
    while i < n:
        if before[i] == "(":
            i = subshell_end(before, i + 1) + 1
            stripped.append(" ")
            continue
        stripped.append(before[i])
        i += 1
    for step in [m.group(1) for m in re.finditer(r"(?:^|[\s;&|(])cd\s+([^\s;&|()]+)", "".join(stripped))][-1:] + ([git_c] if git_c else []):
        if "Q" == step or "$" in step:
            return None
        d = os.path.normpath(os.path.join(d, os.path.expanduser(step)))
    return d if os.path.isdir(d) else None


GIT_FINISH = re.compile(r"\bgit(\s+-C\s+(\S+))?\s+(?:rebase\s+--continue|merge\s+--continue|cherry-pick\s+--continue|commit)\b")


def check_staged_conflict_markers(texts, bare_view, cwd=None, **_):
    for m in GIT_FINISH.finditer(bare_view):
        d = resolve_dir(bare_view[: m.start()], m.group(2), cwd)
        if not d:
            continue
        staged = git(["diff", "--cached", "-U0", "--no-color"], d) or ""
        if MARKER.search("\n".join(line[1:] for line in staged.splitlines() if line.startswith("+"))):
            return True
        # `git add f && git rebase --continue` in ONE command: f is not staged yet when this runs
        for add in re.finditer(r"\bgit(?:\s+-C\s+\S+)?\s+add\s+([^;&|]+)", bare_view[: m.start()]):
            for path in add.group(1).split():
                full = os.path.join(d, path)
                if path.startswith("-") or not os.path.isfile(full):
                    continue
                try:
                    with open(full, encoding="utf-8", errors="replace") as f:
                        if MARKER.search(f.read()):
                            return True
                except OSError:
                    continue
    return False


WORKTREE_REMOVE = re.compile(r"\bgit(\s+-C\s+(\S+))?\s+worktree\s+remove\s+(?:(?:--force|-f)\s+)*([^\s;&|]+)")


def check_worktree_remove_ignored(texts, bare_view, cwd=None, **_):
    for m in WORKTREE_REMOVE.finditer(bare_view):
        d = resolve_dir(bare_view[: m.start()], m.group(2), cwd)
        if not d or m.group(3) == "Q":
            continue
        target = os.path.normpath(os.path.join(d, os.path.expanduser(m.group(3))))
        if not os.path.isdir(target):
            continue
        # Both '!! ' (ignored) and '?? ' (untracked) paths. The two matter for different reasons,
        # measured: with --untracked-files=all git names '?? sub/the-only-copy.md' directly, and
        # WITHOUT it the collapsed '?? sub/' still blocks — because a collapsed dir is judged by
        # INSPECTING it (_is_cache_path(root=…)). Two independent routes, both required: the
        # second is what carries this rule if -uall is ever dropped, and neither alone covers a
        # .DS_Store-only dir.
        out = git(["status", "--porcelain", "--untracked-files=all", "--ignored"], target) or ""
        risky = [ln[3:] for ln in out.splitlines()
                 if ln.startswith(("!! ", "?? ")) and not _is_cache_path(ln[3:], root=target)]
        if risky:
            return True
    return False


# --- git/branch-delete-unrecoverable: a deletion whose commits would be reachable from nowhere else
# Measured 2026-10-27 while porting run-to-completion's ship cleanup. `git branch -d` refuses an
# unmerged branch and `-D` overrides that refusal; `git push --delete` refuses only the branch the
# REMOTE's HEAD points at, and stops refusing the moment origin/HEAD points elsewhere — which is the
# shape of a repo that renamed its default after cloning (echoprism ships `master`). Neither case is
# reachable by the worktree rules above, which look at FILES, not at commit reachability.
# `git` followed by any number of global options: -C <dir> moves the repo, -c <k=v> sets config
# (an agent writes `git -c core.quotepath=off branch -D x` as often as the bare form).
GIT_PREFIX = r"\bgit((?:\s+-[Cc]\s+\S+)*)"
BRANCH_DELETE_LOCAL = re.compile(GIT_PREFIX + r"\s+branch\s+([^;&|\n]*)")
PUSH_DELETE = re.compile(GIT_PREFIX + r"\s+push\s+([^;&|\n]*)")
BRANCH_DELETE_FLAGS = ("-d", "-D", "--delete")
# The git guard's own overrides (0.6.0) also count as a deliberate deletion here, so the two layers
# never disagree about what "I really mean it" looks like.
ALLOW_ENV = re.compile(r"(?:^|[\s;&|(])(?:export\s+)?WYS_ALLOW_BRANCH_DELETE=1\b")
GUARD_OFF = {"branch": "hook.wys-branch-guard.enabled=false", "push": "hook.wys-push-guard.enabled=false"}
# Conventional default-branch names, checked as a NAMED pair rather than resolved from the repo:
# deleting either from a remote breaks every consumer that resolves it, whatever origin/HEAD says.
# A remote branch that origin/HEAD itself points at is NOT flagged — git refuses that deletion, so
# nothing would be lost and a warning there is noise over a refusal.
DEFAULT_NAMES = ("main", "master")
LS_REMOTE_BUDGET = 4.0   # seconds for every ls-remote of one evaluation; the hook timeout is 10


def _git_opts(opts):
    """(the last -C dir or None, the set of -c values) from GIT_PREFIX's captured options."""
    toks = opts.split()
    cdir = next((toks[i + 1] for i in range(len(toks) - 2, -1, -1) if toks[i] == "-C"), None)
    return cdir, {toks[i + 1] for i in range(len(toks) - 1) if toks[i] == "-c"}


def _deliberate(view, m, kind, cfg):
    return GUARD_OFF[kind] in cfg or bool(ALLOW_ENV.search(view[: m.start()]))


def unrecoverable(d, tip, exclude, extra_holders=frozenset()):
    """True when the commit `tip` would be reachable from no ref outside `exclude` — the one
    predicate the command-line rule and both git hooks share.

    exclude: full ref names being deleted (refs/heads/x, refs/remotes/origin/x). extra_holders:
    commit ids a ref is being SET to in the same operation (a rename, a delete-and-recreate) —
    they hold `tip` when it is one of them or an ancestor. A commit absent from this repo is held
    by nothing here. False whenever git cannot answer: no verdict, never a guess."""
    if git(["cat-file", "-e", f"{tip}^{{commit}}"], d) is None:
        return True
    for oid in extra_holders:
        if oid == tip or git(["merge-base", "--is-ancestor", tip, oid], d) is not None:
            return False
    out = git(["for-each-ref", "--contains", tip, "--format=%(refname)"], d)
    if out is None:
        return False
    # a symbolic */HEAD only aliases another ref, so it never holds anything on its own
    return not [r for r in out.split() if r not in exclude and not r.endswith("/HEAD")]


def _remote_state(d, remote, name, deadline):
    """(the branch the remote's HEAD points at, the remote sha of `name`) from ONE ls-remote.
    Queried, not read from refs/remotes/<remote>/HEAD: that ref is absent in a `git init` +
    `git remote add` repo, and the remote's own answer is what decides git's refusal anyway."""
    import time
    left = deadline - time.monotonic()
    if left <= 0:
        return None, None
    out = git(["ls-remote", "--symref", remote, "HEAD", f"refs/heads/{name}"], d, timeout=left) or ""
    head = sha = None
    for ln in out.splitlines():
        if ln.startswith("ref:"):
            ref = ln[4:].split("\t")[0].strip()
            if ref.startswith("refs/heads/"):
                head = ref[len("refs/heads/"):]
        elif ln.endswith(f"\trefs/heads/{name}"):
            sha = ln.split("\t")[0]
    return head, sha


def _push_delete(args):
    """(remote, branch) when these `git push` args delete a remote branch, else None."""
    toks = [t for t in args.split() if t and not REDIRECT_TOK.match(t)]
    if not toks:
        return None
    colon = next((t for t in toks if t.startswith(":") and len(t) > 1), None)
    flagged = any(t in ("--delete", "-d") for t in toks)
    if not colon and not flagged:
        return None
    positional = [t for t in toks if not t.startswith("-") and not t.startswith(":")]
    if not positional:
        return None
    remote = positional[0]
    name = None
    for i, t in enumerate(toks[:-1]):
        if t in ("--delete", "-d") and not toks[i + 1].startswith("-"):
            name = toks[i + 1]
            break
    if name is None and colon:
        name = colon[1:]
    if name is None:
        rest = [t for t in positional if t != remote]
        name = rest[-1] if rest else None
    if not name or name.startswith("-"):
        return None
    return remote, (name.rsplit(":", 1)[-1] if ":" in name else name)


def _resolvable(word):
    return bool(word) and "Q" != word and "$" not in word and "`" not in word


def check_branch_delete_unrecoverable(texts, bare_view, cwd=None, lit_view=None, **_):
    import time
    view = lit_view if lit_view is not None else bare_view
    for m in BRANCH_DELETE_LOCAL.finditer(view):
        cdir, cfg = _git_opts(m.group(1))
        toks = [t for t in m.group(2).split() if not REDIRECT_TOK.match(t)]
        if not any(t in BRANCH_DELETE_FLAGS for t in toks):
            continue                      # `git branch -v`, `--show-current`: not a deletion
        if _deliberate(view, m, "branch", cfg):
            continue
        d = resolve_dir(view[: m.start()], cdir, cwd)
        if not d:
            continue
        # every name is judged; one the tokenizer cannot resolve ($var, a spaced string) gets no
        # verdict on its own, without hiding the names around it
        names = [t for t in toks if not t.startswith("-") and _resolvable(t)]
        exclude = {f"refs/heads/{n}" for n in names}
        for name in names:
            tip = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}^{{commit}}"], d)
            if tip is None:
                continue                  # no such local branch: git errors, nothing is lost
            if name in DEFAULT_NAMES:
                return True               # the default branch itself: never delete it
            # No separate merged-into-the-default test: if a branch's tip is an ancestor of the
            # default branch, the default branch IS a holder — measured redundant in 0.4.2.
            if unrecoverable(d, tip.strip(), exclude):
                return True
    deadline = time.monotonic() + LS_REMOTE_BUDGET
    seen = set()
    for m in PUSH_DELETE.finditer(view):
        cdir, cfg = _git_opts(m.group(1))
        target = _push_delete(m.group(2))
        if not target or not all(_resolvable(t) for t in target):
            continue
        if _deliberate(view, m, "push", cfg):
            continue
        d = resolve_dir(view[: m.start()], cdir, cwd)
        if not d or (d, target) in seen:
            continue
        seen.add((d, target))
        remote, name = target
        head, sha = _remote_state(d, remote, name, deadline)
        if head is not None and name == head:
            continue                      # git refuses it: nothing would be lost, no verdict
        if sha is None:
            continue                      # not on the remote (or out of budget): no verdict
        if name in DEFAULT_NAMES:
            # A conventional default the remote's HEAD no longer points at: git permits it, and it
            # breaks every consumer that resolves that name even though the local copy survives.
            return True
        if unrecoverable(d, sha, {f"refs/remotes/{remote}/{name}"}):
            return True
    return False


# ---- stateful zsh check: a glob that matches nothing aborts the whole command line ----

GLOB_WORD = re.compile(r"[^\s;&|<>()\[\]{}\"'`]+")
GLOB_QUALIFIED = re.compile(r"[^\s;&|<>()\[\]{}\"'`]*[*?][^\s;&|<>()\[\]{}\"'`]*\([A-Za-z,]+\)")
BRACE_WORD = re.compile(r"\S*\{[^{}]*\}\S*")


def check_unmatched_glob(texts, bare_view, cwd=None, **_):
    """True when a bare glob would expand to nothing: zsh then refuses to run the command
    at all ('no matches found'), so every stage of the line silently never executes."""
    if not bare_view or ("*" not in bare_view and "?" not in bare_view):
        return False
    if re.search(r"\bsetopt\b[^\n;&|]*\bNULL_GLOB\b", bare_view) or re.match(r"\s*noglob\b", bare_view):
        return False
    view = re.sub(r"\[\[.*?\]\]", " ", bare_view, flags=re.S)  # a [[ ]] pattern matches a string, not the fs
    view = re.sub(r"\bcase\b.*?\besac\b", " ", view, flags=re.S)  # so does a case PATTERN
    view = GLOB_QUALIFIED.sub(" ", view)                        # *.log(N) is deliberately null-globbed
    view = BRACE_WORD.sub(" ", view)                           # brace expansion cannot be resolved here
    for m in GLOB_WORD.finditer(view):
        word = m.group(0)
        if ("*" not in word and "?" not in word) or re.search(r"[$Q`]", word):
            continue  # a quoted span or a variable inside the word makes the expansion unknowable
        d = resolve_dir(view[: m.start()], None, cwd)  # the command's own `cd X` moves the glob's base
        if d is None:
            continue
        pattern = os.path.expanduser(word)
        target = pattern if os.path.isabs(pattern) else os.path.join(d, pattern)
        if not glob.glob(target, recursive=True):
            return True
    return False


# ---- new rules (0.4.0): dirty worktrees, argv leaks, stashes that skip untracked ----

WORKTREE_ADD = re.compile(r"\bgit(\s+-C\s+(\S+))?\s+worktree\s+add\b")


def dirty_paths(d, untracked_only=False):
    """The real (non-cache) dirty paths of the repo at d: staged, unstaged and untracked —
    or untracked only. [] when clean, None when git cannot answer (then: no verdict)."""
    out = git(["status", "--porcelain"], d)
    if out is None:
        return None
    paths = []
    for ln in out.splitlines():
        if untracked_only and not ln.startswith("?? "):
            continue
        p = ln[3:]
        if p and not _is_cache_path(p):
            paths.append(p)
    return paths


def check_worktree_add_dirty(texts, bare_view, cwd=None, **_):
    for m in WORKTREE_ADD.finditer(bare_view):
        d = resolve_dir(bare_view[: m.start()], m.group(2), cwd)
        if not d:
            continue
        real = dirty_paths(d) or []
        # a preceding `git stash -u` in the SAME command empties the source checkout first
        if real and not re.search(r"\bgit(?:\s+-C\s+\S+)?\s+stash\b[^;&|\n]*(?:-u|--include-untracked|-a|--all)\b",
                                  bare_view[: m.start()]):
            n = len(real)
            return (f"{n} uncommitted path{'s' if n != 1 else ''} "
                    f"{'stay' if n != 1 else 'stays'} behind in the source checkout.")
    return False


PS_WRAPPERS = {"sudo", "env", "nohup", "command", "exec", "time", "nice", "stdbuf", "setsid", "xargs"}
PS_ARGV_AUX = re.compile(r"^-{0,2}(?:aux|ax)(?:ww)?$")
PS_ARGV_O_SPEC = re.compile(r"(?:^|,)(?:args|command|cmd)(?:=|$)")
PGREP_LIST_FULL = ("--list-full",)


def _ps_argv_args(toks):
    """(argument tokens, command token) when a token IS a ps/pgrep invocation in command
    position, else (None, None): `echo ps aux` is not one. sudo may carry its own flags
    and value before the command (sudo -u root ps aux)."""
    for i, t in enumerate(toks):
        if t in ("ps", "pgrep") or t.endswith(("/ps", "/pgrep")):
            pre = toks[:i]
            if "sudo" in pre or all(s in PS_WRAPPERS or re.fullmatch(r"[A-Za-z_]\w*=\S*", s) for s in pre):
                return toks[i + 1:], t
    return None, None


def check_ps_argv(texts, bare_view, **_):
    for view in texts:
        for seg in re.split(r"&&|\|\||[;|\n]|\|", view):
            args, cmd = _ps_argv_args(seg.split())
            if args is None or cmd is None:
                continue
            if cmd == "pgrep" or cmd.endswith("/pgrep"):
                # bare -l/--list print the process NAME only; argv needs -a (Linux),
                # -fl (BSD/Linux) or --list-full
                letters = "".join(t[1:] for t in args if re.fullmatch(r"-[A-Za-z]+", t))
                if any(t in PGREP_LIST_FULL for t in args):
                    return True
                if "a" in letters or ("l" in letters and "f" in letters):
                    return True
                continue
            short = ""
            for i, t in enumerate(args):
                if PS_ARGV_AUX.match(t):
                    return True
                if re.fullmatch(r"-[A-Za-z]+", t):
                    short += t[1:]  # separated short flags: -e -f is the same leak as -ef
                if not t.startswith("-"):
                    continue
                letters = t.lstrip("-")
                if letters == "format" or letters.endswith("o"):
                    spec = args[i + 1] if i + 1 < len(args) else ""
                    if PS_ARGV_O_SPEC.search(spec):
                        return True
                if re.fullmatch(r"-o(?:args|command)=?", t):
                    return True
            if ("e" in short and "f" in short) or "ww" in short:
                return True
    return False


STASH = re.compile(r"\bgit(\s+-C\s+(\S+))?\s+stash\b")
STASH_SAFE_SUB = {"list", "show", "pop", "apply", "drop", "clear", "branch", "create", "store"}
STASH_UNTRACKED_SAFE = {"-u", "--include-untracked", "--all", "-a"}


def check_stash_untracked(texts, bare_view, cwd=None, **_):
    for m in STASH.finditer(bare_view):
        toks = re.split(r"[;&|\n]", bare_view[m.end():])[0].split()
        if toks and toks[0] in STASH_SAFE_SUB:
            continue
        args = toks[1:] if toks and toks[0] in ("push", "save") else toks
        if any(t in STASH_UNTRACKED_SAFE for t in args):
            continue
        d = resolve_dir(bare_view[: m.start()], m.group(2), cwd)
        if not d:
            continue
        if dirty_paths(d, untracked_only=True):
            return True
    return False


# ---- git/discards-uncommitted: a command that silently discards uncommitted work --------------
# Measured 2026-10-28 (echoprism Task 10): `git checkout -- <file> 2>/dev/null`, meant to revert a
# test mutation, also discarded the uncommitted backfill in the same file and exited 0. The
# pathspec form of checkout/restore restores from the index or a revision and NEVER refuses —
# unlike a branch switch, which either carries the changes or errors out. `reset --hard` and
# `clean -f` destroy the same class of work; one rule, because the same-command escape
# (`git stash -u`) and the repo-state read are shared.

DISCARD_CMD = re.compile(r"\bgit(?:\s+-C\s+(\S+))?\s+(checkout|switch|restore|reset|clean)\b([^;&|\n]*)")
REDIRECT_TOK = re.compile(r"^\d*[<>]{1,2}|^&?[<>]{1,2}")   # 2>/dev/null, >f, >>f, 2>&1: not pathspecs
STASH_U_BEFORE = re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+stash\b[^;&|\n]*(?:-u|--include-untracked|-a|--all)\b")


def _discard_detail(n):
    return f"{n} uncommitted path{'s' if n != 1 else ''} would be discarded."


def _path_loss(d, paths, from_rev):
    """How many uncommitted paths a checkout/restore of `paths` would discard, or False.

    from_rev (checkout <rev> -- / restore --source): the worktree is rewritten from the rev, so a
    STAGED-only edit dies too — any real status in either column counts. From the index: only the
    unstaged difference (column Y) is destroyed; a staged-only file already matches the index, so
    `checkout --` is a no-op there. Untracked ('??') rows never count: git ERRORS on such a
    pathspec, it does not lose it. Cache rows never count (the siblings' noise filter). A path the
    tokenizer could not resolve (a quoted span, a $var) makes the whole match NO VERDICT — it may
    hold the trap, and guessing a pathspec from 'Q' would be invention.
    """
    if not paths or any("Q" in p or "$" in p for p in paths):
        return False
    out = git(["status", "--porcelain", "--", *paths], d)
    if out is None:
        return False
    n = 0
    for ln in out.splitlines():
        x, y, p = ln[0], ln[1], ln[3:]
        if x == "?" and y == "?":
            continue
        if _is_cache_path(p):
            continue
        if from_rev:
            if x != " " or y != " ":
                n += 1
        elif y not in (" ", "?"):
            n += 1
    return _discard_detail(n) if n else False


def check_discards_uncommitted(texts, bare_view, cwd=None, **_):
    for m in DISCARD_CMD.finditer(bare_view):
        toks = [t for t in m.group(3).split() if not REDIRECT_TOK.match(t)]
        d = resolve_dir(bare_view[: m.start()], m.group(1), cwd)
        if not d:
            continue
        # a `git stash -u` earlier in the SAME command segment empties the tree first (the same
        # escape as worktree_add_dirty's; both stop at a newline since 0.6.0)
        if STASH_U_BEFORE.search(bare_view[: m.start()]):
            continue
        sub = m.group(2)
        if sub in ("checkout", "switch"):
            if "--" in toks:
                sep = toks.index("--")
                rev = [t for t in toks[:sep] if not t.startswith("-")] if sub == "checkout" else []
                loss = _path_loss(d, toks[sep + 1:], bool(rev))
            elif sub == "switch":
                # switch never takes pathspecs: its positionals are branches, so only force discards
                if any(t in ("-f", "--force", "--discard-changes") for t in toks):
                    real = dirty_paths(d) or []
                    loss = _discard_detail(len(real)) if real else False
                else:
                    loss = False        # a plain switch carries the changes or refuses: no verdict
            else:
                force = any(t in ("-f", "--force") for t in toks)
                pos = [t for t in toks if not t.startswith("-")]
                if force and not pos:
                    real = dirty_paths(d) or []
                    loss = _discard_detail(len(real)) if real else False
                elif pos:
                    # Without `--`, git reads ONE resolvable revision as a switch (carries or
                    # refuses: no loss) and anything else as pathspecs. Resolve before judging:
                    # `checkout -f main` is a force-switch (any dirt dies), `checkout -f f.txt` is
                    # a forced path restore (only f.txt's dirt dies).
                    resolvable = (len(pos) == 1
                                  and git(["rev-parse", "--verify", "--quiet", pos[0]], d) is not None)
                    if resolvable:
                        real = dirty_paths(d) or []
                        loss = _discard_detail(len(real)) if (force and real) else False
                    else:
                        loss = _path_loss(d, pos, from_rev=False)
                else:
                    loss = False
            if loss:
                return loss
            continue
        if sub == "restore":
            # One pass over the tokens: flags are classified (with --source/-s consuming their
            # VALUE, which is a rev, never a path), everything else — before or after `--` — is a
            # pathspec, because restore always takes paths and `--` is optional for it.
            staged = worktree = source = False
            paths, after_dd, skip = [], False, False
            for t in toks:
                if after_dd:
                    paths.append(t)
                    continue
                if skip:
                    skip = False
                    continue
                if t == "--":
                    after_dd = True
                elif t.startswith("-"):
                    if t in ("--staged", "-S"):
                        staged = True
                    elif t in ("--worktree", "-W"):
                        worktree = True
                    elif t.startswith("--source="):
                        source = True
                    elif t in ("--source", "-s"):
                        source = True
                        skip = True
                    # other flags (--ours, --theirs, --merge) are deliberately not judged: they
                    # resolve a conflict rather than silently discard, and the spec is silent on
                    # them, so the conservative no-verdict default applies (cross-family review)
                else:
                    paths.append(t)
            if staged and not worktree:
                continue        # index-only: the worktree copy keeps the work — recoverable
            loss = _path_loss(d, paths, from_rev=source)
            if loss:
                return loss
            continue
        if sub == "reset":
            if "--" in toks:
                continue        # git refuses a hard reset with paths
            if "--hard" in toks:
                out = git(["status", "--porcelain"], d)
                if out is not None:
                    rows = [ln[3:] for ln in out.splitlines()
                            if not ln.startswith("??") and not _is_cache_path(ln[3:])]
                    if rows:
                        return _discard_detail(len(rows))
            continue
        # clean: every deleting form needs a force flag; -n is a dry run either way
        letters = "".join(t[1:] for t in toks if re.fullmatch(r"-[A-Za-z]+", t))
        if "n" in letters or "--dry-run" in toks or not ("f" in letters or "--force" in toks):
            continue
        pos = [t for t in toks if not t.startswith("-")]
        out = git(["status", "--porcelain", "--ignored"] + (["--", *pos] if pos else []), d)
        if out is None:
            continue
        keep = set()
        for ln in out.splitlines():
            p = ln[3:]
            if (ln.startswith("??") or ("x" in letters and ln.startswith("!!"))) and not _is_cache_path(p):
                keep.add(p)
        if keep:
            return _discard_detail(len(keep))
    return False


ARG_LIST_CMDS = ("grep", "find", "ls", "rm", "cp", "mv", "cat", "wc")
CMD_WRAPPERS = {"sudo", "env", "nohup", "command", "exec", "time", "nice", "stdbuf", "setsid", "xargs"}
WRAPPER_VALUE_FLAGS = {"-u", "-n"}  # sudo -u USER CMD / nice -n N CMD: the next token is the value
UNQUOTED_ARG_VAR = re.compile(r"\$(?:\{(?!=)([A-Za-z_]\w*)\}|(?![{(])(?![_0-9])([A-Za-z_]\w*)(?![\w/.,\[]))")
FOR_LOOP_VAR = re.compile(r"\bfor\s+([A-Za-z_]\w*)\s+in\b")
ARRAY_ASSIGN = re.compile(r"(?:^|[\s;&|(])([A-Za-z_]\w*)=\(")
HIDES_STDERR = re.compile(r"2>\s*/dev/null|&>\s*/dev/null|2>>\s*/dev/null|2>&-|(?:^|[\s;&|])>\s*/dev/null\s+2>&1")


def _cmd_word(seg):
    """The segment's command word after skipping env assignments and wrappers — including a
    wrapper's flag cluster and, for value-taking flags, their value token. None when the
    segment has no plain command word."""
    toks = seg.split()
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"[A-Za-z_]\w*=\S*", t) or t in CMD_WRAPPERS:
            i += 1
            continue
        if t in WRAPPER_VALUE_FLAGS:
            i += 2
            continue
        if t.startswith("-") and i > 0:  # a wrapper's own flag cluster: sudo -l, env -i
            i += 1
            continue
        break
    if i >= len(toks):
        return None
    return toks[i]


def check_unquoted_var_arglist(texts, bare_view=None, **_):
    """zsh passes an unquoted $var as ONE argument (no word splitting): a multi-path
    argument list becomes a single nonexistent path, and with stderr discarded the search
    reads as a clean empty result. Loop variables, same-command array assignments, ${=x}
    and $arr[@] / ${arr[@]} forms are exempt. Loop and array scoping is sequential: a name
    only exempts the segments from its for/array onward, never the ones before it."""
    loopvars, arrays = set(), set()
    for view in texts:
        for seg in re.split(r"&&|\|\||[;|\n]|\|", view):
            loopvars |= {m.group(1) for m in FOR_LOOP_VAR.finditer(seg)}
            arrays |= {m.group(1) for m in ARRAY_ASSIGN.finditer(seg)}
            if not HIDES_STDERR.search(seg):
                continue
            cmd = _cmd_word(seg)
            if cmd not in ARG_LIST_CMDS:
                continue
            m = re.search(r"\b" + cmd + r"\b", seg)
            if m is None:
                continue
            for v in UNQUOTED_ARG_VAR.finditer(seg):
                name = v.group(1) or v.group(2)
                if v.start() > m.start() and name not in loopvars and name not in arrays:
                    return True
    return False


def check_read_pages_non_pdf(texts, tool_input=None, **_):
    """Read's `pages` pages a PDF only; on any other file it is ignored WITHOUT an error and the
    call returns line 1 again, so a pager loop re-reads the same lines while believing it moved."""
    ti = tool_input or {}
    return "pages" in ti and not str(ti.get("file_path", "")).lower().endswith(".pdf")


def _mid_indent(text, anchor):
    """True when `anchor` (starting with a space or tab) occurs after whitespace-only characters
    on its line — the 8-space anchor inside a 12-space line."""
    if not anchor or anchor[0] not in " \t":
        return False
    i = text.find(anchor)
    while i >= 0:
        line_start = text.rfind("\n", 0, i) + 1
        lead = text[line_start:i]
        if lead and not lead.strip(" \t"):
            return True
        i = text.find(anchor, i + 1)
    return False


def check_edit_anchor_mid_indent(texts, tool_input=None, **_):
    ti = tool_input or {}
    try:
        with open(str(ti.get("file_path", "")), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False                      # no such file (Edit creates nothing): no verdict
    multi = ti.get("edits")
    edits = multi if isinstance(multi, list) else [ti]
    return any(_mid_indent(text, str(e.get("old_string") or "")) for e in edits if isinstance(e, dict))


CHECKS = {
    "home_typo": check_home_typo,
    "read_pages_non_pdf": check_read_pages_non_pdf,
    "edit_anchor_mid_indent": check_edit_anchor_mid_indent,
    "truncate_before_read": check_truncate_before_read,
    "equals_word": check_equals_word,
    "staged_conflict_markers": check_staged_conflict_markers,
    "worktree_remove_ignored": check_worktree_remove_ignored,
    "unmatched_glob": check_unmatched_glob,
    "worktree_add_dirty": check_worktree_add_dirty,
    "ps_argv": check_ps_argv,
    "stash_untracked": check_stash_untracked,
    "unquoted_var_arglist": check_unquoted_var_arglist,
    "branch_delete_unrecoverable": check_branch_delete_unrecoverable,
    "discards_uncommitted": check_discards_uncommitted,
}


def load_rules(path=None):
    with open(path or os.environ.get("WATCH_YOUR_STEP_RULES") or DEFAULT_RULES, encoding="utf-8") as f:
        return json.load(f)["rules"]


def evaluate(tool, tool_input, shell=None, user=None, rules=None, cwd=None, strict=False):
    """Return the rules a tool call trips, in rules-file order.

    strict=True (the `test` probe) re-raises a check that crashes or names no CHECKS entry instead
    of reading it as 'did not fire': hook mode fails open, a probe that does so lies."""
    rules = load_rules() if rules is None else rules
    shell = shell or os.environ.get("WATCH_YOUR_STEP_SHELL") or os.path.basename(os.environ.get("SHELL", "sh"))
    user = user or os.environ.get("WATCH_YOUR_STEP_USER") or getpass.getuser()
    disabled = {s.strip() for s in os.environ.get("WATCH_YOUR_STEP_DISABLE", "").split(",") if s.strip()}
    if "all" in disabled:
        return []
    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        view, parts = split_command(cmd)
        # only a REAL comment carries the marker — never a commit message or a heredoc body
        allowed = {a for c in parts["comment"] for m in ALLOW.finditer(c)
                   for a in re.split(r"[,\s]+", m.group(1)) if a}
        lit_view = parts["lit"][-1]
    else:
        path = next((tool_input[k] for k in PATH_KEYS if tool_input.get(k)), "")
        view, parts = path, {k: [] for k in CONTEXTS}
        parts["raw"].append(path)
        allowed = set()
        lit_view = path
    hits = []
    for rule in rules:
        rid = rule["id"]
        if tool not in rule["tools"] or rid in disabled or rid in allowed:
            continue
        if rule["shells"] and shell not in rule["shells"]:
            continue
        texts = [t for ctx in rule["context"] for t in parts[ctx]]
        if "check" in rule:
            try:
                fired = CHECKS[rule["check"]](texts, bare_view=view, lit_view=lit_view, user=user,
                                              cwd=cwd, tool_input=tool_input)
            except Exception as e:  # a check that cannot decide (git missing, timeout) never blocks
                if strict:
                    raise RuntimeError(f"rule {rid}: check {rule['check']!r} raised "
                                       f"{type(e).__name__}: {e}") from e
                fired = False
        else:
            fired = any(re.search(rule["pattern"], t) for t in texts)
        if fired:
            hits.append({**rule, "detail": fired} if isinstance(fired, str) else rule)
    return hits


def render(hits):
    lines = []
    for h in hits:
        verdict = "✖ BLOCKED" if h["severity"] == "block" else "⚠ warning"
        msg = h["message"] + (f" {h['detail']}" if h.get("detail") else "")
        lines.append(f"{MARK}: {verdict} — {msg}")
        lines.append(f"  fix: {h['fix']}")
        lines.append(f"  rule: {h['id']} · incident {h['incident']}")
    lines.append("  (intended? end the command with  # wys:allow <rule-id>)")
    return "\n".join(lines)


def hook_output(hits):
    if not hits:
        return None
    hits = sorted(hits, key=lambda h: h["severity"] != "block")
    out = {"hookEventName": "PreToolUse"}
    if hits[0]["severity"] == "block":
        out["permissionDecision"] = "deny"
        out["permissionDecisionReason"] = render(hits)
    else:
        out["additionalContext"] = render(hits)
    return {"hookSpecificOutput": out}


def cmd_check():
    try:
        payload = json.load(sys.stdin)
        result = hook_output(evaluate(payload.get("tool_name", ""), payload.get("tool_input") or {},
                                      cwd=payload.get("cwd")))
    except Exception as e:  # fail open: our bug must never stall a session
        sys.stderr.write(f"{MARK}: internal error, call allowed ({type(e).__name__}: {e})\n")
        return 0
    if result:
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_test(args):
    tool, shell = "Bash", None
    while args and args[0] in ("--tool", "--shell"):
        if len(args) < 2:
            usage(sys.stderr)
            return 2
        if args[0] == "--tool":
            tool = args[1]
        else:
            shell = args[1]
        args = args[2:]
    if not args:
        usage(sys.stderr)
        return 2
    text = " ".join(args)
    tool_input = {"command": text} if tool == "Bash" else {"file_path": text}
    try:
        hits = evaluate(tool, tool_input, shell=shell, strict=True)
    except RuntimeError as e:
        sys.stderr.write(f"{MARK}: ✖ check error — the probe cannot vouch for this command: {e}\n")
        return 1
    print(render(hits) if hits else f"{MARK}: clean")
    return 0


def cmd_rules():
    for r in load_rules():
        shells = ",".join(r["shells"]) or "any shell"
        print(f"{r['severity']:5}  {r['id']:32}  {', '.join(r['tools'])} · {shells}")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        (sys.stdout if argv else sys.stderr).write(DOC)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "check" and not rest:
        return cmd_check()
    if cmd == "test":
        return cmd_test(rest)
    if cmd == "rules" and not rest:
        return cmd_rules()
    sys.stderr.write(f"watch-your-step: unknown argument {cmd!r}\n")
    usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
