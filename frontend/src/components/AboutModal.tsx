import { Alert, Anchor, Button, Code, Loader, Modal, Stack, Table, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useState } from 'react';
import { REPOSITORY_URL } from '../actions/appActions';
import { useSourceCode } from '../hooks/useSourceCode';
import { useVersionStatus } from '../hooks/useVersionStatus';
import { ThirdPartyLicensesModal } from './ThirdPartyLicensesModal';

interface AboutModalProps {
  opened: boolean;
  onClose: () => void;
}

/**
 * Help ▸ About: tool version and build of this frontend and of the backend
 * it talks to, the licence notice, the source code of the running build
 * (AGPL-3.0 §13), and the third-party licences.
 */
export function AboutModal({ opened, onClose }: AboutModalProps) {
  const { frontend, backend, mismatch } = useVersionStatus();
  const source = useSourceCode();
  const about = source.about;
  const [licensesOpen, setLicensesOpen] = useState(false);

  return (
    <>
      <Modal opened={opened && !licensesOpen} onClose={onClose} title="About ReqManTool" centered size="lg">
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
              <Table.Tr>
                <Table.Th>Source code</Table.Th>
                <Table.Td data-testid="about-source">
                  <Anchor href={source.link} target="_blank" rel="noopener noreferrer" size="sm">
                    {source.link}
                  </Anchor>
                  {about.isLoading && <Loader size="xs" ml="xs" />}
                  {about.data && !source.ref && (
                    <Text size="xs" c="dimmed">
                      Build {about.data.build} is not a published revision (development build or local changes); the
                      link opens the repository.
                    </Text>
                  )}
                  {about.isError && !about.data && (
                    <Text size="xs" c="dimmed">
                      The backend did not report its source location ({about.error.message}); the link opens the
                      upstream repository.
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Th>Licence</Table.Th>
                <Table.Td data-testid="about-license">
                  <Code>{about.data?.license ?? 'AGPL-3.0-only'}</Code>
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
            Copyright © 2026 Marcin Kwiatkowski. ReqManTool is free software: you can redistribute it and/or modify it
            under the terms of the{' '}
            <Anchor href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noopener noreferrer">
              GNU Affero General Public License, version 3
            </Anchor>{' '}
            only. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
            implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the licence for details. A
            commercial licence is also available; see{' '}
            <Anchor href={`${REPOSITORY_URL}/blob/main/LICENSING.md`} target="_blank" rel="noopener noreferrer">
              LICENSING.md
            </Anchor>
            .
          </Text>
          <Text size="sm" c="dimmed">
            The source code link above is the complete source of the version you are using. If this server runs a
            modified version, its operator must publish the modified source and point the link to it (setting{' '}
            <Code>source_url</Code> in the config file or <Code>REQMAN_SOURCE_URL</Code>).
          </Text>
          <Button variant="default" onClick={() => setLicensesOpen(true)} style={{ alignSelf: 'flex-start' }}>
            Third-party licences
          </Button>
        </Stack>
      </Modal>
      <ThirdPartyLicensesModal opened={opened && licensesOpen} onClose={() => setLicensesOpen(false)} />
    </>
  );
}
