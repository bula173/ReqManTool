"""Module edit locks (``reqman_server/module_locks.py``): the checkout rules.

The checkout's name (required when the lock is taken) and the commit subject
it names (``[module][username][name]``, asked of the lock service by
``reqman.extensions.module_commit_subject``), the refusal of writes to a
module nobody checked out (``server.require_module_checkout``) and the
persistence of the locks.

No HTTP and no project data: the locks are their own file in a temporary
state directory; the permission guard and the user registry are small fakes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reqman.extensions import (
    ACTION_PROJECT_ADMIN,
    ACTION_PROJECT_READ,
    ACTION_PROJECT_REVIEW,
    ACTION_PROJECT_WRITE,
    AccessDeniedError,
    ModuleLockedError,
    RequestIdentity,
    identity_scope,
)
from reqman_server.module_locks import ModuleLockRegistry, ModuleLocks
from reqman_server.users import User

PROJECT = "alpha"
MODULE = "sys"
NAME = "Reworking the braking chapter"

ALICE = RequestIdentity("u-alice", "Alice Adams", "alice@example.com")
BOB = RequestIdentity("u-bob", "Bob Brown", "bob@example.com")


class _Guard:
    """Refuses the actions in ``deny`` (an action is refused for everybody)."""

    def __init__(self, *deny: str) -> None:
        self.deny = set(deny)

    def check(self, identity, action, project_id=None) -> None:
        if action in self.deny:
            raise AccessDeniedError(f"you may not ({action})")


class _Users:
    """Just what ``ModuleLocks`` asks the user registry for."""

    def __init__(self, *users: User) -> None:
        self.by_id = {user.id: user for user in users}

    def get(self, user_id: str):
        return self.by_id.get(user_id)


USERS = _Users(
    User(id="u-alice", name="Alice Adams", email="alice@example.com", username="alice", provider="github", subject="1"),
    User(id="u-bob", name="", email="bob@example.com", username="bobby", provider="github", subject="2"),
)


def make_locks(path: Path, *, guard: _Guard | None = None, require_checkout: bool = True, clock=None) -> ModuleLocks:
    registry = ModuleLockRegistry(path, 3600.0) if clock is None else ModuleLockRegistry(path, 3600.0, clock=clock)
    return ModuleLocks(
        registry,
        guard or _Guard(),
        USERS,
        require_checkout=require_checkout,
        module_exists=lambda project_id, module_id: module_id == MODULE,
    )


@pytest.fixture
def locks(tmp_path):
    return make_locks(tmp_path / "module_locks.json")


def checkout(locks: ModuleLocks, identity: RequestIdentity = ALICE, name=NAME):
    with identity_scope(identity):
        return locks.lock(PROJECT, MODULE, name)


# --- taking a checkout ------------------------------------------------------------------


def test_a_checkout_needs_a_name(locks):
    for name in (None, "", "   \t"):
        with identity_scope(ALICE), pytest.raises(ValueError, match="needs a name"):
            locks.lock(PROJECT, MODULE, name)
    assert locks.registry.get(PROJECT, MODULE) is None


def test_the_checkout_name_is_stored_trimmed_and_can_be_replaced(locks):
    result = checkout(locks, name="  Reworking   the braking chapter  ")
    assert result["lock"]["reason"] == NAME

    with identity_scope(ALICE):  # by the owner again: a refresh, the name may stay
        refreshed = locks.lock(PROJECT, MODULE, None)
    assert refreshed["lock"]["reason"] == NAME

    with identity_scope(ALICE):
        renamed = locks.lock(PROJECT, MODULE, "New name")
    assert renamed["lock"]["reason"] == "New name"


def test_the_checkout_name_may_not_be_longer_than_500_characters(locks):
    with identity_scope(ALICE), pytest.raises(ValueError, match="too long"):
        locks.lock(PROJECT, MODULE, "x" * 501)


def test_an_unknown_module_cannot_be_checked_out(locks):
    with identity_scope(ALICE), pytest.raises(FileNotFoundError, match="Module 'other' not found"):
        locks.lock(PROJECT, "other", NAME)


def test_a_viewer_may_not_check_out_and_a_reviewer_may(locks):
    with identity_scope(ALICE), pytest.raises(AccessDeniedError):
        make_locks(
            locks.registry.path,
            guard=_Guard(ACTION_PROJECT_READ, ACTION_PROJECT_REVIEW, ACTION_PROJECT_WRITE),
        ).lock(PROJECT, MODULE, NAME)

    # The lock needs ``project.review`` only, so a reviewer who may sign off can check out.
    reviewed = make_locks(locks.registry.path, guard=_Guard(ACTION_PROJECT_WRITE))
    assert checkout(reviewed)["lock"]["reason"] == NAME


# --- while it is held --------------------------------------------------------------------


def test_only_the_owner_may_write_and_a_refresh_keeps_the_lock_alive(locks):
    checkout(locks)
    with pytest.raises(ModuleLockedError, match="checked out for editing by Alice"):
        locks.check_write(BOB, PROJECT, MODULE)
    with identity_scope(ALICE):
        locks.check_write(ALICE, PROJECT, MODULE)  # the owner refreshes it
    assert locks.registry.get(PROJECT, MODULE).user_id == ALICE.user_id


def test_a_write_without_a_checkout_is_refused_only_when_the_server_requires_it(locks):
    with identity_scope(ALICE), pytest.raises(ModuleLockedError, match="must be checked out"):
        locks.check_write(ALICE, PROJECT, MODULE)
    # A module that does not exist fails with its own 404 instead of 423.
    with identity_scope(ALICE):
        locks.check_write(ALICE, PROJECT, "other")

    optional = make_locks(locks.registry.path, require_checkout=False)
    with identity_scope(ALICE):
        optional.check_write(ALICE, PROJECT, MODULE)  # an unlocked module stays freely editable


def test_an_expired_checkout_is_forgotten_and_the_module_needs_a_new_one(locks):
    now = [1_000_000.0]
    expiring = make_locks(locks.registry.path, clock=lambda: now[0])
    checkout(expiring)
    with identity_scope(ALICE):
        expiring.check_write(ALICE, PROJECT, MODULE)
    now[0] += 3601.0  # past ``module_lock_idle_minutes``
    with identity_scope(ALICE), pytest.raises(ModuleLockedError, match="must be checked out"):
        expiring.check_write(ALICE, PROJECT, MODULE)


# --- releasing ----------------------------------------------------------------------------


def test_only_the_owner_releases_and_a_project_admin_breaks(locks):
    checkout(locks)
    with identity_scope(BOB), pytest.raises(AccessDeniedError, match="only they can release"):
        locks.unlock(PROJECT, MODULE)
    with identity_scope(BOB), pytest.raises(AccessDeniedError):
        make_locks(locks.registry.path, guard=_Guard(ACTION_PROJECT_ADMIN)).break_lock(PROJECT, MODULE)

    assert checkout(make_locks(locks.registry.path))["lock"]["reason"] == NAME  # still held
    with identity_scope(ALICE):
        assert locks.unlock(PROJECT, MODULE)["released"] is True
    assert locks.registry.get(PROJECT, MODULE) is None


# --- the commit subject ---------------------------------------------------------------------


def test_the_checkout_names_the_commits_of_its_session(locks):
    checkout(locks, identity=ALICE, name=NAME)
    with identity_scope(ALICE):
        assert locks.commit_subject(PROJECT, MODULE) == f"[{MODULE}][alice][{NAME}]"
    with identity_scope(BOB):  # somebody else's checkout names nothing of theirs
        assert locks.commit_subject(PROJECT, MODULE) is None
    assert locks.commit_subject(PROJECT, MODULE) is None  # outside a request
    with identity_scope(ALICE):
        locks.unlock(PROJECT, MODULE)
        assert locks.commit_subject(PROJECT, MODULE) is None


def test_an_unknown_user_is_named_by_its_id_and_a_lock_without_a_name_names_nothing(locks):
    with identity_scope(ALICE):
        locks.lock(PROJECT, MODULE, NAME)
    locks.registry.release(PROJECT, MODULE)
    locks.registry.acquire(PROJECT, MODULE, "u-gone", NAME)  # a user since removed from the registry
    with identity_scope(RequestIdentity("u-gone", "Gone", "gone@example.com")):
        assert locks.commit_subject(PROJECT, MODULE) == f"[{MODULE}][u-gone][{NAME}]"

    locks.registry.release(PROJECT, MODULE)
    locks.registry.acquire(PROJECT, MODULE, ALICE.user_id, None)  # a lock taken before the name was required
    with identity_scope(ALICE):
        assert locks.commit_subject(PROJECT, MODULE) is None  # no name: the commit describes its files


# --- persistence ------------------------------------------------------------------------------


def test_a_lock_and_its_name_survive_a_restart(tmp_path):
    path = tmp_path / "module_locks.json"
    checkout(make_locks(path))

    reloaded = make_locks(path)
    lock = reloaded.registry.get(PROJECT, MODULE)
    assert lock is not None and lock.reason == NAME and lock.user_id == ALICE.user_id
    with identity_scope(ALICE):
        listed = reloaded.list_locks(PROJECT)
    assert [view["reason"] for view in listed["locks"]] == [NAME]
    assert listed["require_checkout"] is True
    with identity_scope(ALICE):
        assert reloaded.commit_subject(PROJECT, MODULE) == f"[{MODULE}][alice][{NAME}]"
