import { Badge, Group, Text, Tooltip } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useBackendVersion } from '../api/queries';
import { FRONTEND_VERSION_INFO, isVersionMismatch } from '../buildInfo';

/**
 * App-shell footer: this frontend's version/build, plus a warning when the
 * backend reports a different tool version. Never throws: while the backend
 * version loads, or if /version fails, only the frontend part is shown.
 */
export function VersionFooter() {
  const backend = useBackendVersion();
  const frontend = FRONTEND_VERSION_INFO;
  const mismatch = isVersionMismatch(frontend, backend.data);

  const backendLabel = backend.data
    ? `Backend: ReqManTool ${backend.data.version} · build ${backend.data.build}`
    : backend.isError
      ? `Backend version unavailable: ${backend.error.message}`
      : 'Checking backend version…';

  return (
    <Group h="100%" px="md" gap="xs" justify="flex-end" wrap="nowrap">
      <Tooltip label={backendLabel}>
        <Text size="xs" c="dimmed" data-testid="version-footer">
          ReqManTool {frontend.version} · build {frontend.build}
        </Text>
      </Tooltip>
      {backend.isError && !backend.data && (
        <Text size="xs" c="dimmed" fs="italic">
          (backend version unavailable)
        </Text>
      )}
      {mismatch && backend.data && (
        <Tooltip
          multiline
          w={320}
          label={`This page was built for ${frontend.version}, but the backend runs ${backend.data.version} (build ${backend.data.build}). Reload the page; if the warning persists, rebuild the frontend.`}
        >
          <Badge color="orange" variant="light" size="sm" leftSection={<IconAlertTriangle size={12} />}>
            Version mismatch: backend {backend.data.version}
          </Badge>
        </Tooltip>
      )}
    </Group>
  );
}
