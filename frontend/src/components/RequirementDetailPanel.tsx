import { ActionIcon, Alert, Badge, Card, Center, Group, Loader, Stack, Tabs, Text, Timeline, Title, Tooltip } from '@mantine/core';
import { IconGitCommit, IconHistory, IconX } from '@tabler/icons-react';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useRequirementHistory } from '../api/queries';
import type { AttributeDefinition, HistoryCommit, HistoryDiff, HistoryEntry, Requirement } from '../api/types';
import { EditableAttributeCell } from './EditableAttributeCell';

interface RequirementDetailPanelProps {
  projectId: string;
  moduleId: string;
  attributes: AttributeDefinition[];
  requirement: Requirement | null;
  onSave: (requirement: Requirement, attrId: string, value: unknown) => void;
  saving: boolean;
  onNavigateToLinked: (requirementId: string) => void;
  onClose: () => void;
}

const MAX_VALUE_LENGTH = 120;

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '(empty)';
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return text.length > MAX_VALUE_LENGTH ? `${text.slice(0, MAX_VALUE_LENGTH - 1)}…` : text;
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function blameLabel(commit: HistoryCommit): string {
  return `Last changed by ${commit.author_name} on ${formatDate(commit.date)}: ${commit.subject}`;
}

const CHANGE_LABELS: Record<HistoryEntry['change'], { label: string; color: string }> = {
  added: { label: 'Created', color: 'green' },
  modified: { label: 'Changed', color: 'blue' },
  removed: { label: 'Removed', color: 'red' },
};

function DiffLine({ diff, attributeName }: { diff: HistoryDiff; attributeName: (id: string) => string }) {
  const label =
    diff.kind === 'parent' ? 'Parent' : diff.kind === 'order' ? 'Position' : attributeName(diff.attribute_id ?? '');
  return (
    <Text size="xs" style={{ wordBreak: 'break-word' }}>
      <Text span size="xs" fw={600}>
        {label}:
      </Text>{' '}
      <Text span size="xs" c="dimmed" td="line-through">
        {formatValue(diff.old)}
      </Text>{' '}
      → {formatValue(diff.new)}
    </Text>
  );
}

/**
 * The requirement detail panel of the module page: a Details tab (every
 * attribute, editable, with "last changed by/when" tooltips on the labels
 * from the attribute blame) and a History tab (the requirement's commits with
 * per-attribute diffs). Both read GET .../requirements/{id}/history, which is
 * refetched after every requirement change (and by live sync).
 *
 * The panel is as high as the module page's table area: the title and the
 * tab list stay put, and the tab content scrolls on its own.
 */
