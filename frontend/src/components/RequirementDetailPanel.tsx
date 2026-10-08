import { ActionIcon, Alert, Anchor, Badge, Card, Center, Group, Loader, Stack, Tabs, Text, Timeline, Title, Tooltip } from '@mantine/core';
import { IconGitCommit, IconHistory, IconX } from '@tabler/icons-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useIncomingLinks, useRequirementHistory } from '../api/queries';
import type {
  AttributeDefinition,
  HistoryCommit,
  HistoryDiff,
  HistoryEntry,
  Requirement,
  VerificationStatus,
} from '../api/types';
import { toggleHint } from '../layout/sidePanels';
import { commitBrowserPath, commitPath } from './commits';
import { EditableAttributeCell } from './EditableAttributeCell';
import { StatusBadge } from './ReportParts';
import { richTextToPlain } from './richText';

interface RequirementDetailPanelProps {
  projectId: string;
  moduleId: string;
  attributes: AttributeDefinition[];
  requirement: Requirement | null;
  onSave: (requirement: Requirement, attrId: string, value: unknown) => void;
  saving: boolean;
  onNavigateToLinked: (requirementId: string) => void;
  onClose: () => void;
  /**
   * The computed, read-only Verification status (requirements modules only;
   * omitted elsewhere). `status` null: none (e.g. a heading) or not loaded.
   */
  verification?: { status: VerificationStatus | null; loading: boolean; error: boolean };
}

const MAX_VALUE_LENGTH = 120;

/**
 * Links into this requirement from referencing projects. They are stored
 * only in the source project (no mirror attribute here), so the server
 * computes them on read (`GET /projects/{id}/incoming-links`). Shown only
 * when there are some; a badge double-click opens the source requirement.
 */
function IncomingCrossProjectLinks({
  projectId,
  moduleId,
  requirementId,
  onNavigate,
}: {
  projectId: string;
  moduleId: string;
  requirementId: string;
  onNavigate: (linkEnd: string) => void;
}) {
  const incomingQuery = useIncomingLinks(projectId, moduleId);
  if (incomingQuery.isError) {
    return (
      <Text size="xs" c="red">
        Links from other projects could not be loaded
      </Text>
    );
  }
  const links = (incomingQuery.data ?? []).filter((link) => link.target_id === requirementId);
  if (links.length === 0) return null;
  const groups = new Map<string, typeof links>();
  for (const link of links) {
    const key = `${link.project_name} – ${link.config_name}`;
    groups.set(key, [...(groups.get(key) ?? []), link]);
  }
  return (
    <>
      {[...groups.entries()].map(([label, group]) => (
        <div key={label}>
          <Text size="xs" c="dimmed">
            {label} (from another project)
          </Text>
          <Group gap={4}>
            {group.map((link) => {
              const end = `${link.project_id}:${link.source_id}`;
              return (
                <Tooltip key={end} label={`Double-click to open ${end}`} openDelay={300}>
                  <Badge
                    variant="light"
                    color="blue"
                    style={{ cursor: 'pointer' }}
                    onDoubleClick={() => onNavigate(end)}
                  >
                    {'<'} {end}
                  </Badge>
                </Tooltip>
              );
            })}
          </Group>
        </div>
      ))}
    </>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '(empty)';
  // Rich text is shown as its plain projection.
  const text = typeof value === 'string' ? richTextToPlain(value).replace(/\s*\n\s*/g, ' ') : JSON.stringify(value);
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
  verification,
}: RequirementDetailPanelProps) {
  const [tab, setTab] = useState<string | null>('details');
  const historyQuery = useRequirementHistory(projectId, moduleId, requirement?.id, requirement !== null);
  const history = historyQuery.data;
  const blame = history?.available ? history.blame : {};
  const attributeName = (id: string) => attributes.find((a) => a.id === id)?.name ?? id;

  return (
    // Fills its side panel (layout/SidePanelLayout.tsx), which owns the width.
    <Card withBorder style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
      <Group justify="space-between" mb="sm" wrap="nowrap">
        <Title order={5}>Requirement details</Title>
        <Tooltip label={toggleHint('Hide', 'requirement detail panel', 'view.toggleDetailPanel')}>
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
              {history?.available && (history.created || history.modified) && (
                <div>
                  <Text size="xs" c="dimmed">
                    Created / last changed (from the git history)
                  </Text>
                  {history.created && (
                    <Text size="xs" title={history.created.author_email}>
                      Created by {history.created.author_name}, {formatDate(history.created.date)}
                    </Text>
                  )}
                  {history.modified && history.modified.commit !== history.created?.commit && (
                    <Text size="xs" title={history.modified.author_email}>
                      Last changed by {history.modified.author_name}, {formatDate(history.modified.date)}
                    </Text>
                  )}
                </div>
              )}
              {verification && (
                <div>
                  <Tooltip
                    label="Computed from the linked test cases and their latest executions (read-only, never stored)"
                    multiline
                    w={280}
                    withArrow
                    openDelay={300}
                  >
                    <Text size="xs" c="dimmed" style={{ cursor: 'help', width: 'fit-content' }}>
                      Verification status
                    </Text>
                  </Tooltip>
                  {verification.loading ? (
                    <Loader size="xs" />
                  ) : verification.error ? (
                    <Text size="sm" c="red">
                      Could not be computed
                    </Text>
                  ) : verification.status ? (
                    <StatusBadge status={verification.status} />
                  ) : (
                    <Text size="sm" c="dimmed">
                      – (not a reportable object)
                    </Text>
                  )}
                </div>
              )}
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
              <IncomingCrossProjectLinks
                projectId={projectId}
                moduleId={moduleId}
                requirementId={requirement.id}
                onNavigate={onNavigateToLinked}
              />
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
                          <Anchor component={Link} to={commitPath(projectId, entry.commit)} ff="monospace" size="xs">
                            {entry.commit.slice(0, 8)}
                          </Anchor>{' '}
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
                {history.entries.length > 0 && (
                  <Anchor component={Link} to={commitBrowserPath(projectId, moduleId, requirement.id)} size="xs">
                    Show in the commit browser
                  </Anchor>
                )}
              </Stack>
            )}
          </Tabs.Panel>
        </Tabs>
      )}
    </Card>
  );
}
