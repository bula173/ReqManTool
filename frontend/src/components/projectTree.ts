import { isProjectStub, type ProjectListEntry } from '../api/types';

export interface ProjectForest {
  /** Top-level projects, in the order received. */
  roots: ProjectListEntry[];
  /** Child projects by parent id, in the order received. */
  childrenOf: Map<string, ProjectListEntry[]>;
}

/**
 * Arranges projects into the subproject tree by `parent_project_id`.
 *
 * The backend already reports a dangling or cyclic parent as top level in
 * `GET /projects`; the same guard is repeated here so stale or hand-edited
 * data can never make the sidebar recurse forever: a project whose parent
 * is unknown, or whose ancestor chain loops, is shown at the top level.
 * A stub (a project that cannot be loaded) is always top level too: its
 * stored parent cannot be trusted. Its readable subprojects stay under it.
 */
export function buildProjectForest(projects: ProjectListEntry[]): ProjectForest {
  const byId = new Map(projects.map((p) => [p.id, p]));
  const effectiveParent = (project: ProjectListEntry): string | null => {
    if (isProjectStub(project)) return null;
    const parentId = project.parent_project_id ?? null;
    if (parentId === null || !byId.has(parentId)) return null;
    const seen = new Set<string>([project.id]);
    let node: string | null = parentId;
    while (node !== null) {
      if (seen.has(node)) return null; // loops back: break the cycle here
      seen.add(node);
      const next: string | null = byId.get(node)?.parent_project_id ?? null;
      node = next !== null && byId.has(next) ? next : null;
    }
    return parentId;
  };

  const roots: ProjectListEntry[] = [];
  const childrenOf = new Map<string, ProjectListEntry[]>();
  for (const project of projects) {
    const parentId = effectiveParent(project);
    if (parentId === null) {
      roots.push(project);
    } else {
      const siblings = childrenOf.get(parentId) ?? [];
      siblings.push(project);
      childrenOf.set(parentId, siblings);
    }
  }
  return { roots, childrenOf };
}

/** Depth-first (pre-order) list of every project with its tree depth. */
export function flattenProjectForest(forest: ProjectForest): { project: ProjectListEntry; depth: number }[] {
  const result: { project: ProjectListEntry; depth: number }[] = [];
  const visit = (project: ProjectListEntry, depth: number) => {
    result.push({ project, depth });
    for (const child of forest.childrenOf.get(project.id) ?? []) visit(child, depth + 1);
  };
  for (const root of forest.roots) visit(root, 0);
  return result;
}

/** Ids of every project below `projectId` in the tree. */
export function descendantIds(forest: ProjectForest, projectId: string): Set<string> {
  const result = new Set<string>();
  const stack = [...(forest.childrenOf.get(projectId) ?? [])];
  while (stack.length > 0) {
    const project = stack.pop()!;
    if (result.has(project.id)) continue;
    result.add(project.id);
    stack.push(...(forest.childrenOf.get(project.id) ?? []));
  }
  return result;
}

/** Ids of every ancestor of `projectId` (nearest first). */
export function ancestorIds(projects: ProjectListEntry[], projectId: string): string[] {
  const forest = buildProjectForest(projects);
  const parentOf = new Map<string, string>();
  for (const [parentId, children] of forest.childrenOf) {
    for (const child of children) parentOf.set(child.id, parentId);
  }
  const result: string[] = [];
  let node = parentOf.get(projectId);
  while (node !== undefined && !result.includes(node)) {
    result.push(node);
    node = parentOf.get(node);
  }
  return result;
}

/**
 * Select options for picking a parent project, indented by tree depth.
 *
 * A stub (a project that cannot be loaded) is listed disabled so the tree
 * stays readable, but it can never be picked: it cannot be a parent.
 */
export function projectOptions(
  forest: ProjectForest,
  exclude: Set<string> = new Set(),
): { value: string; label: string; disabled: boolean }[] {
  return flattenProjectForest(forest)
    .filter(({ project }) => !exclude.has(project.id))
    .map(({ project, depth }) => {
      const stub = isProjectStub(project);
      return {
        value: project.id,
        label: `${'   '.repeat(depth)}${project.name}${stub ? ' (cannot be opened)' : ''}`,
        disabled: stub,
      };
    });
}
