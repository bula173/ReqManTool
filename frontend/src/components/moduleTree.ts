import type { ComboboxItem, ComboboxItemGroup } from '@mantine/core';
import type { Module, ModuleTreeEntry, ModuleTreeFolderEntry } from '../api/types';

/**
 * Helpers for a project's module tree (`GET /projects/{id}/module-tree`):
 * modules arranged in nested, purely organisational folders. The server owns
 * the rules (folder ids, names, nesting, what may move where); these helpers
 * only shape the tree for display and pickers.
 */

export function isFolderEntry(entry: ModuleTreeEntry): entry is ModuleTreeFolderEntry {
  return 'folder' in entry;
}

/** Separator of folder paths in labels ("Requirements / System"). */
export const FOLDER_PATH_SEPARATOR = ' / ';

/** A node of the displayed tree: the tree's entries joined with the loaded modules. */
export type ModuleTreeNode = ModuleTreeModuleNode | ModuleTreeFolderNode;

interface NodePosition {
  /** The folder holding the node (null: the top level). */
  parentId: string | null;
  /** Position among the parent's entries (modules and folders) and their count: Move Up/Down availability. */
  index: number;
  siblingCount: number;
  /** Nesting depth (top level = 0). */
  depth: number;
}

export interface ModuleTreeModuleNode extends NodePosition {
  kind: 'module';
  module: Module;
}

export interface ModuleTreeFolderNode extends NodePosition {
  kind: 'folder';
  id: string;
  name: string;
  children: ModuleTreeNode[];
  /** The folder's entries as returned by the server (for `isEmpty`, moves). */
  entry: ModuleTreeFolderEntry;
}

/**
 * The displayed tree: `tree` entries with their `Module`s. Entries naming a
 * module that is not (yet) in `modules` are skipped; modules `tree` does not
 * list follow at the top level (e.g. while the tree is loading, or failed to
 * load, the modules are shown flat in their display order).
 */
export function buildModuleTreeNodes(tree: ModuleTreeEntry[] | undefined, modules: Module[]): ModuleTreeNode[] {
  const byId = new Map(modules.map((m) => [m.id, m]));
  const placed = new Set<string>();
  const build = (entries: ModuleTreeEntry[], parentId: string | null, depth: number): ModuleTreeNode[] => {
    const visible = entries.filter((entry) => isFolderEntry(entry) || (byId.has(entry.module) && !placed.has(entry.module)));
    return visible.map((entry, index) => {
      const position = { parentId, index, siblingCount: visible.length, depth };
      if (isFolderEntry(entry)) {
        return {
          kind: 'folder',
          id: entry.folder,
          name: entry.name,
          entry,
          children: build(entry.children, entry.folder, depth + 1),
          ...position,
        };
      }
      placed.add(entry.module);
      return { kind: 'module', module: byId.get(entry.module)!, ...position };
    });
  };
  const nodes = build(tree ?? [], null, 0);
  const unplaced = modules.filter((m) => !placed.has(m.id));
  const count = nodes.length + unplaced.length;
  return [
    ...nodes.map((node) => ({ ...node, siblingCount: count })),
    ...unplaced.map(
      (module, offset): ModuleTreeNode => ({
        kind: 'module',
        module,
        parentId: null,
        index: nodes.length + offset,
        siblingCount: count,
        depth: 0,
      }),
    ),
  ];
}

/** Every folder of the tree, depth-first in display order, with its depth (top level = 0) and path. */
export function flattenFolders(
  tree: ModuleTreeEntry[] | undefined,
): { folder: ModuleTreeFolderEntry; depth: number; path: string }[] {
  const result: { folder: ModuleTreeFolderEntry; depth: number; path: string }[] = [];
  const visit = (entries: ModuleTreeEntry[], depth: number, prefix: string) => {
    for (const entry of entries) {
      if (!isFolderEntry(entry)) continue;
      const path = prefix ? `${prefix}${FOLDER_PATH_SEPARATOR}${entry.name}` : entry.name;
      result.push({ folder: entry, depth, path });
      visit(entry.children, depth + 1, path);
    }
  };
  visit(tree ?? [], 0, '');
  return result;
}

/** The folder path of every module in a folder (top-level modules are absent). */
export function moduleFolderPaths(tree: ModuleTreeEntry[] | undefined): Map<string, string> {
  const result = new Map<string, string>();
  const visit = (entries: ModuleTreeEntry[], path: string) => {
    for (const entry of entries) {
      if (isFolderEntry(entry)) {
        visit(entry.children, path ? `${path}${FOLDER_PATH_SEPARATOR}${entry.name}` : entry.name);
      } else if (path) {
        result.set(entry.module, path);
      }
    }
  };
  visit(tree ?? [], '');
  return result;
}

