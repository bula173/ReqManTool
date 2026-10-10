"""What a git change means for requirements: commit messages and per-requirement history.

Pure functions over the raw JSON bytes of a project's data files at two or
more revisions. No git calls and no ``reqman.models``/``service`` import
(duck-typed like ``csv_export.py``), so it is easy to test standalone;
``git_repo.py`` fetches the file versions and ``ProjectService`` supplies the
project (for attribute and link names).

- ``describe_changes`` turns the files changed by one (possibly squashed)
  commit into short entries such as ``"SYRS-0012: Status Draft → Reviewed"``
  or ``"SYRS-0013 added"``; ``commit_message`` joins them into a subject,
  an optional body and the ``ReqMan-Session`` trailer that marks a commit
  ReqManTool may squash into (plus, in the server edition, the
  ``ReqMan-User`` trailer: the squash window is per user).
- ``requirement_history`` turns the versions of one requirement file (newest
  first, as ``git log --first-parent`` lists them) into a change list with
  per-attribute diffs, and derives the attribute blame from it: for each
  attribute, the last commit that changed it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .richtext import is_rich, to_plain

SESSION_TRAILER = "ReqMan-Session"
# Server edition: the id of the logged-in user an auto-commit was made for.
USER_TRAILER = "ReqMan-User"
_TRAILER_VALUE = re.compile(r"[A-Za-z0-9._@+-]{1,128}")

# Subject line budget: entries are added while the subject stays this short.
MAX_SUBJECT_LENGTH = 72
# Body lines listed before "… and N more".
MAX_BODY_ENTRIES = 200
# Attribute values in messages are cut to this many characters.
MAX_VALUE_LENGTH = 40

_PROJECT_FILE = re.compile(r"^project\.json$")
_MODULE_FILE = re.compile(r"^modules/([^/]+)/module\.json$")
_REQUIREMENT_FILE = re.compile(r"^modules/([^/]+)/requirements/([^/]+)\.json$")
_LINK_FILE = re.compile(r"^links/([^/]+)\.json$")

_UNREADABLE = object()


@dataclass
class FileChange:
    """One data file changed by a commit: its path and contents before/after (``None`` = absent)."""

    path: str
    old: bytes | None
    new: bytes | None


@dataclass
class Revision:
    """One version of a requirement file, from ``git log`` (``content`` ``None`` = deleted)."""

    commit: str
    author_name: str
    author_email: str
    authored_date: str
    date: str
    subject: str
    content: bytes | None


def _parse(data: bytes | None) -> Any:
    """The JSON value of ``data``; ``None`` if absent, ``_UNREADABLE`` if it cannot be parsed."""
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return _UNREADABLE


def format_value(value: Any) -> str:
    """A short, single-line rendering of an attribute value for a commit message.

    Rich text is shown as its plain-text projection; a value whose projection
    is empty reads as "(formatted text)".
    """
    if is_rich(value):
        value = to_plain(value) or "(formatted text)"
    if value is None or value == "":
        return "(empty)"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    if len(text) > MAX_VALUE_LENGTH:
        text = text[: MAX_VALUE_LENGTH - 1].rstrip() + "…"
    return text


# --- naming -----------------------------------------------------------------


@dataclass
class _Naming:
    attribute_names: dict[str, str]
    attribute_order: list[str]
    system_attribute_ids: set[str]
    link_names: dict[str, str]
    module_names: dict[str, str]
    # Link configuration id -> its target project, for cross-project links
    # (their target ends are named ``<project>:<id>``).
    link_target_projects: dict[str, str] = field(default_factory=dict)


def _list(value: Any) -> list:
    """``value`` if it is a list, else ``[]`` (a hand-edited file can hold anything)."""
    return value if isinstance(value, list) else []


def _naming(projects: Iterable[Any]) -> _Naming:
    """Attribute/link names from project.json objects; later ones win (pass old, then new)."""
    naming = _Naming({}, [], set(), {}, {})
    for project in projects:
        if not isinstance(project, dict):
            continue
        for attr in _list(project.get("attributes")):
            if isinstance(attr, dict) and isinstance(attr.get("id"), str):
                attr_id = attr["id"]
                if attr_id not in naming.attribute_names:
                    naming.attribute_order.append(attr_id)
                naming.attribute_names[attr_id] = str(attr.get("name") or attr_id)
                if attr.get("system"):
                    naming.system_attribute_ids.add(attr_id)
        for config in _list(project.get("link_configurations")):
            if isinstance(config, dict) and isinstance(config.get("id"), str):
                naming.link_names[config["id"]] = str(config.get("name") or config["id"])
                if isinstance(config.get("target_project_id"), str):
                    naming.link_target_projects[config["id"]] = config["target_project_id"]
                naming.system_attribute_ids.add(config["id"])
    return naming


def _by_id(items: Any) -> dict[str, dict]:
    if not isinstance(items, list):
        return {}
    return {i["id"]: i for i in items if isinstance(i, dict) and isinstance(i.get("id"), str)}


# --- commit messages -----------------------------------------------------------


def _describe_project(old: Any, new: Any) -> list[str]:
    if new is None or new is _UNREADABLE:
        return ["Project file removed" if new is None else "Project file changed"]
    name = new.get("name", "") if isinstance(new, dict) else ""
    if old is None:
        return [f"Project '{name}' created"]
    if old is _UNREADABLE or not isinstance(old, dict) or not isinstance(new, dict):
        return ["Project settings changed"]
    entries: list[str] = []
    if old.get("name") != new.get("name"):
        entries.append(f"Project renamed '{old.get('name')}' → '{new.get('name')}'")
    if old.get("description") != new.get("description"):
        entries.append("Project description changed")
    if old.get("parent_project_id") != new.get("parent_project_id"):
        entries.append(
            f"Parent project {format_value(old.get('parent_project_id'))} → "
            f"{format_value(new.get('parent_project_id'))}"
        )
    if old.get("references") != new.get("references"):
        entries.append(
            f"Project references {', '.join(map(str, _list(old.get('references')))) or '(none)'} → "
            f"{', '.join(map(str, _list(new.get('references')))) or '(none)'}"
        )
    if old.get("locked") != new.get("locked"):
        lock = new.get("locked")
        if lock:
            reason = lock.get("reason") if isinstance(lock, dict) else None
            reason = " ".join(reason.split()) if isinstance(reason, str) else ""
            if len(reason) > 80:
                reason = reason[:79] + "…"
            entries.append(f"Project locked{f': {reason}' if reason else ''}")
        else:
            entries.append("Project unlocked")
    for key, label, skip_system in (
        ("attributes", "Attribute", True),
        ("link_configurations", "Link configuration", False),
        ("views", "View", False),
    ):
        before, after = _by_id(old.get(key)), _by_id(new.get(key))
        for item_id, item in after.items():
            if skip_system and item.get("system"):
                continue  # link mirrors: the link configuration entry describes them
            if item_id not in before:
                entries.append(f"{label} '{item.get('name', item_id)}' added")
            elif before[item_id] != item:
                entries.append(f"{label} '{item.get('name', item_id)}' changed")
        for item_id, item in before.items():
            if item_id not in after and not (skip_system and item.get("system")):
                entries.append(f"{label} '{item.get('name', item_id)}' removed")
    entries.extend(_describe_module_tree(old.get("module_order"), new.get("module_order")))
    if _list(old.get("row_styles")) != _list(new.get("row_styles")):
        entries.append("Row styles changed")
    if not entries:
        ignored = {"format_version", "id_counters", "module_order"}
        if {k: v for k, v in old.items() if k not in ignored} != {
            k: v for k, v in new.items() if k not in ignored
        }:
            entries.append("Project settings changed")
    return entries


def _module_tree_entries(value: Any) -> tuple[list[tuple[str, str, str | None]], dict[str, str]]:
    """A ``module_order`` (format <= 6 id strings, or format 7 module/folder entries) flattened.

    Returns ``(kind, id, parent folder id)`` triples in display order and the
    folder names by id. Anything unreadable is skipped (hand-edited files).
    """
    entries: list[tuple[str, str, str | None]] = []
    names: dict[str, str] = {}

    def walk(items: Any, parent: str | None, depth: int) -> None:
        if depth > 64:
            return
        for item in _list(items):
            if isinstance(item, str):
                entries.append(("module", item, parent))
            elif isinstance(item, dict) and isinstance(item.get("module"), str):
                entries.append(("module", item["module"], parent))
            elif isinstance(item, dict) and isinstance(item.get("folder"), str):
                folder_id = item["folder"]
                name = item.get("name")
                names[folder_id] = name if isinstance(name, str) and name else folder_id
                entries.append(("folder", folder_id, parent))
                walk(item.get("children"), folder_id, depth + 1)

    walk(value, None, 1)
    return entries, names


def _describe_module_tree(old: Any, new: Any) -> list[str]:
    """Commit message lines for a changed module tree: folders added/removed/renamed/moved, modules moved.

    Modules added or removed are described by their module files; a pure
    reordering (same folders, same places) is "Module order changed".
    """
    old_entries, old_names = _module_tree_entries(old)
    new_entries, new_names = _module_tree_entries(new)
    if old_entries == new_entries and old_names == new_names:
        return []
    old_parent = {(kind, item): parent for kind, item, parent in old_entries}
    new_parent = {(kind, item): parent for kind, item, parent in new_entries}

    def where(parent: str | None) -> str:
        return f"folder '{new_names.get(parent, parent)}'" if parent is not None else "the top level"

    lines: list[str] = []
    for kind, item, parent in new_entries:
        key = (kind, item)
        if kind == "folder":
            if key not in old_parent:
                lines.append(f"Module folder '{new_names[item]}' added")
                continue
            if old_names.get(item) != new_names[item]:
                lines.append(f"Module folder '{old_names.get(item)}' renamed to '{new_names[item]}'")
        if key in old_parent and old_parent[key] != parent:
            label = f"Module folder '{new_names[item]}'" if kind == "folder" else f"Module '{item}'"
            lines.append(f"{label} moved to {where(parent)}")
    for kind, item, _ in old_entries:
        if kind == "folder" and (kind, item) not in new_parent:
            lines.append(f"Module folder '{old_names.get(item, item)}' removed")
    if not lines:
        common = set(old_parent) & set(new_parent)
        if [e for e in old_entries if e[:2] in common] != [e for e in new_entries if e[:2] in common]:
            lines.append("Module order changed")
    return lines


def _describe_module(module_id: str, old: Any, new: Any) -> list[str]:
    current = new if isinstance(new, dict) else old if isinstance(old, dict) else {}
    name = current.get("name", module_id)
    if old is None:
        return [f"Module '{name}' added"]
    if new is None:
        return [f"Module '{name}' removed"]
    if isinstance(old, dict) and isinstance(new, dict) and old.get("name") != new.get("name"):
        return [f"Module '{old.get('name')}' renamed to '{new.get('name')}'"]
    return [f"Module '{name}' changed"]


def _requirement_parts(old: dict, new: dict, naming: _Naming) -> list[str]:
    parts: list[str] = []
    old_attrs = old.get("attributes") if isinstance(old.get("attributes"), dict) else {}
    new_attrs = new.get("attributes") if isinstance(new.get("attributes"), dict) else {}
    keys = [k for k in naming.attribute_order if k in old_attrs or k in new_attrs]
    keys += sorted(k for k in set(old_attrs) | set(new_attrs) if k not in naming.attribute_names)
    for key in keys:
        if key in naming.system_attribute_ids:
            continue  # link mirrors: the link file entry describes the link itself
        before, after = old_attrs.get(key), new_attrs.get(key)
        if before != after:
            label = naming.attribute_names.get(key, key)
            parts.append(f"{label} {format_value(before)} → {format_value(after)}")
    if old.get("parent_id") != new.get("parent_id"):
        parent = new.get("parent_id")
        parts.append(f"moved under {parent}" if parent else "moved to top level")
    elif old.get("order") != new.get("order"):
        parts.append("reordered")
    return parts


def _describe_requirement(requirement_id: str, old: Any, new: Any, naming: _Naming) -> list[str]:
    if old is None and new is None:
        return []
    if old is None:
        return [f"{requirement_id} added"]
    if new is None:
        return [f"{requirement_id} removed"]
    if not isinstance(old, dict) or not isinstance(new, dict):
        return [f"{requirement_id} changed"]
    parts = _requirement_parts(old, new, naming)
    return [f"{requirement_id}: {', '.join(parts)}"] if parts else []


def _pairs(data: Any) -> set[tuple[str, str]]:
    if not isinstance(data, dict) or not isinstance(data.get("pairs"), list):
        return set()
    return {
        (str(p.get("source_id")), str(p.get("target_id")))
        for p in data["pairs"]
        if isinstance(p, dict)
    }


def _describe_links(config_id: str, old: Any, new: Any, naming: _Naming) -> list[str]:
    if new is None:
        return []  # the link configuration was removed: the project entry says so
    name = naming.link_names.get(config_id, config_id)
    target_project = naming.link_target_projects.get(config_id)
    prefix = f"{target_project}:" if target_project else ""
    before, after = _pairs(old), _pairs(new)
    entries = [f"Link {s} → {prefix}{t} added ({name})" for s, t in sorted(after - before)]
    entries += [f"Link {s} → {prefix}{t} removed ({name})" for s, t in sorted(before - after)]
    return entries


def describe_changes(changes: list[FileChange], project: Any = None) -> list[str]:
    """Short entries describing ``changes``, project first, then modules, requirements and links.

    ``project`` is the current project.json object (for attribute and link
    names); the old and new project.json contents in ``changes`` are used
    too, so a removed attribute is still named. Changes to link-mirror
    (system) attributes and id counters are left out: the link and the new
    requirement entries already describe them.
    """
    parsed = [(c.path, _parse(c.old), _parse(c.new)) for c in changes]
    project_versions: list[Any] = []
    for path, old, new in parsed:
        if _PROJECT_FILE.match(path):
            project_versions = [old, new]
    naming = _naming([*project_versions[:1], project, *project_versions[1:]])

    groups: dict[int, list[tuple[str, list[str]]]] = {0: [], 1: [], 2: [], 3: [], 4: []}
    for path, old, new in parsed:
        if _PROJECT_FILE.match(path):
            groups[0].append((path, _describe_project(old, new)))
        elif match := _MODULE_FILE.match(path):
            groups[1].append((path, _describe_module(match.group(1), old, new)))
        elif match := _REQUIREMENT_FILE.match(path):
            requirement_id = match.group(2)
            if old is _UNREADABLE or new is _UNREADABLE:
                groups[2].append((requirement_id, [f"{requirement_id} changed"]))
            else:
                groups[2].append((requirement_id, _describe_requirement(requirement_id, old, new, naming)))
        elif match := _LINK_FILE.match(path):
            groups[3].append((path, _describe_links(match.group(1), old, new, naming)))
        else:
            what = "added" if old is None else "removed" if new is None else "changed"
            groups[4].append((path, [f"{path} {what}"]))
    entries: list[str] = []
    for key in sorted(groups):
        for _sort_key, items in sorted(groups[key], key=lambda item: item[0]):
            entries.extend(items)
    return entries


def commit_message(entries: list[str], session_id: str | None = None, user_id: str | None = None) -> str:
    """A commit message: entries joined into the subject while it stays short, the rest in the body.

    With ``session_id``, a ``ReqMan-Session: <id>`` trailer marks the commit
    as an auto-commit that a later change of the same editing session may
    amend (squash). With ``user_id`` (server edition: the logged-in user), a
    ``ReqMan-User: <id>`` trailer follows it, so the squash window is per
    user (``user_of``); a value that is not a plain token is left out.
    """
    entries = [e for e in entries if e] or ["Update project data"]
    subject = entries[0]
    used = 1
    while used < len(entries) and len(subject) + 2 + len(entries[used]) <= MAX_SUBJECT_LENGTH:
        subject += "; " + entries[used]
        used += 1
    remaining = len(entries) - used
    parts = [subject + (f"; +{remaining} more" if remaining else "")]
    if remaining:
        listed = entries[:MAX_BODY_ENTRIES]
        body = "\n".join(listed)
        if len(entries) > len(listed):
            body += f"\n… and {len(entries) - len(listed)} more"
        parts.append(body)
    trailers = []
    if session_id:
        trailers.append(f"{SESSION_TRAILER}: {session_id}")
    if session_id and user_id and _TRAILER_VALUE.fullmatch(user_id):
        trailers.append(f"{USER_TRAILER}: {user_id}")
    if trailers:
        parts.append("\n".join(trailers))
    return "\n\n".join(parts) + "\n"


def _trailer(message: str, name: str) -> str | None:
    """The value of trailer ``name`` in the last paragraph of a commit message, or ``None``."""
    paragraphs = [p for p in message.strip().split("\n\n") if p.strip()]
    if not paragraphs:
        return None
    for line in paragraphs[-1].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == name and value.strip():
            return value.strip()
    return None


def session_of(message: str) -> str | None:
    """The ``ReqMan-Session`` trailer value of a commit message, or ``None``."""
    return _trailer(message, SESSION_TRAILER)


def user_of(message: str) -> str | None:
    """The ``ReqMan-User`` trailer value of a commit message (server edition), or ``None``."""
    return _trailer(message, USER_TRAILER)


# --- requirement history ----------------------------------------------------------


def _attribute_diffs(old: dict | None, new: dict | None) -> list[dict[str, Any]]:
    """Per-attribute, parent and order differences between two requirement objects."""
    old = old or {}
    new = new or {}
    old_attrs = old.get("attributes") if isinstance(old.get("attributes"), dict) else {}
    new_attrs = new.get("attributes") if isinstance(new.get("attributes"), dict) else {}
    diffs: list[dict[str, Any]] = []
    keys = list(new_attrs) + [k for k in old_attrs if k not in new_attrs]
    for key in keys:
        before, after = old_attrs.get(key), new_attrs.get(key)
        if before != after:
            diffs.append({"kind": "attribute", "attribute_id": key, "old": before, "new": after})
    if old.get("parent_id") != new.get("parent_id") and (old or new.get("parent_id") is not None):
        diffs.append({"kind": "parent", "attribute_id": None, "old": old.get("parent_id"), "new": new.get("parent_id")})
    if old.get("order") != new.get("order") and old:
        diffs.append({"kind": "order", "attribute_id": None, "old": old.get("order"), "new": new.get("order")})
    return diffs


def requirement_history(
    requirement_id: str, revisions: list[Revision], truncated: bool = False
) -> dict[str, Any]:
    """The change list (newest first) and attribute blame of one requirement.

    ``revisions`` are the versions of its file, newest first, as ``git log
    --first-parent`` lists them (each compared with the previous one, which
    is its first parent's version). If ``truncated``, the oldest revision is
    only the baseline the next one is compared with and is not listed
    itself. History stops at a revision whose content names another
    requirement id: the older revisions are not this requirement's, so the
    history is complete and its oldest own revision is listed (``added``).

    ``blame`` maps each attribute the requirement has now to the last listed
    commit that changed it (including setting it at creation). ``created``
    and ``modified`` are the commit (author, dates, subject) of the newest
    listed creation and of the newest listed change, or ``None``.
    """
    usable: list[tuple[Revision, Any]] = []
    for revision in revisions:
        content = _parse(revision.content)
        if isinstance(content, dict) and content.get("id") not in (None, requirement_id):
            # Older revisions are another requirement's (e.g. a file edited by hand):
            # this requirement's history starts after them, complete, with its creation.
            truncated = False
            break
        usable.append((revision, content))
    chronological = list(reversed(usable))
    baseline: Any = None
    if truncated and chronological:
        baseline = chronological[0][1]
        chronological = chronological[1:]

    entries: list[dict[str, Any]] = []
    blame: dict[str, dict[str, Any]] = {}
    previous = baseline if isinstance(baseline, dict) else None
    previous_present = baseline is not None
    for revision, content in chronological:
        current = content if isinstance(content, dict) else None
        if content is None:
            change = "removed" if previous_present else None
        elif not previous_present:
            change = "added"
        else:
            change = "modified"
        diffs = _attribute_diffs(previous, current) if content is not _UNREADABLE else []
        if change is not None and (change != "modified" or diffs or content is _UNREADABLE):
            info = {
                "commit": revision.commit,
                "author_name": revision.author_name,
                "author_email": revision.author_email,
                "authored_date": revision.authored_date,
                "date": revision.date,
                "subject": revision.subject,
            }
            entries.append({**info, "change": change, "diffs": diffs, "unreadable": content is _UNREADABLE})
            for diff in diffs:
                if diff["kind"] == "attribute":
                    blame[diff["attribute_id"]] = info
        if content is not _UNREADABLE:
            previous = current
            previous_present = content is not None
    latest = previous if isinstance(previous, dict) else {}
    latest_attrs = latest.get("attributes") if isinstance(latest.get("attributes"), dict) else {}
    blame = {key: value for key, value in blame.items() if key in latest_attrs}
    entries.reverse()
    # Who created the requirement (its newest listed creation: none when the
    # history is truncated before it) and who changed it last, from git: no
    # created_by / modified_by is stored in the requirement file.
    meta_keys = ("commit", "author_name", "author_email", "authored_date", "date", "subject")
    created = next((e for e in entries if e["change"] == "added"), None)
    return {
        "entries": entries,
        "blame": blame,
        "truncated": truncated,
        "created": {k: created[k] for k in meta_keys} if created else None,
        "modified": {k: entries[0][k] for k in meta_keys} if entries else None,
    }


# --- baseline compare ----------------------------------------------------------


def build_snapshot(files: dict[str, bytes | None]) -> dict[str, Any]:
    """A structured snapshot of a project's data files (for ``compare_snapshots``).

    ``files`` maps project-relative paths to raw bytes (a baseline's tree, or
    the working files on disk). Unreadable or missing files are skipped.
    Returns ``{"project", "modules": {id: dict}, "requirements": {module id:
    {requirement id: dict}}, "links": {config id: {(source, target), ...}}}``.
    """
    snapshot: dict[str, Any] = {"project": None, "modules": {}, "requirements": {}, "links": {}}
    for path, content in files.items():
        parsed = _parse(content)
        if parsed is None or parsed is _UNREADABLE:
            continue
        if _PROJECT_FILE.match(path):
            snapshot["project"] = parsed if isinstance(parsed, dict) else None
        elif match := _MODULE_FILE.match(path):
            snapshot["modules"][match.group(1)] = parsed if isinstance(parsed, dict) else {}
        elif match := _REQUIREMENT_FILE.match(path):
            if isinstance(parsed, dict):
                snapshot["requirements"].setdefault(match.group(1), {})[match.group(2)] = parsed
        elif match := _LINK_FILE.match(path):
            snapshot["links"][match.group(1)] = _pairs(parsed)
    return snapshot


def _attribute_definition_changes(old_project: Any, new_project: Any) -> dict[str, list[dict[str, Any]]]:
    """Added/removed/changed attribute definitions between two project.json objects (system ones excluded)."""
    before = _by_id(old_project.get("attributes") if isinstance(old_project, dict) else None)
    after = _by_id(new_project.get("attributes") if isinstance(new_project, dict) else None)
    entry = lambda item: {"id": item["id"], "name": str(item.get("name") or item["id"])}
    return {
        "added": [entry(a) for i, a in after.items() if i not in before and not a.get("system")],
        "removed": [entry(a) for i, a in before.items() if i not in after and not a.get("system")],
        "changed": [
            entry(after[i])
            for i in after
            if i in before and after[i] != before[i] and not after[i].get("system")
        ],
    }


def compare_snapshots(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Requirement-level differences between two project snapshots (``build_snapshot``).

    Per module: requirement ids added/removed and, for requirements present
    on both sides, per-attribute/parent/order diffs (``_attribute_diffs``;
    link-mirror/system attributes are left out — the ``links`` section
    describes them). Modules added/removed and attribute definition changes
    are reported at summary level; link pair changes are listed per
    configuration with its name.
    """
    naming = _naming([old.get("project"), new.get("project")])
    module_ids = sorted(
        set(old["modules"]) | set(new["modules"]) | set(old["requirements"]) | set(new["requirements"])
    )
    modules: list[dict[str, Any]] = []
    added_total = removed_total = modified_total = 0
    for module_id in module_ids:
        old_reqs: dict[str, dict] = old["requirements"].get(module_id, {})
        new_reqs: dict[str, dict] = new["requirements"].get(module_id, {})
        in_old, in_new = module_id in old["modules"], module_id in new["modules"]
        change = "added" if (in_new and not in_old) else "removed" if (in_old and not in_new) else None
        added = sorted(set(new_reqs) - set(old_reqs))
        removed = sorted(set(old_reqs) - set(new_reqs))
        modified = []
        for requirement_id in sorted(set(old_reqs) & set(new_reqs)):
            diffs = [
                diff
                for diff in _attribute_diffs(old_reqs[requirement_id], new_reqs[requirement_id])
                if not (diff["kind"] == "attribute" and diff["attribute_id"] in naming.system_attribute_ids)
            ]
            if diffs:
                modified.append({"id": requirement_id, "diffs": diffs})
        if not (change or added or removed or modified):
            continue
        module = new["modules"].get(module_id) or old["modules"].get(module_id) or {}
        modules.append(
            {
                "module_id": module_id,
                "name": str(module.get("name") or module_id),
                "change": change,
                "added": added,
                "removed": removed,
                "modified": modified,
            }
        )
        added_total += len(added)
        removed_total += len(removed)
        modified_total += len(modified)

    links_added: list[dict[str, str]] = []
    links_removed: list[dict[str, str]] = []
    for config_id in sorted(set(old["links"]) | set(new["links"])):
        before, after = old["links"].get(config_id, set()), new["links"].get(config_id, set())
        name = naming.link_names.get(config_id, config_id)
        links_added += [
            {"config_id": config_id, "name": name, "source_id": s, "target_id": t}
            for s, t in sorted(after - before)
        ]
        links_removed += [
            {"config_id": config_id, "name": name, "source_id": s, "target_id": t}
            for s, t in sorted(before - after)
        ]

    attributes = _attribute_definition_changes(old.get("project"), new.get("project"))
    return {
        "modules": modules,
        "attributes": attributes,
        "links": {"added": links_added, "removed": links_removed},
        "summary": {
            "modules_added": sum(1 for m in modules if m["change"] == "added"),
            "modules_removed": sum(1 for m in modules if m["change"] == "removed"),
            "requirements_added": added_total,
            "requirements_removed": removed_total,
            "requirements_modified": modified_total,
            "links_added": len(links_added),
            "links_removed": len(links_removed),
            "attributes_added": len(attributes["added"]),
            "attributes_removed": len(attributes["removed"]),
            "attributes_changed": len(attributes["changed"]),
        },
    }