export function RequirementDetailPanel({
  projectId,
  moduleId,
  attributes,
  requirement,
  onSave,
  saving,
  onNavigateToLinked,
  onClose,
}: RequirementDetailPanelProps) {
  const [tab, setTab] = useState<string | null>('details');
  const historyQuery = useRequirementHistory(projectId, moduleId, requirement?.id, requirement !== null);
  const history = historyQuery.data;
  const blame = history?.available ? history.blame : {};
  const attributeName = (id: string) => attributes.find((a) => a.id === id)?.name ?? id;

  return (
    <Card withBorder w={320} style={{ flexShrink: 0, minHeight: 0 }}>
      <Group justify="space-between" mb="sm" wrap="nowrap">
        <Title order={5}>Requirement details</Title>
        <Tooltip label="Hide the detail panel (View › Requirement detail panel)">
          <ActionIcon variant="subtle" size="sm" aria-label="Hide requirement details" onClick={onClose}>
            <IconX size={14} />
          </ActionIcon>
        </Tooltip>
      </Group>
      {!requirement && (
        <Text c="dimmed" size="sm">
          Select a requirement to see its full details.
        </Text>
      )}
      {requirement && (
        <Tabs
          value={tab}
          onChange={setTab}
          keepMounted={false}
          style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
        >
          <Tabs.List mb="sm">
            <Tabs.Tab value="details">Details</Tabs.Tab>
            <Tabs.Tab value="history" leftSection={<IconHistory size={14} />}>
              History
            </Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="details" className="reqman-card-scroll">
            <Stack gap="sm">
              <div>
                <Text size="xs" c="dimmed">
                  Id
                </Text>
                <Text fw={600}>{requirement.id}</Text>
              </div>
              {attributes.map((attr) => {
                const lastChange = blame[attr.id];
                return (
                  <div key={attr.id}>
                    {lastChange ? (
                      <Tooltip label={blameLabel(lastChange)} multiline w={280} withArrow openDelay={300}>
                        <Text size="xs" c="dimmed" style={{ cursor: 'help', width: 'fit-content' }}>
                          {attr.name}
                        </Text>
                      </Tooltip>
                    ) : (
                      <Text size="xs" c="dimmed">
                        {attr.name}
                      </Text>
                    )}
                    <EditableAttributeCell
                      attr={attr}
                      value={requirement.attributes[attr.id]}
                      onSave={(value) => onSave(requirement, attr.id, value)}
                      disabled={attr.system ? undefined : saving}
                      projectId={projectId}
                      onNavigateToLinked={onNavigateToLinked}
                    />
                  </div>
                );
              })}
            </Stack>
          </Tabs.Panel>

          <Tabs.Panel value="history" className="reqman-card-scroll">
            {historyQuery.isLoading && (
              <Center py="md">
                <Loader size="sm" />
              </Center>
            )}
            {historyQuery.isError && (
              <Alert color="red" title="Failed to load the history">
                {historyQuery.error instanceof ApiError || historyQuery.error instanceof Error
                  ? historyQuery.error.message
                  : 'Unknown error'}
              </Alert>
            )}
            {history && !history.available && (
              <Alert color="gray" title="History not available">
                {history.reason ?? 'This project has no readable git history.'}
              </Alert>
            )}
            {history?.available && (
              <Stack gap="sm">
                {history.uncommitted && (
                  <Text size="xs" c="yellow.8">
                    This requirement has changes that are not committed yet (see the project page).
                  </Text>
                )}
                {history.entries.length === 0 && (
                  <Text size="sm" c="dimmed">
                    No history recorded yet.
                  </Text>
                )}
                {history.entries.length > 0 && (
                  <Timeline bulletSize={18} lineWidth={2} active={history.entries.length}>
                    {history.entries.map((entry) => (
                      <Timeline.Item
                        key={entry.commit}
                        bullet={<IconGitCommit size={12} />}
                        title={
                          <Group gap={6} wrap="nowrap">
                            <Badge size="xs" variant="light" color={CHANGE_LABELS[entry.change].color}>
                              {CHANGE_LABELS[entry.change].label}
                            </Badge>
                            <Text size="xs" c="dimmed">
                              {formatDate(entry.date)}
                            </Text>
                          </Group>
                        }
                      >
                        <Tooltip label={entry.author_email} withArrow openDelay={300}>
                          <Text size="xs" fw={500} style={{ width: 'fit-content' }}>
                            {entry.author_name}
                          </Text>
                        </Tooltip>
                        <Text size="xs" c="dimmed" lineClamp={2} title={entry.subject}>
                          {entry.subject}
                        </Text>
                        {entry.unreadable && (
                          <Text size="xs" c="red">
                            The file could not be read at this revision.
                          </Text>
                        )}
                        {entry.change !== 'removed' &&
                          entry.diffs.map((diff, index) => (
                            <DiffLine key={index} diff={diff} attributeName={attributeName} />
                          ))}
                      </Timeline.Item>
                    ))}
                  </Timeline>
                )}
                {history.truncated && (
                  <Text size="xs" c="dimmed">
                    Older changes are not shown.
                  </Text>
                )}
              </Stack>
            )}
          </Tabs.Panel>
        </Tabs>
      )}
    </Card>
  );
}
