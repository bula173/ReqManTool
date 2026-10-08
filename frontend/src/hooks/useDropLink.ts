import { notifications } from '@mantine/notifications';
import { useCallback } from 'react';
import { useAppActions } from '../actions/context';
import { ApiError } from '../api/client';
import { fetchLinkOptions, useLinkRequirements } from '../api/queries';
import type { LinkOption } from '../api/types';
import type { RequirementDragPayload } from '../components/requirementDrag';

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError || err instanceof Error ? err.message : fallback;
}

export function linkOptionLabel(option: LinkOption): string {
  return `${option.config.name}: ${option.source_id} > ${option.target_id}`;
}

/**
 * Drag-and-drop linking: a requirement row (`payload`) dropped on requirement
 * `toId` of the module `projectId`/`moduleId`. The server lists the link
 * configurations between the two modules in either direction
 * (`fetchLinkOptions`); one → the link is created in its configured
 * direction, several → the user picks one (`dropLink` dialog), none → a
 * warning and no link. An existing link is reported, never duplicated, and the
 * server's link rules stay the authority.
 */
export function useDropLink(projectId: string) {
  const { state } = useAppActions();
  const { commands, modules } = state;
  const linkRequirements = useLinkRequirements(projectId);
  const createLink = linkRequirements.mutateAsync;

  return useCallback(
    async (payload: RequirementDragPayload, toId: string, toModuleId: string) => {
      if (payload.projectId !== projectId) {
        notifications.show({
          title: 'No link created',
          message: 'Links between requirements of different projects are not supported yet.',
          color: 'yellow',
        });
        return;
      }
      if (payload.requirementIds.length !== 1) {
        notifications.show({ message: 'Drop one requirement at a time to link it.', color: 'yellow' });
        return;
      }
      const fromId = payload.requirementIds[0];
      if (fromId === toId) return;

      let options: LinkOption[];
      try {
        options = await fetchLinkOptions(projectId, fromId, toId);
      } catch (err) {
        notifications.show({ message: errorMessage(err, 'Failed to look up link configurations'), color: 'red' });
        return;
      }

      if (options.length === 0) {
        const name = (id: string) => modules.find((m) => m.id === id)?.name ?? id;
        const between =
          payload.moduleId === toModuleId
            ? `within module ${name(toModuleId)}`
            : `between modules ${name(payload.moduleId)} and ${name(toModuleId)}`;
        notifications.show({
          title: 'No link created',
          message: `There is no link configuration ${between}. Add one on the project page first.`,
          color: 'yellow',
        });
        return;
      }
      if (options.every((option) => option.exists)) {
        notifications.show({
          message: `Already linked (${options.map(linkOptionLabel).join('; ')}). No duplicate created.`,
          color: 'blue',
        });
        return;
      }
      if (options.length > 1) {
        commands.openDialog({ kind: 'dropLink', projectId, fromId, toId, options });
        return;
      }

      const [option] = options;
      try {
        await createLink({ config_id: option.config.id, source_id: option.source_id, target_id: option.target_id });
        notifications.show({ message: `Linked ${linkOptionLabel(option)}`, color: 'green' });
      } catch (err) {
        notifications.show({ message: errorMessage(err, 'Failed to link requirements'), color: 'red' });
      }
    },
    [projectId, modules, commands, createLink],
  );
}
