import { Anchor, Badge, Group, Text, Tooltip } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useSourceCode } from '../hooks/useSourceCode';
import { useVersionStatus } from '../hooks/useVersionStatus';

/**
 * App-shell footer: this frontend's version/build, plus a warning when the
 * backend reports a different tool version, and the "Source code" link of
 * the running build (AGPL-3.0 §13). Never throws: while the backend
 * version loads, or if /version fails, only the frontend part is shown.
 */
export function VersionFooter() {
  const { frontend, backend, mismatch, backendLabel } = useVersionStatus();
  const source = useSourceCode();

  return (
    <Group h="100%" px="md" gap="xs" justify="flex-end" wrap="nowrap">
      <Tooltip label={backendLabel}>
        <Text size="xs" c="dimmed" data-testid="version-footer">
          ReqManTool {frontend.version} · build {frontend.build}
        </Text>
      </Tooltip>
      <Tooltip label={source.tooltip}>
        <Anchor
          size="xs"
          href={source.link}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="footer-source-link"
        >
          Source code
        </Anchor>
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
