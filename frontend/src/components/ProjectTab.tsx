import { Center, Loader, NavLink, ScrollArea, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconCornerLeftUp, IconFileText, IconFolderPlus, IconFolders, IconPlus } from '@tabler/icons-react';
import { useState } from 'react';
import { useAppActions } from '../actions/context';
import { useModules, useProjects } from '../api/queries';
import { isProjectStub } from '../api/types';
import { NewModuleModal } from './NewModuleModal';
import { buildProjectForest } from './projectTree';

interface ProjectTabProps {
  /** The chosen project (from the route, else the last one picked in this browser). */
  projectId: string | null;
  activeModuleId: string | undefined;
  onPickProject: (projectId: string) => void;
  onOpenModule: (projectId: string, moduleId: string) => void;
}

function SectionLabel({ children }: { children: string }) {
  return (
    <Text size="xs" fw={700} c="dimmed" tt="uppercase" px="sm" pt="xs" pb={2}>
      {children}
    </Text>
  );
}

/**
 * The Project tab of the left panel: the chosen project's parent (to go up),
 * subprojects (picking one drills down) and modules (picking one opens it in
 * the Module tab), plus "New module" / "New subproject".
 */
export function ProjectTab({ projectId, activeModuleId, onPickProject, onOpenModule }: ProjectTabProps) {
  const projectsQuery = useProjects();
  const { state, run, isAvailable } = useAppActions();
  const [newModuleProjectId, setNewModuleProjectId] = useState<string | null>(null);

  const projects = projectsQuery.data ?? [];
  const project = projectId ? projects.find((p) => p.id === projectId) : undefined;
  const readable = project !== undefined && !isProjectStub(project);
  const modulesQuery = useModules(readable ? projectId! : undefined);

  if (!projectId) {
    return (
      <Text c="dimmed" size="sm" p="sm">
        Pick a project in the Workspace tab.
      </Text>
    );
  }
  if (projectsQuery.isLoading) {
    return (
      <Center py="md">
        <Loader size="sm" />
      </Center>
    );
  }
  if (projectsQuery.isError) {
    return (
      <Text c="red" size="sm" p="sm">
        Failed to load projects
        {projectsQuery.error instanceof Error ? `: ${projectsQuery.error.message}` : ''}
      </Text>
    );
  }
  if (!project) {
    return (
      <Text c="dimmed" size="sm" p="sm">
        Project "{projectId}" is not in this data folder. Pick a project in the Workspace tab.
      </Text>
    );
  }

  // The effective tree (a dangling/cyclic stored parent is shown top level).
  const forest = buildProjectForest(projects);
  const parent = projects.find((p) => forest.childrenOf.get(p.id)?.some((child) => child.id === projectId));
  const subprojects = forest.childrenOf.get(projectId) ?? [];
  // "New subproject" works on the open project (the route's).
  const canAddSubproject = state.projectId === projectId && isAvailable('project.newSubproject');

  return (
    <Stack gap={0} h="100%">
      <Stack gap={0} px="xs" pt="xs">
        <Text fw={600} size="sm" px="sm" truncate="end">
          {project.name}
        </Text>
        {parent && (
          <NavLink
            label={`Up to ${parent.name}`}
            leftSection={<IconCornerLeftUp size={14} />}
            onClick={() => onPickProject(parent.id)}
            c="dimmed"
          />
        )}
      </Stack>
      <ScrollArea flex={1} type="auto" px="xs">
        {isProjectStub(project) && (
          <Text c="red" size="xs" px="sm" py="xs">
            <IconAlertTriangle size={12} /> Cannot be opened: {project.error}
          </Text>
        )}

        {subprojects.length > 0 && <SectionLabel>Subprojects</SectionLabel>}
        {subprojects.map((child) => (
          <NavLink
            key={child.id}
            label={child.name}
            leftSection={isProjectStub(child) ? <IconAlertTriangle size={14} /> : <IconFolders size={14} />}
            c={isProjectStub(child) ? 'dimmed' : undefined}
            title={isProjectStub(child) ? `Cannot be opened: ${child.error}` : undefined}
            onClick={() => onPickProject(child.id)}
          />
        ))}
        {canAddSubproject && (
          <NavLink
            label="New subproject"
            leftSection={<IconFolderPlus size={14} />}
            onClick={() => run('project.newSubproject')}
            c="dimmed"
          />
        )}

        {readable && (
          <>
            <SectionLabel>Modules</SectionLabel>
            {modulesQuery.isLoading && (
              <Center py="xs">
                <Loader size="xs" />
              </Center>
            )}
            {modulesQuery.isError && (
              <Text c="red" size="xs" px="sm" py="xs">
                Failed to load modules
              </Text>
            )}
            {modulesQuery.data?.length === 0 && (
              <Text c="dimmed" size="xs" px="sm" py={4}>
                No modules yet
              </Text>
            )}
            {modulesQuery.data?.map((module) => (
              <NavLink
                key={module.id}
                label={module.name}
                description={module.prefix}
                leftSection={<IconFileText size={14} />}
                active={state.projectId === projectId && activeModuleId === module.id}
                onClick={() => onOpenModule(projectId, module.id)}
              />
            ))}
            <NavLink
              label="New module"
              leftSection={<IconPlus size={14} />}
              onClick={() => setNewModuleProjectId(projectId)}
              c="dimmed"
            />
          </>
        )}
      </ScrollArea>
      <NewModuleModal projectId={newModuleProjectId} onClose={() => setNewModuleProjectId(null)} />
    </Stack>
  );
}
