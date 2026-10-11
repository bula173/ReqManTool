"""The commit subject of a checked-out module: ``[module][username][name]``.

The server edition's lock service names the commits of a checkout
(``reqman_server.module_locks.ModuleLocks.commit_subject``); here it is a
small fake, so these tests cover the core's wiring: ``_check_module_writes``
→ ``_checkout_subject`` → ``GitHistory.auto_commit`` →
``history.commit_message``. The rules of the lock itself are
``test_module_locks.py``.

Same rules as ``test_git_history.py`` (whose helpers and fixtures are
reused): every repository lives in pytest's ``tmp_path``, ``conftest.py``
isolates git from the user's configuration, and ``git()`` refuses to run in
the checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_git_history import (  # noqa: F401 - fixtures are used by name
    clock,
    commits,
    git,
    root,
    service,
)

from reqman import extensions
from reqman.extensions import ModuleLockedError


class _Locks:
    """The lock service: refuses (and names) exactly the listed (project, module) checkouts."""

    def __init__(self, held: dict | None = None, require: bool = False) -> None:
        self.held = dict(held or {})
        self.require = require

    def check_write(self, identity, project_id, module_id) -> None:
        if self.require and (project_id, module_id) not in self.held:
            raise ModuleLockedError(f"Module '{module_id}' must be checked out for editing before it can be changed")

    def commit_subject(self, project_id: str, module_id: str) -> str | None:
        return self.held.get((project_id, module_id))


SUBJECT = "[syrs][alice][Reworking the braking chapter]"


def checked_out(service, monkeypatch, *, require: bool = False) -> tuple:
    project = service.create_project("Alpha")
    module = service.create_module(project.id, "System", "SYRS")
    locks = _Locks({(project.id, module.id): SUBJECT}, require=require)
    monkeypatch.setattr(extensions.HOOKS, "lock_service", locks)
    return project, module, locks


def test_the_checkout_names_the_commit_of_a_write(service, root, monkeypatch):
    project, module, _locks = checked_out(service, monkeypatch)
    folder = root / project.id

    req = service.add_requirement(project.id, module.id, {"object_text": "Hello"})

    assert commits(folder)[0] == SUBJECT  # the subject line (newest first)
    body = git(folder, "log", "-1", "--format=%B")
    assert f"{req.id} added" in body  # the changed files stay in the body
    assert "ReqMan-Session:" in body


def test_the_label_survives_the_sessions_squash_and_a_project_level_change(service, root, clock, monkeypatch):
    project, module, _locks = checked_out(service, monkeypatch)
    folder = root / project.id

    service.add_requirement(project.id, module.id, {"object_text": "Hello"})
    service.update_requirement(project.id, module.id, "SYRS-0001", {"object_text": "Hello again"})
    service.rename_project(project.id, "Alpha 2")  # no module: the session's label stays

    log = commits(folder)  # newest first
    assert log[0] == SUBJECT
    assert len([subject for subject in log if subject == SUBJECT]) == 1  # one squashed session commit
    body = git(folder, "log", "-1", "--format=%B")
    assert "SYRS-0001 added" in body and "Module 'System' added" in body


def test_a_commit_without_a_checkout_describes_its_files(service, root, clock, monkeypatch):
    project, module, locks = checked_out(service, monkeypatch)
    folder = root / project.id

    service.add_requirement(project.id, module.id, {"object_text": "Hello"})
    assert commits(folder)[0] == SUBJECT

    locks.held.clear()  # released: the next session is labelled by its files again
    clock.offset += 11 * 60  # past the squash window: a new commit, not an amended one
    service.add_requirement(project.id, module.id, {"object_text": "More"})

    last = commits(folder)[0]
    assert last != SUBJECT and not last.startswith("[")
    assert "SYRS-0002 added" in git(folder, "log", "-1", "--format=%B")


def test_a_write_without_a_checkout_is_refused(service, root, monkeypatch):
    project, module, locks = checked_out(service, monkeypatch, require=True)
    folder = root / project.id
    locks.held.clear()
    before = len(commits(folder))

    with pytest.raises(ModuleLockedError, match="must be checked out"):
        service.add_requirement(project.id, module.id, {"object_text": "Hello"})

    assert len(commits(folder)) == before  # a refused change commits nothing
