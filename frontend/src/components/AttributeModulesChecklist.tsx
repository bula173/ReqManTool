import { Alert, Checkbox, Group, Loader, Stack, Text } from '@mantine/core';
import { MODULE_TYPE_LABELS, MODULE_TYPES, moduleTypeOf } from '../api/moduleTypes';
import { useModules, useModuleTree } from '../api/queries';
import type { ModuleType } from '../api/types';
import { ModuleTypeIcon } from './ModuleTypeIcon';
import { moduleFolderPaths } from './moduleTree';

interface AttributeModulesChecklistProps {
  projectId: string;
  /** Selected module ids; with `types` empty too, the attribute applies to every module. */
  value: string[];
  onChange: (value: string[]) => void;
  /** Selected module types (every module of such a type). */
  types: ModuleType[];
  onTypesChange: (types: ModuleType[]) => void;
  disabled?: boolean;
}

/**
 * The "Applies to" checklists of the attribute dialogs: module types, then
 * single modules. The attribute applies to a module that is ticked or whose
 * type is ticked; nothing ticked means every module, which is also what a
 * project without modules gets.
 */
export function AttributeModulesChecklist({
  projectId,
  value,
  onChange,
  types,
  onTypesChange,
  disabled,
}: AttributeModulesChecklistProps) {
  const modulesQuery = useModules(projectId);
  const modules = modulesQuery.data ?? [];
  const folderPaths = moduleFolderPaths(useModuleTree(projectId).data);
  const covered = modules.filter((m) => value.includes(m.id) || types.includes(moduleTypeOf(m))).length;
  const nothingTicked = value.length === 0 && types.length === 0;

  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
        Applies to module types
      </Text>
      <Checkbox.Group value={types} onChange={(next) => onTypesChange(MODULE_TYPES.filter((t) => next.includes(t)))}>
        <Group gap="md">
          {MODULE_TYPES.map((type) => (
            <Checkbox
              key={type}
              value={type}
              disabled={disabled}
              label={
                <Group gap={4} wrap="nowrap">
                  <ModuleTypeIcon type={type} size={14} />
                  {MODULE_TYPE_LABELS[type]}
                </Group>
              }
            />
          ))}
        </Group>
      </Checkbox.Group>
      <Text size="sm" fw={500} mt="xs">
        Applies to modules
      </Text>
      {modulesQuery.isLoading && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="xs" c="dimmed">
            Loading modules…
          </Text>
        </Group>
      )}
      {modulesQuery.isError && (
        <Alert color="red" variant="light" p="xs">
          Failed to load modules{modulesQuery.error instanceof Error ? `: ${modulesQuery.error.message}` : ''}
        </Alert>
      )}
      {modulesQuery.isSuccess && modules.length === 0 && (
        <Text size="xs" c="dimmed">
          The project has no modules yet.
        </Text>
      )}
      {modules.length > 0 && (
        <Checkbox.Group value={value} onChange={onChange}>
          <Stack gap={4}>
            {modules.map((module) => (
              <Checkbox
                key={module.id}
                value={module.id}
                disabled={disabled}
                label={
                  <Group gap={4} wrap="nowrap">
                    <ModuleTypeIcon module={module} size={14} />
                    {module.name}
                    {folderPaths.has(module.id) && (
                      <Text span size="xs" c="dimmed">
                        {folderPaths.get(module.id)}
                      </Text>
                    )}
                  </Group>
                }
              />
            ))}
          </Stack>
        </Checkbox.Group>
      )}
      <Text size="xs" c="dimmed">
        {nothingTicked
          ? 'None ticked: applies to every module.'
          : modules.length > 0
            ? `Applies to ${covered} of ${modules.length} modules (ticked modules and every module of a ticked type).`
            : 'Applies to modules of the ticked types.'}
      </Text>
    </Stack>
  );
}
