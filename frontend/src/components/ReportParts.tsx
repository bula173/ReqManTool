import { Alert, Anchor, Badge, Button, Card, Group, Menu, SimpleGrid, Text, Tooltip } from '@mantine/core';
import { IconDownload, IconInfoCircle } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import type { ReportDataSource } from '../api/types';
import { modulePath, projectPath } from '../layout/moduleTabs';
import { STATUS_COLORS, STATUS_LABELS } from './reportFormat';

/** Shared building blocks of the report pages (traceability, verification, test execution). */

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge size="sm" variant="light" color={STATUS_COLORS[status] ?? 'gray'}>
      {STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

/** A requirement/test/record id that opens it in its module (with its text as a tooltip). */
export function ObjectRef({
  projectId,
  moduleId,
  id,
  text,
}: {
  projectId: string;
  moduleId: string;
  id: string;
  text?: string;
}) {
  const link = (
    <Anchor
      component={Link}
      to={`${modulePath(projectId, moduleId)}?highlight=${encodeURIComponent(id)}`}
      size="sm"
      ff="monospace"
    >
      {id}
    </Anchor>
  );
  return text ? (
    <Tooltip label={text} multiline maw={420} openDelay={300} withinPortal>
      {link}
    </Tooltip>
  ) : (
    link
  );
}

export function StatGrid({ items }: { items: { label: string; value: ReactNode; hint?: string }[] }) {
  return (
    <SimpleGrid cols={{ base: 2, sm: 4 }}>
      {items.map((item) => (
        <div key={item.label}>
          <Text size="xl" fw={600}>
            {item.value}
          </Text>
          <Text size="xs" c="dimmed">
            {item.label}
          </Text>
          {item.hint && (
            <Text size="xs" c="dimmed">
              {item.hint}
            </Text>
          )}
        </div>
      ))}
    </SimpleGrid>
  );
}

/** "Export" menu of download links. */
export function ExportMenu({
  formats,
  disabled,
}: {
  formats: { label: string; url: string }[];
  disabled?: boolean;
}) {
  return (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>
        <Button size="xs" variant="default" leftSection={<IconDownload size={14} />} disabled={disabled}>
          Export
        </Button>
      </Menu.Target>
      <Menu.Dropdown>
        {formats.map((format) => (
          <Menu.Item key={format.label} component="a" href={format.url} rel="noopener">
            {format.label}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}

export function ReportError({ error, title }: { error: unknown; title: string }) {
  return (
    <Alert icon={<IconInfoCircle size={16} />} color="red" title={title}>
      {error instanceof ApiError || error instanceof Error ? error.message : 'Unknown error'}
    </Alert>
  );
}

export function ReportCard({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Text fw={600}>{title}</Text>
        {right}
      </Group>
      {children}
    </Card>
  );
}

/** A list of object refs, or a dimmed "none". */
export function RefList({
  projectId,
  moduleId,
  ids,
  texts,
  empty = 'None',
}: {
  projectId: string;
  moduleId: string;
  ids: string[];
  texts?: Map<string, string>;
  empty?: string;
}) {
  if (ids.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        {empty}
      </Text>
    );
  }
  return (
    <Group gap={8}>
      {ids.map((id) => (
        <ObjectRef key={id} projectId={projectId} moduleId={moduleId} id={id} text={texts?.get(id)} />
      ))}
    </Group>
  );
}

/**
 * Which data the report was computed from: the project's current files at a
 * commit (and the baseline tagged there, linked), uncommitted changes, the
 * latest baseline otherwise, and the project lock.
 */
export function DataSourceLine({ projectId, source }: { projectId: string; source: ReportDataSource | undefined }) {
  if (!source) return null;
  const baselineLink = (name: string) => (
    <Anchor key={name} component={Link} to={`${projectPath(projectId)}/baselines/${encodeURIComponent(name)}`} size="xs">
      {name}
    </Anchor>
  );
  return (
    <Text size="xs" c="dimmed" mt={4}>
      Data: the current project{' '}
      {source.commit ? (
        <>
          at commit <code>{source.commit.slice(0, 12)}</code>
          {source.baselines.length > 0 && (
            <>
              {' '}
              = baseline {source.baselines.map((name, index) => [index > 0 ? ', ' : '', baselineLink(name)])}
            </>
          )}
          {source.pending_changes > 0 && ` plus ${source.pending_changes} uncommitted change(s)`}
          {source.latest_baseline && !source.baselines.includes(source.latest_baseline.name) && (
            <> (latest baseline: {baselineLink(source.latest_baseline.name)}, not the same data)</>
          )}
        </>
      ) : (
        '(no git history: not tied to a commit or baseline)'
      )}
      {source.locked && '; the project is locked'}.
    </Text>
  );
}
