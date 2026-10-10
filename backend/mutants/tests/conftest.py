"""Shared test setup.

Every test runs with git history turned off (``REQMAN_GIT=off``), so the app
fixtures that reload ``reqman.app`` keep their speed and never run git;
tests of the history feature opt in (see ``test_git_history.py``). Git is
also isolated from the developer's own configuration for every test: no
global or system config, and a fixed author/committer identity.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch):
    monkeypatch.setenv("REQMAN_GIT", "off")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test Author")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "author@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test Author")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "author@example.com")
    for name in ("REQMAN_GIT_AUTHOR_NAME", "REQMAN_GIT_AUTHOR_EMAIL", "REQMAN_GIT_SQUASH_MINUTES",
                 "REQMAN_GIT_EXECUTABLE", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
