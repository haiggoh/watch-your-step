"""The suite never touches the real ~/.gitconfig: the git guard writes GLOBAL config, so every test
must see a temp file there (tests/conftest.py pins it; the conftest tripwire proves nothing leaked)."""
import os
import subprocess
from pathlib import Path


def test_global_git_config_is_a_temp_file_not_the_real_one():
    p = subprocess.run(["git", "config", "--global", "--show-origin", "--list"],
                       capture_output=True, text=True)
    real = Path("~/.gitconfig").expanduser().resolve()
    assert os.environ.get("GIT_CONFIG_GLOBAL"), "GIT_CONFIG_GLOBAL is not pinned"
    assert Path(os.environ["GIT_CONFIG_GLOBAL"]).resolve() != real
    assert str(real) not in p.stdout, "a test can read or write the real ~/.gitconfig"


def test_system_git_config_is_ignored():
    assert os.environ.get("GIT_CONFIG_NOSYSTEM") == "1"
