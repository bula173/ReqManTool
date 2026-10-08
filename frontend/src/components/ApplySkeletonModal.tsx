import { Alert, Button, Group, Loader, Modal, ScrollArea, Select, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { MODULE_TYPE_LABELS } from '../api/moduleTypes';
import { useApplyModuleSkeleton, useModuleSkeletonPreview, useModuleTemplates } from '../api/queries';
import type { ModuleType } from '../api/types';

interface ApplySkeletonModalProps {
  projectId: string;
  moduleId: string;
  moduleName: string;
  moduleType: ModuleType;
  onClose: () => void;
}

/**
 * "Apply chapter template…": adds the chapter headings of a module template
 * that the module does not have yet (matched by title, level by level),
 * after the existing chapters. A preview lists what would be added; nothing
 * existing is moved, changed or deleted, so applying again adds nothing. The
 * server owns every rule; the default is the built-in template of the
 * module's type.
 */
export function ApplySkeletonModal({ projectId, moduleId, moduleName, moduleType, onClose }: ApplySkeletonModalProps) {
  const templatesQuery = useModuleTemplates();
  const defaultId = `builtin:${moduleType}`;
  const [templateId, setTemplateId] = useState<string>(defaultId);
  const preview = useModuleSkeletonPreview(projectId, moduleId, templateId);
  const apply = useApplyModuleSkeleton(projectId, moduleId);

  const templateOptions = (templatesQuery.data ?? [])
    .filter((t) => t.error === null && t.headings > 0)
    .map((t) => ({
      value: t.id,
      label: t.module ? `${t.name} (${MODULE_TYPE_LABELS[t.module.type]})` : t.name,
    }));
  if (!templateOptions.some((o) => o.value === templateId)) {
    templateOptions.unshift({ value: templateId, label: templateId });
  }

  const headings = preview.data?.headings ?? [];
  const canApply = preview.isSuccess && headings.length > 0 && !apply.isPending;

  const handleApply = () => {
    apply.mutate(
      { template_id: templateId },
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
          data={templateOptions}
          value={templateId}
          onChange={(value) => value && setTemplateId(value)}
          allowDeselect={false}
          searchable
          disabled={apply.isPending}
          error={templatesQuery.isError ? 'Failed to load the module templates' : undefined}
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
