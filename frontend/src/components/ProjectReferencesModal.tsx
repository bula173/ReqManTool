import { Alert, Button, Group, Modal, MultiSelect, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconInfoCircle } from '@tabler/icons-react';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useProjectReferences, useProjects, useSetProjectReferences } from '../api/queries';
import { isProjectStub, type Project } from '../api/types';

interface ProjectReferencesModalProps {
  project: Project;
  onClose: () => void;
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

/**
 * Edits the projects a project references explicitly (`PUT
 * /projects/{id}/references`). The parent is referenced implicitly and is not
 * offered. Link configurations of the project may target modules of the
 * referenced projects; the server refuses to drop a reference that one of them
 * still targets (the error is shown).
 */
export function ProjectReferencesModal({ project, onClose }: ProjectReferencesModalProps) {
  const projectsQuery = useProjects();
  const referencesQuery = useProjectReferences(project.id);
  const setReferences = useSetProjectReferences(project.id);
  const stored = project.references ?? [];
  const [picked, setPicked] = useState<string[]>(stored);

  const projects = projectsQuery.data ?? [];
  const parentId = project.parent_project_id ?? null;
  const parentName = parentId ? (projects.find((p) => p.id === parentId)?.name ?? parentId) : null;
  // Loadable projects other than this one and its parent (implicit). A stored reference to a
  // project that can no longer be loaded stays listed so it can be removed.
  const options = projects
    .filter((p) => p.id !== project.id && p.id !== parentId && (!isProjectStub(p) || stored.includes(p.id)))
    .map((p) => ({ value: p.id, label: isProjectStub(p) ? `${p.name} (cannot be loaded)` : p.name }));
  for (const id of stored) {
    if (!options.some((o) => o.value === id)) options.push({ value: id, label: `${id} (missing)` });
  }
  const inUse = new Map(
    (referencesQuery.data?.references ?? [])
      .filter((r) => r.link_configurations > 0)
      .map((r) => [r.id, r.link_configurations]),
  );

  const handleSave = () => {
    setReferences.mutate(picked, {
      onSuccess: () => {
        notifications.show({ message: `Saved the references of "${project.name}"`, color: 'green' });
        onClose();
      },
      onError: (error) =>
        notifications.show({
          message: error instanceof ApiError ? error.message : 'Failed to save the project references',
          color: 'red',
        }),
    });
  };

  return (
    <Modal opened onClose={onClose} title={`References of ${project.name}`} centered size="lg">
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Link configurations of this project may target modules of the projects it references, for
          example a parent product, a sibling or a shared library project. Cross-project links are stored
          in this project only.
        </Text>
        {parentName && (
          <Text size="sm">
            The parent project <b>{parentName}</b> is always referenced.
          </Text>
        )}
        <MultiSelect
          label="Referenced projects"
          placeholder={projectsQuery.isLoading ? 'Loading projects…' : 'Pick projects'}
          data={options}
          value={picked}
          onChange={setPicked}
          searchable
          clearable
          disabled={projectsQuery.isLoading}
          error={projectsQuery.isError ? 'Failed to load projects' : undefined}
          nothingFoundMessage="No other projects"
        />
        {stored.some((id) => !picked.includes(id) && inUse.has(id)) && (
          <Alert color="yellow" icon={<IconInfoCircle size={16} />}>
            A removed project is still the target of link configurations of this project. Remove those
            configurations first; saving is refused otherwise.
          </Alert>
        )}
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            loading={setReferences.isPending}
            disabled={projectsQuery.isLoading || projectsQuery.isError || sameList(picked, stored)}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
