import { Alert, Anchor, Loader, Modal, Stack, Table, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { REPOSITORY_URL } from '../actions/appActions';
import { useVersionStatus } from '../hooks/useVersionStatus';

interface AboutModalProps {
  opened: boolean;
  onClose: () => void;
}

/** Help ▸ About: tool version and build of this frontend and of the backend it talks to. */
export function AboutModal({ opened, onClose }: AboutModalProps) {
  const { frontend, backend, mismatch } = useVersionStatus();

  return (
    <Modal opened={opened} onClose={onClose} title="About ReqManTool" centered>
      <Stack gap="sm">
        <Text size="sm">
          ReqManTool is a local requirements-management tool. Project data is stored as JSON files in the data
          folder, ready to be version-controlled with git.
        </Text>
        <Table withTableBorder variant="vertical">
          <Table.Tbody>
            <Table.Tr>
              <Table.Th w={110}>Frontend</Table.Th>
              <Table.Td data-testid="about-frontend-version">
                ReqManTool {frontend.version} · build {frontend.build}
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Backend</Table.Th>
              <Table.Td data-testid="about-backend-version">
                {backend.data && `ReqManTool ${backend.data.version} · build ${backend.data.build}`}
                {backend.isLoading && <Loader size="xs" />}
                {backend.isError && !backend.data && (
                  <Text size="sm" c="red">
                    Unavailable: {backend.error.message}
                  </Text>
                )}
              </Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        {mismatch && backend.data && (
          <Alert color="orange" icon={<IconAlertTriangle size={16} />} title="Version mismatch">
            This page was built for {frontend.version}, but the backend runs {backend.data.version}. Reload the page;
            if the warning persists, rebuild the frontend.
          </Alert>
        )}
        <Text size="sm">
          Source and documentation:{' '}
          <Anchor href={REPOSITORY_URL} target="_blank" rel="noopener noreferrer">
            {REPOSITORY_URL}
          </Anchor>{' '}
          (MIT license)
        </Text>
      </Stack>
    </Modal>
  );
}