/** Ids of folder `folderId` and every folder inside it. */
export function folderSubtreeIds(folder: ModuleTreeFolderEntry): Set<string> {
  const result = new Set<string>([folder.folder]);
  const visit = (entries: ModuleTreeEntry[]) => {
    for (const entry of entries) {
      if (isFolderEntry(entry)) {
        result.add(entry.folder);
        visit(entry.children);
      }
    }
  };
  visit(folder.children);
  return result;
}

/** The folder holding the module or folder `id` (null: the top level); undefined when it is not in the tree. */
export function parentFolderOf(
  tree: ModuleTreeEntry[] | undefined,
  kind: 'module' | 'folder',
  id: string,
): string | null | undefined {
  const search = (entries: ModuleTreeEntry[], parentId: string | null): string | null | undefined => {
    for (const entry of entries) {
      if (kind === 'module' ? !isFolderEntry(entry) && entry.module === id : isFolderEntry(entry) && entry.folder === id) {
        return parentId;
      }
    }
    for (const entry of entries) {
      if (isFolderEntry(entry)) {
        const found = search(entry.children, entry.folder);
        if (found !== undefined) return found;
      }
    }
    return undefined;
  };
  return search(tree ?? [], null);
}

/** Select value of "the top level" in folder pickers (folder ids are never empty). */
export const TOP_LEVEL_FOLDER = '';

/**
 * Destination options for a folder picker: the top level, then every folder
 * indented by depth. `exclude` folders (a folder being moved and its
 * subfolders) are left out.
 */
export function folderPickerOptions(
  tree: ModuleTreeEntry[] | undefined,
  exclude: Set<string> = new Set(),
): { value: string; label: string }[] {
  return [
    { value: TOP_LEVEL_FOLDER, label: '(Top level)' },
    ...flattenFolders(tree)
      .filter(({ folder }) => !exclude.has(folder.folder))
      .map(({ folder, depth }) => ({ value: folder.folder, label: `${' '.repeat(depth)}${folder.name}` })),
  ];
}

/**
 * Select data for a module picker grouped by folder: top-level modules as
 * plain options, then one group per folder holding modules, labelled with
 * its path, in tree order. `toOption` builds each module's option (and may
 * return null to leave it out).
 */
export function groupedModuleOptions<T extends ComboboxItem>(
  tree: ModuleTreeEntry[] | undefined,
  modules: Module[],
  toOption: (module: Module) => T | null,
): (T | ComboboxItemGroup<T>)[] {
  const byId = new Map(modules.map((m) => [m.id, m]));
  const placed = new Set<string>();
  const topLevel: T[] = [];
  const groups: ComboboxItemGroup<T>[] = [];
  const optionOf = (moduleId: string): T | null => {
    const module = byId.get(moduleId);
    if (!module || placed.has(moduleId)) return null;
    placed.add(moduleId);
    return toOption(module);
  };
  const visit = (entries: ModuleTreeEntry[], path: string) => {
    const items: T[] = [];
    for (const entry of entries) {
      if (isFolderEntry(entry)) continue;
      const option = optionOf(entry.module);
      if (option) items.push(option);
    }
    if (path) {
      if (items.length > 0) groups.push({ group: path, items });
    } else {
      topLevel.push(...items);
    }
    for (const entry of entries) {
      if (isFolderEntry(entry)) {
        visit(entry.children, path ? `${path}${FOLDER_PATH_SEPARATOR}${entry.name}` : entry.name);
      }
    }
  };
  visit(tree ?? [], '');
  for (const module of modules) {
    if (placed.has(module.id)) continue;
    placed.add(module.id);
    const option = toOption(module);
    if (option) topLevel.push(option);
  }
  return [...topLevel, ...groups];
}

/** Where the module or folder `id` sits in `tree` (as the server reorders it); null when it is not in the tree. */
export function treePosition(
  tree: ModuleTreeEntry[] | undefined,
  kind: 'module' | 'folder',
  id: string,
): { parentId: string | null; index: number; siblingCount: number } | null {
  const search = (
    entries: ModuleTreeEntry[],
    parentId: string | null,
  ): { parentId: string | null; index: number; siblingCount: number } | null => {
    const index = entries.findIndex((entry) =>
      kind === 'module' ? !isFolderEntry(entry) && entry.module === id : isFolderEntry(entry) && entry.folder === id,
    );
    if (index >= 0) return { parentId, index, siblingCount: entries.length };
    for (const entry of entries) {
      if (isFolderEntry(entry)) {
        const found = search(entry.children, entry.folder);
        if (found) return found;
      }
    }
    return null;
  };
  return search(tree ?? [], null);
}
