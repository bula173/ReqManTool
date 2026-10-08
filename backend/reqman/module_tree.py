"""Pure helpers for a project's module tree (``Project.module_order``, project format 7).

The tree is a list of entries in display order: module ids (``str``) and
``ModuleFolder``s whose ``children`` are entries again. These helpers never
touch the disk; ``ProjectService`` owns the rules (ids, names, depth, what
may move where) and decides when the tree is stored.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from .models import ModuleFolder

Entries = list  # list[str | ModuleFolder]


def effective_tree(entries: Iterable, existing_module_ids: Iterable[str]) -> Entries:
    """The tree as displayed: ``entries`` applied tolerantly to the modules that exist.

    A module entry naming a module that does not exist, or repeating one
    listed earlier, is skipped. A folder repeating an earlier folder's id is
    dropped and its entries take its place (they are not lost). Folders,
    empty ones included, are kept. Modules the tree does not list follow at
    the top level in id order (``existing_module_ids`` order), so older
    projects (no tree) list in id order. Returns new folder objects; the
    input is not modified.
    """
    existing = list(existing_module_ids)
    existing_set = set(existing)
    seen_modules: set[str] = set()
    seen_folders: set[str] = set()

    def walk(items: Iterable) -> Entries:
        result: Entries = []
        for entry in items:
            if isinstance(entry, ModuleFolder):
                if entry.id in seen_folders:
                    result.extend(walk(entry.children))
                    continue
                seen_folders.add(entry.id)
                result.append(ModuleFolder(id=entry.id, name=entry.name, children=walk(entry.children)))
            elif isinstance(entry, str) and entry in existing_set and entry not in seen_modules:
                seen_modules.add(entry)
                result.append(entry)
        return result

    tree = walk(entries)
    tree.extend(module_id for module_id in existing if module_id not in seen_modules)
    return tree


def iter_folders(entries: Iterable, depth: int = 1) -> Iterator[tuple[ModuleFolder, int]]:
    """Every folder of the tree with its depth (top level = 1), depth-first, in display order."""
    for entry in entries:
        if isinstance(entry, ModuleFolder):
            yield entry, depth
            yield from iter_folders(entry.children, depth + 1)


def module_ids(entries: Iterable) -> list[str]:
    """The tree's module ids depth-first in display order (a folder's modules where the folder is)."""
    result: list[str] = []
    for entry in entries:
        if isinstance(entry, ModuleFolder):
            result.extend(module_ids(entry.children))
        else:
            result.append(entry)
    return result


def find_folder(entries: Iterable, folder_id: str) -> ModuleFolder | None:
    for folder, _ in iter_folders(entries):
        if folder.id == folder_id:
            return folder
    return None


def find_container(entries: Entries, kind: str, entry_id: str) -> tuple[Entries, ModuleFolder | None] | None:
    """The list holding the module (``kind`` ``"module"``) or folder with ``entry_id``, and its folder.

    The folder is ``None`` for the top level. ``None`` when not found.
    """

    def matches(entry: object) -> bool:
        if kind == "folder":
            return isinstance(entry, ModuleFolder) and entry.id == entry_id
        return isinstance(entry, str) and entry == entry_id

    def search(items: Entries, owner: ModuleFolder | None) -> tuple[Entries, ModuleFolder | None] | None:
        for entry in items:
            if matches(entry):
                return items, owner
        for entry in items:
            if isinstance(entry, ModuleFolder):
                found = search(entry.children, entry)
                if found is not None:
                    return found
        return None

    return search(entries, None)


def index_of(items: Entries, kind: str, entry_id: str) -> int:
    for index, entry in enumerate(items):
        if kind == "folder" and isinstance(entry, ModuleFolder) and entry.id == entry_id:
            return index
        if kind == "module" and isinstance(entry, str) and entry == entry_id:
            return index
    raise ValueError(f"{kind} '{entry_id}' is not in this list")


def height(folder: ModuleFolder) -> int:
    """Levels of folders in ``folder``'s subtree, itself included (an empty folder is 1)."""
    return 1 + max((height(child) for child in folder.children if isinstance(child, ModuleFolder)), default=0)


def depth_of(entries: Entries, folder_id: str) -> int:
    """The depth of folder ``folder_id`` (top level = 1); 0 if it is not in the tree."""
    for folder, depth in iter_folders(entries):
        if folder.id == folder_id:
            return depth
    return 0


def has_folders(entries: Iterable) -> bool:
    return any(isinstance(entry, ModuleFolder) for entry in entries)
