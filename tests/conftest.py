"""Suite-wide git isolation. The git guard (0.6.0) writes GLOBAL git config, so no test may ever see
the real ~/.gitconfig: GIT_CONFIG_GLOBAL points at a temp file for the whole run (every xdist
worker configures itself), system config is ignored, and a tripwire fails the run if the real
file changed anyway — a leak must be loud, never a silent edit of the user's config."""
import hashlib
import os
import tempfile
from pathlib import Path

import pytest

REAL = Path("~/.gitconfig").expanduser()


def _digest(p):
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def pytest_configure(config):
    d = tempfile.mkdtemp(prefix="wys-gitconfig-")
    cfg = Path(d) / "gitconfig"
    cfg.write_text("[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = main\n")
    os.environ["GIT_CONFIG_GLOBAL"] = str(cfg)
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    config._wys_real_digest = _digest(REAL)


def pytest_sessionfinish(session):
    # sessionfinish, not unconfigure: an exception raised in unconfigure does not fail the run
    # (measured: a planted leak still reported "1 passed", exit 0)
    before = getattr(session.config, "_wys_real_digest", "unset")
    if before != "unset" and _digest(REAL) != before:
        session.config.get_terminal_writer().line(f"TRIPWIRE: the test run changed the real {REAL}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
