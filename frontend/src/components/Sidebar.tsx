import { Loader, NavLink, ScrollArea, Stack, Text, Tooltip } from '@mantine/core';
import { IconFolderPlus, IconFolders, IconFileText, IconPlus } from '@tabler/icons-react';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useModules, useProjects } from '../api/queries';
import { NewModuleModal } from './NewModuleModal';
import { NewProjectModal } from './NewProjectModal';

function ProjectNode({
  projectId,
  name,
  activeProjectId,
  activeModuleId,
  onRequestNewModule,
}: {
  projectId: string;
  name: string;
  activeProjectId: string | undefined;
  activeModuleId: string | undefined;
  onRequestNewModule: (projectId: string) => void;
}) {
  const navigate = useNavigate();
  const isActiveProject = activeProjectId === projectId;
  const [opened, setOpened] = useState(isActiveProject);
  const modulesQuery = useModules(opened ? projectId : undefined);

  return (
    <NavLink
      label={name}
      leftSection={<IconFolders size={16} />}
      active={isActiveProject && !activeModuleId}
      opened={opened}
      onChange={setOpened}
      onClick={() => navigate(`/projects/${projectId}`)}
      childrenOffset={28}
    >
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
        {projectsQuery.data?.map((project) => (
          <ProjectNode
            key={project.id}
            projectId={project.id}
            name={project.name}
            activeProjectId={params.projectId}
            activeModuleId={params.moduleId}
            onRequestNewModule={setNewModuleProjectId}
          />
        ))}
      </ScrollArea>
      <NewProjectModal opened={newProjectOpened} onClose={() => setNewProjectOpened(false)} />
      <NewModuleModal projectId={newModuleProjectId} onClose={() => setNewModuleProjectId(null)} />
    </Stack>
  );
}
