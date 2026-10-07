import { Alert, Checkbox, Group, Loader, Stack, Text } from '@mantine/core';
import { useModules } from '../api/queries';

interface AttributeModulesChecklistProps {
  projectId: string;
  /** Selected module ids; empty means the attribute applies to every module. */
  value: string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
}

/**
 * The "Applies to modules" checklist of the attribute dialogs. Nothing
 * ticked means every module, which is also what a project without modules
 * gets.
 */
export function AttributeModulesChecklist({ projectId, value, onChange, disabled }: AttributeModulesChecklistProps) {
  const modulesQuery = useModules(projectId);
  const modules = modulesQuery.data ?? [];

  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
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
          The project has no modules yet; the attribute applies to every module.
        </Text>
      )}
      {modules.length > 0 && (
        <Checkbox.Group value={value} onChange={onChange}>
          <Stack gap={4}>
            {modules.map((module) => (
              <Checkbox key={module.id} value={module.id} label={module.name} disabled={disabled} />
            ))}
          </Stack>
        </Checkbox.Group>
      )}
      {modules.length > 0 && (
        <Text size="xs" c="dimmed">
          {value.length === 0 ? 'None ticked: applies to every module.' : `Applies to ${value.length} of ${modules.length} modules.`}
        </Text>
      )}
    </Stack>
  );
}
