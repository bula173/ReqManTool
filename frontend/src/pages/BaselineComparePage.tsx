import { Alert, Anchor, Card, Group, Loader, Select, Stack, Text, Title } from '@mantine/core';
import { IconArrowRight, IconInfoCircle } from '@tabler/icons-react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useBaselineCompare, useBaselines, useProject } from '../api/queries';
import { CompareResultView } from '../components/CompareResult';
import { compareAttributeNames } from '../components/compareNames';

const CURRENT = 'current';

/**
 * Compares two baselines, or a baseline with `current` (the working files on
 * disk, including uncommitted changes): a summary plus the per-module
 * requirement changes with per-attribute diffs, link changes and attribute
 * definition changes. Read-only.
 */
export function BaselineComparePage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [params, setParams] = useSearchParams();
  const from = params.get('from') ?? '';
  const to = params.get('to') ?? '';
  const baselinesQuery = useBaselines(projectId);
  const projectQuery = useProject(projectId);
  const compareQuery = useBaselineCompare(projectId, from || undefined, to || undefined);

  if (!projectId) return null;

  const names = (baselinesQuery.data?.baselines ?? []).map((b) => b.name);
  const sideOptions = (extra: string[]) => [
    ...extra.map((name) => ({ value: name, label: name === CURRENT ? 'Current state (working files)' : name })),
    ...names.map((name) => ({ value: name, label: `Baseline ${name}` })),
  ];
  const setSide = (key: 'from' | 'to') => (value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const result = compareQuery.data;
  // Attribute names: the live project's definitions, plus any named in the
  // compare payload itself (added/removed/changed definitions); unknown ids
  // (e.g. an attribute that existed only at the baseline) fall back to the id.
  const resolveAttribute = compareAttributeNames(projectQuery.data?.attributes, result);

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>Compare baselines</Title>
        <Text size="sm" c="dimmed">
          Requirement-level differences between two baselines, or a baseline and the current state (including
          uncommitted changes).{' '}
          <Anchor component={Link} to={`/projects/${projectId}`} size="sm">
            Back to the project
          </Anchor>
        </Text>
      </div>

      <Card withBorder>
        <Group align="flex-end" gap="sm">
          <Select
            label="From"
            placeholder="Pick a baseline"
            data={sideOptions([CURRENT])}
            value={from || null}
            onChange={setSide('from')}
            searchable
            w={260}
          />
          <IconArrowRight size={18} style={{ marginBottom: 8 }} />
          <Select
            label="To"
            placeholder="Pick a baseline or the current state"
            data={sideOptions([CURRENT])}
            value={to || null}
            onChange={setSide('to')}
            searchable
            w={260}
          />
        </Group>
        {baselinesQuery.data && !baselinesQuery.data.available && (
          <Alert color="orange" mt="sm" p="xs">
            <Text size="sm">{baselinesQuery.data.reason ?? 'This project has no readable repository.'}</Text>
          </Alert>
        )}
        {baselinesQuery.data?.available && names.length === 0 && (
          <Text size="sm" c="dimmed" mt="sm">
            This project has no baselines yet; create one from the project page's Git panel.
          </Text>
        )}
      </Card>

      {(!from || !to) && (
        <Text size="sm" c="dimmed">
          Pick both sides to compare.
        </Text>
      )}
      {compareQuery.isLoading && <Loader size="sm" />}
      {compareQuery.isError && (
        <Alert icon={<IconInfoCircle size={16} />} color="red" title="Failed to compare">
          {compareQuery.error instanceof ApiError || compareQuery.error instanceof Error
            ? compareQuery.error.message
            : 'Unknown error'}
        </Alert>
      )}

      {result && (
        <CompareResultView
          result={result}
          attributeName={resolveAttribute}
          title={
            <>
              {result.from.name === CURRENT ? 'Current state' : `Baseline ${result.from.name}`} →{' '}
              {result.to.name === CURRENT ? 'Current state' : `Baseline ${result.to.name}`}
            </>
          }
        />
      )}
    </Stack>
  );
}
