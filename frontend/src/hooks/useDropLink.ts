import { notifications } from '@mantine/notifications';
import { useCallback } from 'react';
import { useAppActions } from '../actions/context';
import { ApiError } from '../api/client';
import { fetchLinkOptions, useLinkInProject } from '../api/queries';
import type { LinkOption, Module } from '../api/types';
import type { PendingLinkSource } from '../actions/pendingLink';
import type { DialogRequest } from '../actions/types';
import type { RequirementDragPayload } from '../components/requirementDrag';

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError || err instanceof Error ? err.message : fallback;
}

/** A link end, project-qualified when it is not in the project holding the configuration. */
function optionEnd(option: LinkOption, side: 'source' | 'target'): string {
  const projectId = side === 'source' ? option.source_project_id : option.target_project_id;
  const id = side === 'source' ? option.source_id : option.target_id;
  return projectId && projectId !== option.project_id ? `${projectId}:${id}` : id;
}

export function linkOptionLabel(option: LinkOption): string {
  return `${option.config.name}: ${optionEnd(option, 'source')} > ${optionEnd(option, 'target')}`;
}

/** Two requirements to link: `from` (dragged / pending link source) and `to` (drop / "Make link" target). */
export interface LinkEnds {
  fromProjectId: string;
  fromModuleId: string;
  fromId: string;
  /** The open project: the one the target requirement belongs to. */
  toProjectId: string;
  toModuleId: string;
  toId: string;
}

/** What `resolveAndLink` did: `picking` → the `dropLink` dialog is open; `failed` covers no options, duplicates and errors. */
export type LinkOutcome = 'linked' | 'picking' | 'failed' | 'ignored';

export interface ResolveAndLinkDeps {
  /** The open (target) project's modules, to name them in the "no link configuration" warning. */
  modules: Module[];
  openDialog: (request: DialogRequest) => void;
  createLink: (variables: {
    projectId: string;
    body: { config_id: string; source_id: string; target_id: string };
  }) => Promise<unknown>;
  /** Passed on to the `dropLink` dialog: the two-step link it completes (cleared once linked). */
  pendingSource?: PendingLinkSource;
}

/**
 * Links `from` to `to` the way drag-and-drop linking does. The server lists
 * the link configurations between the two modules in either direction
 * (`fetchLinkOptions`); one → the link is created in its configured
 * direction, several → the user picks one (`dropLink` dialog), none → a
 * warning and no link. An existing link is reported, never duplicated, and the
 * server's link rules stay the authority. A requirement of another project
 * links through the cross-project link configurations of either project that
 * target the other; the link is created in the project holding the
 * configuration. Shared by drag-and-drop and two-step ("Start link" →
 * "Make link") linking.
 */
export async function resolveAndLink(ends: LinkEnds, deps: ResolveAndLinkDeps): Promise<LinkOutcome> {
  const { fromProjectId, fromModuleId, fromId, toProjectId, toModuleId, toId } = ends;
  const { modules, openDialog, createLink, pendingSource } = deps;
  const crossProject = fromProjectId !== toProjectId;
  if (fromId === toId && !crossProject) return 'ignored';

  let options: LinkOption[];
  try {
    options = await fetchLinkOptions(toProjectId, fromId, toId, fromProjectId);
  } catch (err) {
    notifications.show({ message: errorMessage(err, 'Failed to look up link configurations'), color: 'red' });
    return 'failed';
  }

  if (options.length === 0 && crossProject) {
    notifications.show({
      title: 'No link created',
      message:
        `There is no link configuration between these modules of projects ${fromProjectId} and ${toProjectId}. ` +
        'Reference the other project and add a cross-project link configuration on the project page first.',
      color: 'yellow',
    });
    return 'failed';
  }
  if (options.length === 0) {
    const name = (id: string) => modules.find((m) => m.id === id)?.name ?? id;
    const between =
      fromModuleId === toModuleId
        ? `within module ${name(toModuleId)}`
        : `between modules ${name(fromModuleId)} and ${name(toModuleId)}`;
    notifications.show({
      title: 'No link created',
      message: `There is no link configuration ${between}. Add one on the project page first.`,
      color: 'yellow',
    });
    return 'failed';
  }
  if (options.every((option) => option.exists)) {
    notifications.show({
      message: `Already linked (${options.map(linkOptionLabel).join('; ')}). No duplicate created.`,
      color: 'blue',
    });
    return 'failed';
  }
  if (options.length > 1) {
    openDialog({
      kind: 'dropLink',
      projectId: toProjectId,
      fromId: crossProject ? `${fromProjectId}:${fromId}` : fromId,
      toId,
      options,
      pendingSource,
    });
    return 'picking';
  }

  const [option] = options;
  try {
    await createLink({
      projectId: option.project_id,
      body: { config_id: option.config.id, source_id: option.source_id, target_id: option.target_id },
    });
    notifications.show({ message: `Linked ${linkOptionLabel(option)}`, color: 'green' });
    return 'linked';
  } catch (err) {
    notifications.show({ message: errorMessage(err, 'Failed to link requirements'), color: 'red' });
    return 'failed';
  }
}

/**
 * Drag-and-drop linking: a requirement row (`payload`) dropped on requirement
 * `toId` of the module `projectId`/`toModuleId`, linked by `resolveAndLink`.
 * A row of another project (e.g. a detached window or tab of another project)
 * links through a cross-project link configuration.
 */
export function useDropLink(projectId: string) {
  const { state } = useAppActions();
  const { commands, modules } = state;
  const linkInProject = useLinkInProject();
  const createLink = linkInProject.mutateAsync;

  return useCallback(
    async (payload: RequirementDragPayload, toId: string, toModuleId: string) => {
      if (payload.requirementIds.length !== 1) {
        notifications.show({ message: 'Drop one requirement at a time to link it.', color: 'yellow' });
        return;
      }
      await resolveAndLink(
        {
          fromProjectId: payload.projectId,
          fromModuleId: payload.moduleId,
          fromId: payload.requirementIds[0],
          toProjectId: projectId,
          toModuleId,
          toId,
        },
        { modules, openDialog: commands.openDialog, createLink },
      );
    },
    [projectId, modules, commands, createLink],
  );
}
