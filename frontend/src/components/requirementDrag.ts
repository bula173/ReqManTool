/**
 * The HTML5 drag-and-drop payload of requirement rows (drag-and-drop
 * linking). It works between module tabs, and between browser windows of the
 * app (detached module windows), since the payload travels in the
 * `DataTransfer`, not in app state. It is project-qualified, so a row of one
 * project dropped on a row of another links through a cross-project link
 * configuration (`useDropLink`). It is scoped to the data folder it was
 * dragged in: project and requirement ids repeat across folders, so a drop in
 * a window showing another folder is ignored (like the pending link and the
 * clipboard).
 */

/** The payload's MIME type: dragover handlers only see the type, the data is readable on drop. */
export const REQUIREMENT_DRAG_MIME = 'application/x-reqman-requirements+json';

/** The MIME type of a dragged module tab (tab bar reordering). */
export const TAB_DRAG_MIME = 'application/x-reqman-tab';

export interface RequirementDragPayload {
  kind: 'reqman.requirements';
  /** 2: adds `dataDir` (a version-1 payload names no data folder and is ignored). */
  version: 2;
  /** The data folder the row was dragged in. */
  dataDir: string;
  projectId: string;
  moduleId: string;
  /** One requirement for now; several once bulk selection exists. */
  requirementIds: string[];
}

export function setRequirementDragData(
  dataTransfer: DataTransfer,
  payload: Omit<RequirementDragPayload, 'kind' | 'version'>,
): void {
  const full: RequirementDragPayload = { kind: 'reqman.requirements', version: 2, ...payload };
  dataTransfer.setData(REQUIREMENT_DRAG_MIME, JSON.stringify(full));
  // Dropped into a text field or another application: the ids as text.
  dataTransfer.setData('text/plain', payload.requirementIds.join(', '));
  dataTransfer.effectAllowed = 'copyLink';
}

export function hasRequirementDragData(dataTransfer: DataTransfer | null): boolean {
  return dataTransfer !== null && Array.from(dataTransfer.types).includes(REQUIREMENT_DRAG_MIME);
}

/** The dropped payload, or null when it is missing or malformed. */
export function readRequirementDragData(dataTransfer: DataTransfer): RequirementDragPayload | null {
  try {
    const data = JSON.parse(dataTransfer.getData(REQUIREMENT_DRAG_MIME)) as Partial<RequirementDragPayload>;
    if (
      data.kind !== 'reqman.requirements' ||
      typeof data.dataDir !== 'string' ||
      typeof data.projectId !== 'string' ||
      typeof data.moduleId !== 'string' ||
      !Array.isArray(data.requirementIds) ||
      !data.requirementIds.every((id) => typeof id === 'string')
    ) {
      return null;
    }
    return {
      kind: 'reqman.requirements',
      version: 2,
      dataDir: data.dataDir,
      projectId: data.projectId,
      moduleId: data.moduleId,
      requirementIds: data.requirementIds,
    };
  } catch {
    return null;
  }
}
