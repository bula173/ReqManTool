import { Alert, Button, Group, Loader, Modal, ScrollArea, Select, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMemo, useState } from 'react';
import { ApiError } from '../api/client';
import { MODULE_TYPE_LABELS } from '../api/moduleTypes';
import {
  useApplyModuleSkeleton,
  useModule,
  useModuleSkeletonPreview,
  useModuleTemplates,
  useProject,
  useTemplates,
} from '../api/queries';
import type { ModuleType, TemplateSummary } from '../api/types';

interface ApplySkeletonModalProps {
  projectId: string;
  moduleId: string;
  moduleName: string;
  moduleType: ModuleType;
  onClose: () => void;
}

/**
 * A chapter source as the picker stores it: `m:<module template id>` for a
 * module template, `p:<project template id>#<module key>` for one module of
 * a project template (the RBC-specific chapters of e.g. SyRS).
 */
function moduleTemplateSource(templateId: string): string {
  return `m:${templateId}`;
}

function projectModuleSource(templateId: string, moduleKey: string): string {
  return `p:${templateId}#${moduleKey}`;
}

function parseSource(value: string): { templateId: string; moduleKey: string | null } {
  if (value.startsWith('p:')) {
    const rest = value.slice(2);
    const at = rest.lastIndexOf('#');
    if (at > 0) return { templateId: rest.slice(0, at), moduleKey: rest.slice(at + 1) };
  }
  return { templateId: value.startsWith('m:') ? value.slice(2) : value, moduleKey: null };
}

/** The origin template's entry for this module (by prefix, then by name), or null. */
function preferredSource(
  origin: TemplateSummary | undefined,
  module: { name: string; prefix: string } | undefined,
): string | null {
  if (!origin || origin.error !== null || !module) return null;
  const norm = (value: string) => value.trim().toLowerCase();
  const entry =
    origin.modules.find((m) => norm(m.prefix) === norm(module.prefix)) ??
    origin.modules.find((m) => norm(m.name) === norm(module.name));
  return entry ? projectModuleSource(origin.id, entry.key) : null;
}

/**
 * "Apply chapter template…": adds the chapter headings the module does not
 * have yet (matched by title, level by level), after the existing chapters.
 * The source is a module template, or one module of a project template —
 * pre-selected from the template the project was created from, so an RBC
 * project gets the RBC chapters instead of the generic ones. A preview lists
 * what would be added; nothing existing is moved, changed or deleted, so
 * applying again adds nothing. The server owns every rule; without a match
 * the default stays the built-in template of the module's type.
 */
export function ApplySkeletonModal({ projectId, moduleId, moduleName, moduleType, onClose }: ApplySkeletonModalProps) {
  const moduleQuery = useModule(projectId, moduleId);
  const projectQuery = useProject(projectId);
  const templatesQuery = useTemplates();
  const moduleTemplatesQuery = useModuleTemplates();
  const [chosen, setChosen] = useState<string | null>(null);

  const origin = useMemo(
    () => templatesQuery.data?.find((t) => t.id === projectQuery.data?.template_id),
    [templatesQuery.data, projectQuery.data?.template_id],
  );
  // Not fixed at first render: the project and the templates arrive async, so
  // the remembered template wins until the user picks a source themselves.
  const source = chosen ?? preferredSource(origin, moduleQuery.data) ?? moduleTemplateSource(`builtin:${moduleType}`);
  const { templateId, moduleKey } = parseSource(source);
  const preview = useModuleSkeletonPreview(projectId, moduleId, templateId, moduleKey);
  const apply = useApplyModuleSkeleton(projectId, moduleId);

  const moduleOptions = (moduleTemplatesQuery.data ?? [])
    .filter((t) => t.error === null && t.headings > 0)
    .map((t) => ({
      value: moduleTemplateSource(t.id),
      label: t.module ? `${t.name} (${MODULE_TYPE_LABELS[t.module.type]})` : t.name,
    }));
  const projectGroups = (templatesQuery.data ?? [])
    .filter((t) => t.error === null && t.modules.length > 0)
    .map((t) => ({
      group: `${t.name} (project template)`,
      items: t.modules.map((m) => ({
        value: projectModuleSource(t.id, m.key),
        label: `${m.name} (${m.prefix})`,
      })),
    }));
  const data: { group: string; items: { value: string; label: string }[] }[] = [
    ...projectGroups,
    { group: 'Module templates', items: moduleOptions },
  ];
  const known = projectGroups.flatMap((g) => g.items).concat(moduleOptions);
  if (!known.some((option) => option.value === source)) {
    const label = source.startsWith('m:') || source.startsWith('p:') ? source.slice(2) : source;
    data.push({ group: 'Selected', items: [{ value: source, label }] });
  }

  const headings = preview.data?.headings ?? [];
  const canApply = preview.isSuccess && headings.length > 0 && !apply.isPending;
  const templatesFailed = moduleTemplatesQuery.isError || templatesQuery.isError;

  const handleApply = () => {
    apply.mutate(
      { template_id: templateId, module_key: moduleKey },
      {
        onSuccess: (result) => {
          const count = result.headings.length;
          notifications.show({
            message:
              count === 0
                ? `Module "${moduleName}" already has every chapter`
                : `${count} chapter heading${count === 1 ? '' : 's'} added to "${moduleName}"`,
            color: 'green',
          });
          onClose();
        },
        onError: (error) => {
          notifications.show({
            message: error instanceof ApiError ? error.message : 'Failed to apply the chapter template',
            color: 'red',
          });
        },
      },
    );
  };

  return (
    <Modal opened onClose={onClose} title={`Apply chapter template to "${moduleName}"`} centered size="lg">
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Adds the template&apos;s chapter headings the module does not have yet, after its existing chapters.
          Existing requirements are never moved, changed or deleted; applying again adds nothing.
        </Text>
        <Select
          label="Chapter template"
          data={data}
          value={source}
          onChange={(value) => value && setChosen(value)}
          allowDeselect={false}
          searchable
          disabled={apply.isPending}
          error={templatesFailed ? 'Failed to load the chapter templates' : undefined}
        />
        {preview.isLoading && (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm">Comparing with the module&apos;s chapters…</Text>
          </Group>
        )}
        {preview.isError && (
          <Alert color="red" title="Cannot apply this template">
            {preview.error instanceof ApiError ? preview.error.message : 'Failed to compute the chapters to add'}
          </Alert>
        )}
        {preview.isSuccess && headings.length === 0 && (
          <Alert color="gray">The module already has every chapter of this template; nothing to add.</Alert>
        )}
        {preview.isSuccess && headings.length > 0 && (
          <>
            <Text size="sm">
              {headings.length} heading{headings.length === 1 ? '' : 's'} will be added
              {preview.data.requirements > headings.length
                ? ` (with ${preview.data.requirements - headings.length} informative text${preview.data.requirements - headings.length === 1 ? '' : 's'})`
                : ''}
              :
            </Text>
            <ScrollArea.Autosize mah={320} type="auto">
              <Stack gap={2}>
                {headings.map((heading) => (
                  <Text key={heading.id} size="sm" style={{ paddingLeft: (heading.level - 1) * 16 }}>
                    {heading.heading}
                    {heading.under_existing && (
                      <Text span size="xs" c="dimmed">
                        {' '}
                        (under an existing chapter)
                      </Text>
                    )}
                  </Text>
                ))}
              </Stack>
            </ScrollArea.Autosize>
          </>
        )}
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleApply} loading={apply.isPending} disabled={!canApply}>
            Add chapters
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
