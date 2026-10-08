import { Anchor, Badge, Button, Card, CloseButton, Group, Select, Stack, Text, TextInput, Title } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconGitCommit } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useModules, useProject } from '../api/queries';
import type { CommitQuery } from '../api/types';
import { CommitList } from '../components/CommitList';

const TEXT_FILTERS = ['author', 'text'] as const;
const DATE_FILTERS = ['since', 'until'] as const;

/**
 * The commit browser: the project's commits (first-parent history of
 * `current`), newest first, at project scope or limited to one module or one
 * requirement (`?module=` / `?requirement=`), filtered by author, message
 * text and date range (all kept in the URL). Opening a commit shows its
 * requirement-level changes (`CommitDetailsPage`). Read-only.
 */
export function CommitBrowserPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [params] = useSearchParams();
  if (!projectId) return null;
  // A new scope (e.g. opened from a menu) starts with fresh filter inputs.
  return <CommitBrowser key={`${projectId}/${params.get('module') ?? ''}/${params.get('requirement') ?? ''}`} projectId={projectId} />;
}

function CommitBrowser({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams();
  const projectQuery = useProject(projectId);
  const modulesQuery = useModules(projectId);

  const moduleId = params.get('module') || undefined;
  const requirementId = (moduleId && params.get('requirement')) || undefined;

  // Text filters are typed into local state and written to the URL debounced.
  const [author, setAuthor] = useState(params.get('author') ?? '');
  const [text, setText] = useState(params.get('text') ?? '');
  const [debouncedAuthor] = useDebouncedValue(author.trim(), 400);
  const [debouncedText] = useDebouncedValue(text.trim(), 400);

  const update = (changes: Record<string, string | null | undefined>) => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const [key, value] of Object.entries(changes)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        return next;
      },
      { replace: true },
    );
  };

  useEffect(() => {
    const changes: Record<string, string> = {};
    if ((params.get('author') ?? '') !== debouncedAuthor) changes.author = debouncedAuthor;
    if ((params.get('text') ?? '') !== debouncedText) changes.text = debouncedText;
    if (Object.keys(changes).length > 0) update(changes);
    // Only the debounced values drive the URL; `params` is read, not watched.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedAuthor, debouncedText]);

  const query: CommitQuery = {
    moduleId,
    requirementId,
    author: params.get('author') || undefined,
    text: params.get('text') || undefined,
    since: params.get('since') || undefined,
    until: params.get('until') || undefined,
  };
  const modules = modulesQuery.data ?? [];
  const module = modules.find((m) => m.id === moduleId);
  const hasFilters = TEXT_FILTERS.some((key) => params.get(key)) || DATE_FILTERS.some((key) => params.get(key));

  const scopeLabel = requirementId
    ? `requirement ${requirementId}`
    : moduleId
      ? `module ${module?.name ?? moduleId}`
      : `project ${projectQuery.data?.name ?? projectId}`;

  return (
    <Stack gap="md" className="reqman-fill-page">
      <div>
        <Group gap="xs">
          <IconGitCommit size={22} />
          <Title order={2}>Commits</Title>
          <Badge variant="light" color="gray">
            {scopeLabel}
          </Badge>
        </Group>
        <Text size="sm" c="dimmed">
          The history of the project's <Text span ff="monospace">current</Text> branch, newest first. Open a commit to
          see what it changed at requirement level, or to browse the project as it was then.{' '}
          <Anchor component={Link} to={`/projects/${projectId}`} size="sm">
            Back to the project
          </Anchor>
        </Text>
      </div>

      <Card withBorder>
        <Group align="flex-end" gap="sm" wrap="wrap">
          <Select
            label="Scope"
            w={240}
            data={[
              { value: '', label: 'Whole project' },
              ...modules.map((m) => ({ value: m.id, label: `Module: ${m.name}` })),
            ]}
            value={moduleId ?? ''}
            onChange={(value) => update({ module: value || null, requirement: null })}
            allowDeselect={false}
            disabled={modulesQuery.isLoading}
            searchable
          />
          {requirementId && (
            <TextInput
              label="Requirement"
              w={170}
              value={requirementId}
              readOnly
              rightSection={
                <CloseButton size="sm" aria-label="Show the whole module" onClick={() => update({ requirement: null })} />
              }
            />
          )}
          <TextInput
            label="Author"
            placeholder="Name or e-mail"
            w={180}
            value={author}
            onChange={(event) => setAuthor(event.currentTarget.value)}
            maxLength={200}
          />
          <TextInput
            label="Message contains"
            placeholder="Text"
            w={220}
            value={text}
            onChange={(event) => setText(event.currentTarget.value)}
            maxLength={200}
          />
          <TextInput
            label="From"
            type="date"
            w={150}
            value={params.get('since') ?? ''}
            onChange={(event) => update({ since: event.currentTarget.value })}
          />
          <TextInput
            label="To"
            type="date"
            w={150}
            value={params.get('until') ?? ''}
            onChange={(event) => update({ until: event.currentTarget.value })}
          />
          <Button
            variant="default"
            disabled={!hasFilters && !author && !text}
            onClick={() => {
              setAuthor('');
              setText('');
              update({ author: null, text: null, since: null, until: null });
            }}
          >
            Clear filters
          </Button>
        </Group>
      </Card>

      <Card withBorder className="reqman-card-table" style={{ flex: '0 1 auto', minHeight: 0 }}>
        <CommitList projectId={projectId} query={query} />
      </Card>
    </Stack>
  );
}
