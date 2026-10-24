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
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RULES = os.path.join(ROOT, "rules", "rules.json")
MARK = "\U0001F97E watch-your-step"
CONTEXTS = ("bare", "squote", "dquote", "heredoc_quoted", "heredoc_unquoted", "raw")
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
    """
    parts = {k: [] for k in CONTEXTS}
    bare = []
    pending = []  # heredocs opened on the current line: (delimiter, quoted, strip_tabs)
    i, n = 0, len(cmd)
    at_word_start = True
    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n:
            bare.append("_")
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
            i = j + 1
            at_word_start = False
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n:
                d = cmd[j]
                if d == "\\" and j + 1 < n:
                    j += 2  # an escaped character is literal: never seen by a rule
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
                    j = end + 1
                    continue
                if d == '"':
                    break
                buf.append(d)
                j += 1
            parts["dquote"].append("".join(buf))
            bare.append("Q")
            i = j + 1
            at_word_start = False
            continue
        if c == "#" and at_word_start:
            j = cmd.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "<" and cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = HEREDOC.match(cmd, i)
            if m:
                pending.append((m.group(3), bool(m.group(2)), m.group(1) == "-"))
                bare.append("<<H")
                i = m.end()
                at_word_start = False
                continue
        if c == "\n" and pending:
            bare.append("\n")
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
        bare.append(c)
        at_word_start = c in " \t\n;&|()"
        i += 1
    view = "".join(bare)
    parts["bare"].append(view)
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


def check_truncate_before_read(texts, bare_view, **_):
    for chain in re.split(r"&&|\|\||[;\n]", bare_view):
        for m in REDIRECT.finditer(chain):
            target = m.group(1)
            if target in ("/dev/null", "Q"):
                continue
            if re.search(r"(?<![\w./-])" + re.escape(target) + r"(?![\w./-])", chain[: m.start()]):
                return True
    return any(PY_TRUNCATE.search(t) for t in texts)


def check_equals_word(texts, **_):
    for text in texts:
        text = re.sub(r"\[\[.*?\]\]", " ", text, flags=re.S)
        if re.search(r"(?:^|(?<=[\s;&|(]))=[^\s;&|)]+", text):
            return True
    return False


CHECKS = {
    "home_typo": check_home_typo,
    "truncate_before_read": check_truncate_before_read,
    "equals_word": check_equals_word,
}


def load_rules(path=None):
    with open(path or os.environ.get("WATCH_YOUR_STEP_RULES") or DEFAULT_RULES, encoding="utf-8") as f:
        return json.load(f)["rules"]


def evaluate(tool, tool_input, shell=None, user=None, rules=None):
    """Return the rules a tool call trips, in rules-file order."""
    rules = load_rules() if rules is None else rules
    shell = shell or os.environ.get("WATCH_YOUR_STEP_SHELL") or os.path.basename(os.environ.get("SHELL", "sh"))
    user = user or os.environ.get("WATCH_YOUR_STEP_USER") or getpass.getuser()
    disabled = {s.strip() for s in os.environ.get("WATCH_YOUR_STEP_DISABLE", "").split(",") if s.strip()}
    if "all" in disabled:
        return []
    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        view, parts = split_command(cmd)
        allowed = {a for m in ALLOW.finditer(cmd) for a in re.split(r"[,\s]+", m.group(1)) if a}
    else:
        path = next((tool_input[k] for k in PATH_KEYS if tool_input.get(k)), "")
        view, parts = path, {k: [] for k in CONTEXTS}
        parts["raw"].append(path)
        allowed = set()
    hits = []
    for rule in rules:
        rid = rule["id"]
        if tool not in rule["tools"] or rid in disabled or rid in allowed:
            continue
        if rule["shells"] and shell not in rule["shells"]:
            continue
        texts = [t for ctx in rule["context"] for t in parts[ctx]]
        if "check" in rule:
            fired = CHECKS[rule["check"]](texts, bare_view=view, user=user)
        else:
            fired = any(re.search(rule["pattern"], t) for t in texts)
        if fired:
            hits.append(rule)
    return hits


def render(hits):
    lines = []
    for h in hits:
        verdict = "✖ BLOCKED" if h["severity"] == "block" else "⚠ warning"
        lines.append(f"{MARK}: {verdict} — {h['message']}")
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
        result = hook_output(evaluate(payload.get("tool_name", ""), payload.get("tool_input") or {}))
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
    hits = evaluate(tool, tool_input, shell=shell)
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
