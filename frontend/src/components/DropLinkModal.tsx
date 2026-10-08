import { Button, Group, Modal, Radio, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useLinkRequirements, useModules } from '../api/queries';
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
  const linkRequirements = useLinkRequirements(projectId);
  const [selected, setSelected] = useState<string | null>(
    () => options.find((option) => !option.exists)?.config.id ?? null,
  );
  const moduleName = (id: string) => modulesQuery.data?.find((m) => m.id === id)?.name ?? id;
  const option = options.find((o) => o.config.id === selected && !o.exists) ?? null;

  const handleLink = () => {
    if (!option) return;
    linkRequirements.mutate(
      { config_id: option.config.id, source_id: option.source_id, target_id: option.target_id },
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
                key={o.config.id}
                value={o.config.id}
                disabled={o.exists}
                label={linkOptionLabel(o)}
                description={`${moduleName(o.source_module_id)} > ${moduleName(o.target_module_id)}${o.exists ? ' (already linked)' : ''}`}
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
