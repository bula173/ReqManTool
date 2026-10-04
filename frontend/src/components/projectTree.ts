import type { Project } from '../api/types';

export interface ProjectForest {
  /** Top-level projects, in the order received. */
  roots: Project[];
  /** Child projects by parent id, in the order received. */
  childrenOf: Map<string, Project[]>;
}

/**
 * Arranges projects into the subproject tree by `parent_project_id`.
 *
 * The backend already reports a dangling or cyclic parent as top level in
 * `GET /projects`; the same guard is repeated here so stale or hand-edited
 * data can never make the sidebar recurse forever: a project whose parent
 * is unknown, or whose ancestor chain loops, is shown at the top level.
 */
export function buildProjectForest(projects: Project[]): ProjectForest {
  const byId = new Map(projects.map((p) => [p.id, p]));
  const effectiveParent = (project: Project): string | null => {
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

  const roots: Project[] = [];
  const childrenOf = new Map<string, Project[]>();
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
export function flattenProjectForest(forest: ProjectForest): { project: Project; depth: number }[] {
  const result: { project: Project; depth: number }[] = [];
  const visit = (project: Project, depth: number) => {
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
export function ancestorIds(projects: Project[], projectId: string): string[] {
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

/** Select options for picking a project, indented by tree depth. */
export function projectOptions(
  forest: ProjectForest,
  exclude: Set<string> = new Set(),
): { value: string; label: string }[] {
  return flattenProjectForest(forest)
    .filter(({ project }) => !exclude.has(project.id))
    .map(({ project, depth }) => ({
      value: project.id,
      label: `${'   '.repeat(depth)}${project.name}`,
    }));
}
