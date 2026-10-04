import { ActionIcon, Button, Group, Modal, Stack, Text, TextInput, Tooltip } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconFolder, IconFolderSearch } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppActions } from '../actions/context';
import { ApiError } from '../api/client';
import { useConfig, useSetDataDir } from '../api/queries';
import { FolderPickerModal } from './FolderPickerModal';

interface FormValues {
  data_dir: string;
}

interface DataDirModalProps {
  opened: boolean;
  onClose: () => void;
}

/** Switches the data folder (File ▸ Open project folder, or the header folder button). */
export function DataDirModal({ opened, onClose }: DataDirModalProps) {
  const [pickerOpened, setPickerOpened] = useState(false);
  const navigate = useNavigate();
  const configQuery = useConfig();
  const setDataDir = useSetDataDir();
  const form = useForm<FormValues>({
    initialValues: { data_dir: '' },
    validate: {
      data_dir: (value) => (value.trim().length === 0 ? 'A folder path is required' : null),
    },
  });

  useEffect(() => {
    if (opened && configQuery.data) {
      form.setValues({ data_dir: configQuery.data.data_dir });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, configQuery.data]);

  const handleSubmit = form.onSubmit((values) => {
    setDataDir.mutate(values.data_dir, {
      onSuccess: (result) => {
        notifications.show({ message: `Now using data folder: ${result.data_dir}`, color: 'green' });
        onClose();
        navigate('/');
      },
      onError: (error) => {
        notifications.show({
          message: error instanceof ApiError ? error.message : 'Failed to switch data folder',
          color: 'red',
        });
      },
    });
  });

  return (
    <>
      <Modal opened={opened} onClose={onClose} title="Data folder" centered>
        <form onSubmit={handleSubmit}>
          <Stack gap="sm">
            <Text size="sm" c="dimmed">
              Path on the server's filesystem where projects are stored (e.g. a folder you manage in
              git). Switching this points the app at a different set of projects.
            </Text>
            <Group align="flex-end" gap="xs">
              <TextInput
                label="Folder path"
                placeholder="/path/to/requirements"
                required
                style={{ flex: 1 }}
                {...form.getInputProps('data_dir')}
              />
              <Button
                variant="default"
                leftSection={<IconFolderSearch size={16} />}
                onClick={() => setPickerOpened(true)}
              >
                Browse…
              </Button>
            </Group>
            <Group justify="flex-end" mt="sm">
              <Button variant="default" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" loading={setDataDir.isPending}>
                Use this folder
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>
      <FolderPickerModal
        opened={pickerOpened}
        initialPath={form.values.data_dir || undefined}
        onClose={() => setPickerOpened(false)}
        onSelect={(path) => form.setFieldValue('data_dir', path)}
      />
    </>
  );
}

/** Header button: runs the shared "Open project folder" action. */
export function DataDirSettings() {
  const { run } = useAppActions();
  return (
    <Tooltip label="Data folder">
      <ActionIcon variant="default" size="lg" aria-label="Data folder settings" onClick={() => run('file.openFolder')}>
        <IconFolder size={18} />
      </ActionIcon>
    </Tooltip>
  );
}
