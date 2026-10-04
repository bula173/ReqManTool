import { Button, Group, Modal, Select, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { useProjects, useSetProjectParent } from '../api/queries';
import { ApiError } from '../api/client';
import type { Project } from '../api/types';
import { buildProjectForest, descendantIds, projectOptions } from './projectTree';

/** Select value for "no parent"; project ids are slugs and can never equal it. */
const TOP_LEVEL = '__top__';

interface ProjectParentModalProps {
  project: Project;
  opened: boolean;
  onClose: () => void;
}

function ProjectParentForm({ project, onClose }: Omit<ProjectParentModalProps, 'opened'>) {
  const projectsQuery = useProjects();
  const setParent = useSetProjectParent(project.id);
  const projects = projectsQuery.data ?? [];
  // The list reports the effective parent (a dangling/cyclic stored one is top level);
  // it is what the picker starts on.
  const currentParent = projects.find((p) => p.id === project.id)?.parent_project_id ?? null;
  // `project` comes from GET /projects/{id}, which returns the stored parent. Save is enabled
  // whenever the choice differs from it, so "None (top level)" can repair a dangling or
  // cyclic stored parent even though the list already shows the project as top level.
  const storedParent = project.parent_project_id ?? null;
  // undefined until the user picks: follows the current parent once the list has loaded.
  const [picked, setPicked] = useState<string | undefined>(undefined);
  const value = picked ?? currentParent ?? TOP_LEVEL;

  const forest = buildProjectForest(projects);
  // The server rejects these too; hiding them just avoids a pointless error.
  const excluded = descendantIds(forest, project.id);
  excluded.add(project.id);
  const options = [{ value: TOP_LEVEL, label: 'None (top level)' }, ...projectOptions(forest, excluded)];
  const newParent = value === TOP_LEVEL ? null : value;

  const handleSave = () => {
    setParent.mutate(newParent, {
      onSuccess: () => {
        notifications.show({ message: `Moved "${project.name}"`, color: 'green' });
        onClose();
      },
      onError: (error) =>
        notifications.show({
          message: error instanceof ApiError ? error.message : 'Failed to change the parent project',
          color: 'red',
        }),
    });
  };

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        Subprojects form a generic → specific tree (e.g. generic product → generic application →
        specific application). A project cannot be placed under itself or one of its subprojects.
      </Text>
      <Select
        label="Parent project"
        data={options}
        value={value}
        onChange={(next) => setPicked(next ?? TOP_LEVEL)}
        allowDeselect={false}
        searchable
        disabled={projectsQuery.isLoading}
        placeholder={projectsQuery.isLoading ? 'Loading projects…' : undefined}
        error={projectsQuery.isError ? 'Failed to load projects' : undefined}
      />
      <Group justify="flex-end" mt="sm">
        <Button variant="default" onClick={onClose}>
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          loading={setParent.isPending}
          disabled={projectsQuery.isLoading || projectsQuery.isError || newParent === storedParent}
        >
          Save
        </Button>
      </Group>
    </Stack>
  );
}

export function ProjectParentModal({ project, opened, onClose }: ProjectParentModalProps) {
  return (
    <Modal opened={opened} onClose={onClose} title="Change parent project" centered>
      {opened && <ProjectParentForm project={project} onClose={onClose} />}
    </Modal>
  );
}
