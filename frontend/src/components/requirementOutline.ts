import type { AttributeDefinition, Requirement } from '../api/types';

/**
 * Pure helpers of the Module tab's requirement outline (left panel): the
 * visible rows, the filter, the node label and the drag-and-drop plan. The
 * server owns every rule (parent constraints, ordering); the plan only maps
 * a drop onto one positional Move (`PUT .../parent` with `index`).
 */

/** The built-in ObjectText attribute id. */
export const OBJECT_TEXT_ATTRIBUTE_ID = 'object_text';

export interface OutlineRow {
  requirement: Requirement;
  depth: number;
  hasChildren: boolean;
  expanded: boolean;
}

/** Children by parent id (null = module root), each list sorted by `order`, then id (as the server does). */
export function childrenByParent(requirements: Requirement[]): Map<string | null, Requirement[]> {
  const childrenOf = new Map<string | null, Requirement[]>();
  for (const req of requirements) {
    const siblings = childrenOf.get(req.parent_id) ?? [];
    siblings.push(req);
    childrenOf.set(req.parent_id, siblings);
  }
  for (const siblings of childrenOf.values()) {
    siblings.sort((a, b) => a.order - b.order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  }
  return childrenOf;
}

/** Ids of every requirement below `requirementId`. */
export function descendantIdsOf(requirements: Requirement[], requirementId: string): Set<string> {
  const childrenOf = childrenByParent(requirements);
  const result = new Set<string>();
  const stack = [...(childrenOf.get(requirementId) ?? [])];
  while (stack.length > 0) {
    const req = stack.pop()!;
    if (result.has(req.id)) continue;
    result.add(req.id);
    stack.push(...(childrenOf.get(req.id) ?? []));
  }
  return result;
}

/** Ids of every ancestor of `requirementId` (nearest first); stops on a cycle. */
export function ancestorIdsOf(requirements: Requirement[], requirementId: string): string[] {
  const byId = new Map(requirements.map((r) => [r.id, r]));
  const result: string[] = [];
  let current = byId.get(requirementId)?.parent_id ?? null;
  while (current !== null && !result.includes(current)) {
    result.push(current);
    current = byId.get(current)?.parent_id ?? null;
  }
  return result;
}

/** Ids of every requirement that has children (what "Collapse all" collapses). */
export function parentIds(requirements: Requirement[]): Set<string> {
  const ids = new Set(requirements.map((r) => r.id));
  return new Set(requirements.map((r) => r.parent_id).filter((id): id is string => id !== null && ids.has(id)));
}

/** The start of a requirement's ObjectText, on one line. */
export function objectTextSnippet(requirement: Requirement, maxLength = 80): string {
  const value = requirement.attributes[OBJECT_TEXT_ATTRIBUTE_ID];
  if (value === null || value === undefined) return '';
  const text = String(value).replace(/\s+/g, ' ').trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

/**
 * The project's Object Type attribute, if it has one: the attribute with id
 * `object_type` (as in the built-in templates), else one named "Object Type".
 */
export function findObjectTypeAttribute(attributes: AttributeDefinition[]): AttributeDefinition | null {
  return (
    attributes.find((a) => a.id === 'object_type') ??
    attributes.find((a) => a.name.trim().toLowerCase() === 'object type') ??
    null
  );
}

/**
 * The outline rows to render, depth-first in sibling order.
 *
 * Without a filter, a collapsed node hides its subtree. With a filter
 * (case-insensitive substring of the id or the ObjectText), the matches and
 * their ancestors are shown, fully expanded, whatever was collapsed.
 */
export function buildOutlineRows(requirements: Requirement[], collapsed: Set<string>, filter = ''): OutlineRow[] {
  const childrenOf = childrenByParent(requirements);
  const query = filter.trim().toLowerCase();
  let visible: Set<string> | null = null;
  if (query) {
    visible = new Set<string>();
    for (const req of requirements) {
      const text = String(req.attributes[OBJECT_TEXT_ATTRIBUTE_ID] ?? '').toLowerCase();
      if (req.id.toLowerCase().includes(query) || text.includes(query)) {
        visible.add(req.id);
        for (const ancestorId of ancestorIdsOf(requirements, req.id)) visible.add(ancestorId);
      }
    }
  }

  const rows: OutlineRow[] = [];
  const seen = new Set<string>();
  const walk = (parentId: string | null, depth: number) => {
    for (const req of childrenOf.get(parentId) ?? []) {
      if (seen.has(req.id)) continue; // defensive: never loop on bad data
      seen.add(req.id);
      if (visible && !visible.has(req.id)) continue;
      const children = (childrenOf.get(req.id) ?? []).filter((c) => !visible || visible.has(c.id));
      const hasChildren = children.length > 0;
      const expanded = hasChildren && (visible !== null || !collapsed.has(req.id));
      rows.push({ requirement: req, depth, hasChildren, expanded });
      if (expanded) walk(req.id, depth + 1);
    }
  };
  walk(null, 0);
  return rows;
}

/**
 * Where a dragged node lands relative to the node under the pointer: as its
 * previous or next sibling, as its last child (`inside`) or as its first
 * child (`first-child`, the bottom edge of an expanded node, whose children
 * follow it).
 */
export type DropPosition = 'before' | 'after' | 'inside' | 'first-child';

/**
 * One Move call: the new parent (null = module root) and the 0-based
 * position among that parent's children after the move.
 */
export interface DropPlan {
  parentId: string | null;
  index: number;
}

/**
 * Plans dropping `draggedId` relative to `targetId` (`null` = the end of the
 * module root, with position `inside`). Returns null when the drop changes
 * nothing or is never allowed (onto itself or into its own subtree; the
 * server rejects that too).
 */
export function planDrop(
  requirements: Requirement[],
  draggedId: string,
  targetId: string | null,
  position: DropPosition,
): DropPlan | null {
  const byId = new Map(requirements.map((r) => [r.id, r]));
  const dragged = byId.get(draggedId);
  if (!dragged) return null;
  if (targetId !== null) {
    if (targetId === draggedId || !byId.has(targetId)) return null;
    if (descendantIdsOf(requirements, draggedId).has(targetId)) return null;
  }
  const target = targetId !== null ? byId.get(targetId)! : null;
  const intoTarget = target === null || position === 'inside' || position === 'first-child';
  const parentId = intoTarget ? (target?.id ?? null) : target.parent_id;
  const siblings = childrenByParent(requirements).get(parentId) ?? [];
  const others = siblings.filter((r) => r.id !== draggedId);

  let index: number;
  if (position === 'first-child' && target !== null) index = 0;
  else if (intoTarget) index = others.length;
  else {
    const targetIndex = others.findIndex((r) => r.id === target.id);
    index = position === 'before' ? targetIndex : targetIndex + 1;
  }

  // Already at that position under that parent: the move would change nothing.
  if (dragged.parent_id === parentId && siblings.findIndex((r) => r.id === draggedId) === index) return null;
  return { parentId, index };
}
