import { Alert, Anchor, Breadcrumbs, Button, Group, Loader, Modal, Stack, Text, UnstyledButton } from '@mantine/core';
import { IconFolder } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { useBrowseFolders } from '../api/queries';

interface FolderPickerModalProps {
  opened: boolean;
  initialPath?: string;
  onClose: () => void;
  onSelect: (path: string) => void;
}

function breadcrumbSegments(path: string): { label: string; path: string }[] {
  const parts = path.split('/').filter(Boolean);
  const segments: { label: string; path: string }[] = [{ label: '/', path: '/' }];
  let current = '';
  for (const part of parts) {
    current += `/${part}`;
    segments.push({ label: part, path: current });
  }
  return segments;
}

export function FolderPickerModal({ opened, initialPath, onClose, onSelect }: FolderPickerModalProps) {
  const [browsePath, setBrowsePath] = useState<string | undefined>(initialPath);

  useEffect(() => {
    if (opened) setBrowsePath(initialPath);
  }, [opened, initialPath]);

  const browseQuery = useBrowseFolders(browsePath, opened);

  const handleConfirm = () => {
    if (browseQuery.data) {
      onSelect(browseQuery.data.path);
      onClose();
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Choose a folder" centered size="lg">
      <Stack gap="sm">
        {browseQuery.isLoading && <Loader size="sm" />}
        {browseQuery.isError && (
          <Alert color="red">
            {browseQuery.error instanceof ApiError ? browseQuery.error.message : 'Failed to browse folders'}
          </Alert>
        )}
        {browseQuery.data && (
          <>
            <Breadcrumbs>
              {breadcrumbSegments(browseQuery.data.path).map((seg) => (
                <Anchor key={seg.path} size="sm" onClick={() => setBrowsePath(seg.path)}>
                  {seg.label}
                </Anchor>
              ))}
            </Breadcrumbs>
            <Stack gap={2} mah={320} style={{ overflowY: 'auto' }}>
              {browseQuery.data.directories.length === 0 && (
                <Text size="sm" c="dimmed">
                  No subfolders here.
                </Text>
              )}
              {browseQuery.data.directories.map((dir) => (
                <UnstyledButton
                  key={dir.path}
                  onClick={() => setBrowsePath(dir.path)}
                  style={{ padding: '6px 8px', borderRadius: 4 }}
                >
                  <Group gap="xs">
                    <IconFolder size={16} />
                    <Text size="sm">{dir.name}</Text>
                  </Group>
                </UnstyledButton>
              ))}
            </Stack>
          </>
        )}
        <Group justify="space-between" mt="sm">
          <Text size="xs" c="dimmed">
            Current: {browseQuery.data?.path ?? '—'}
          </Text>
          <Group>
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={handleConfirm} disabled={!browseQuery.data}>
              Select this folder
            </Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}
