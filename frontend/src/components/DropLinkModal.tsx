import { Button, Group, Modal, Radio, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useLinkInProject, useModules } from '../api/queries';
import type { LinkOption } from '../api/types';
import { linkOptionLabel } from '../hooks/useDropLink';

interface DropLinkModalProps {
  projectId: string;
  fromId: string;
  toId: string;
  options: LinkOption[];
  onClose: () => void;
}

/**
 * Drag-and-drop linking with several link configurations between the two
 * modules: the user picks the one to create. Links that already exist are
 * listed but cannot be picked (no duplicates).
 */
export function DropLinkModal({ projectId, fromId, toId, options, onClose }: DropLinkModalProps) {
  const modulesQuery = useModules(projectId);
  const linkRequirements = useLinkInProject();
  // Keyed by project and configuration: across projects, both may have a configuration of the same id.
  const optionKey = (o: LinkOption) => `${o.project_id}/${o.config.id}`;
  const [selected, setSelected] = useState<string | null>(() => {
    const first = options.find((option) => !option.exists);
    return first ? optionKey(first) : null;
  });
  // Modules of the drop target's project are named; another project's modules show their id.
  const moduleName = (id: string, ownerId: string) =>
    (ownerId === projectId ? modulesQuery.data?.find((m) => m.id === id)?.name : undefined) ?? id;
  const option = options.find((o) => optionKey(o) === selected && !o.exists) ?? null;

  const handleLink = () => {
    if (!option) return;
    linkRequirements.mutate(
      {
        projectId: option.project_id,
        body: { config_id: option.config.id, source_id: option.source_id, target_id: option.target_id },
      },
      {
        onSuccess: () => {
          notifications.show({ message: `Linked ${linkOptionLabel(option)}`, color: 'green' });
          onClose();
        },
        onError: (err) =>
          notifications.show({
            message: err instanceof ApiError ? err.message : 'Failed to link requirements',
            color: 'red',
          }),
      },
    );
  };

  return (
    <Modal opened onClose={onClose} title={`Link ${fromId} and ${toId}`} centered>
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Several link configurations join these modules. Pick the link to create.
        </Text>
        <Radio.Group value={selected} onChange={setSelected} aria-label="Link configuration">
          <Stack gap="xs">
            {options.map((o) => (
              <Radio
                key={optionKey(o)}
                value={optionKey(o)}
                disabled={o.exists}
                label={linkOptionLabel(o)}
                description={`${moduleName(o.source_module_id, o.source_project_id)} > ${moduleName(o.target_module_id, o.target_project_id)}${o.exists ? ' (already linked)' : ''}`}
              />
            ))}
          </Stack>
        </Radio.Group>
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleLink} disabled={!option} loading={linkRequirements.isPending}>
            Link
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
