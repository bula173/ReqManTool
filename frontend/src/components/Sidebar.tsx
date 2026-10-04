import { Loader, NavLink, ScrollArea, Stack, Text, Tooltip } from '@mantine/core';
import { IconFolderPlus, IconFolders, IconFileText, IconPlus } from '@tabler/icons-react';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useModules, useProjects } from '../api/queries';
import type { Project } from '../api/types';
import { NewModuleModal } from './NewModuleModal';
import { NewProjectModal } from './NewProjectModal';
import { ancestorIds, buildProjectForest, type ProjectForest } from './projectTree';

function ProjectNode({
  project,
  forest,
  activeProjectId,
  activeModuleId,
  activeAncestorIds,
  onRequestNewModule,
}: {
  project: Project;
  forest: ProjectForest;
  activeProjectId: string | undefined;
  activeModuleId: string | undefined;
  /** Ancestors of the active project: start expanded so the active project is visible. */
  activeAncestorIds: string[];
  onRequestNewModule: (projectId: string) => void;
}) {
  const navigate = useNavigate();
  const projectId = project.id;
  const isActiveProject = activeProjectId === projectId;
  const [opened, setOpened] = useState(isActiveProject || activeAncestorIds.includes(projectId));
  const modulesQuery = useModules(opened ? projectId : undefined);
  const subprojects = forest.childrenOf.get(projectId) ?? [];

  return (
    <NavLink
      label={project.name}
      leftSection={<IconFolders size={16} />}
      active={isActiveProject && !activeModuleId}
      opened={opened}
      onChange={setOpened}
      onClick={() => navigate(`/projects/${projectId}`)}
      childrenOffset={20}
    >
      {opened &&
        subprojects.map((child) => (
          <ProjectNode
            key={child.id}
            project={child}
            forest={forest}
            activeProjectId={activeProjectId}
            activeModuleId={activeModuleId}
            activeAncestorIds={activeAncestorIds}
            onRequestNewModule={onRequestNewModule}
          />
        ))}
      {opened && modulesQuery.isLoading && (
        <Stack align="center" py="xs">
          <Loader size="xs" />
        </Stack>
      )}
      {opened && modulesQuery.isError && (
        <Text c="red" size="xs" px="sm" py="xs">
          Failed to load modules
        </Text>
      )}
      {opened &&
        modulesQuery.data?.map((module) => (
          <NavLink
            key={module.id}
            label={module.name}
            leftSection={<IconFileText size={14} />}
            active={isActiveProject && activeModuleId === module.id}
            onClick={() => navigate(`/projects/${projectId}/modules/${module.id}`)}
          />
        ))}
      {opened && (
        <NavLink
          label="New module"
          leftSection={<IconPlus size={14} />}
          onClick={() => onRequestNewModule(projectId)}
          c="dimmed"
        />
      )}
    </NavLink>
  );
}

export function Sidebar() {
  const projectsQuery = useProjects();
  const params = useParams<{ projectId?: string; moduleId?: string }>();
  const [newProjectOpened, setNewProjectOpened] = useState(false);
  const [newModuleProjectId, setNewModuleProjectId] = useState<string | null>(null);

  const projects = projectsQuery.data ?? [];
  const forest = buildProjectForest(projects);
  const activeAncestorIds = params.projectId ? ancestorIds(projects, params.projectId) : [];

  return (
    <Stack gap="xs" h="100%">
      <Stack gap={4} px="xs" pt="xs">
        <Tooltip label="New project">
          <NavLink
            label="New project"
            leftSection={<IconFolderPlus size={16} />}
            onClick={() => setNewProjectOpened(true)}
            fw={600}
          />
        </Tooltip>
      </Stack>
      <ScrollArea flex={1} type="auto">
        {projectsQuery.isLoading && (
          <Stack align="center" py="md">
            <Loader size="sm" />
          </Stack>
        )}
        {projectsQuery.isError && (
          <Text c="red" size="sm" px="sm">
            Failed to load projects
          </Text>
        )}
        {projectsQuery.data?.length === 0 && (
          <Text c="dimmed" size="sm" px="sm">
            No projects yet
          </Text>
        )}
        {forest.roots.map((project) => (
          <ProjectNode
            key={project.id}
            project={project}
            forest={forest}
            activeProjectId={params.projectId}
            activeModuleId={params.moduleId}
            activeAncestorIds={activeAncestorIds}
            onRequestNewModule={setNewModuleProjectId}
          />
        ))}
      </ScrollArea>
      <NewProjectModal opened={newProjectOpened} onClose={() => setNewProjectOpened(false)} />
      <NewModuleModal projectId={newModuleProjectId} onClose={() => setNewModuleProjectId(null)} />
    </Stack>
  );
}
