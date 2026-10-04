import { Loader, NavLink, ScrollArea, Stack, Text, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconFolderPlus, IconFolders, IconFileText, IconPlus } from '@tabler/icons-react';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAppActions } from '../actions/context';
import { useModules, useProjects } from '../api/queries';
import { isProjectStub, type Project, type ProjectListEntry, type ProjectStub } from '../api/types';
import { NewModuleModal } from './NewModuleModal';
import { ancestorIds, buildProjectForest, type ProjectForest } from './projectTree';

interface NodeContext {
  forest: ProjectForest;
  activeProjectId: string | undefined;
  activeModuleId: string | undefined;
  /** Ancestors of the active project: start expanded so the active project is visible. */
  activeAncestorIds: string[];
  onRequestNewModule: (projectId: string) => void;
}

/** A project, or a read-only stub for a project that cannot be loaded. */
function ProjectTreeNode({ project, ...context }: NodeContext & { project: ProjectListEntry }) {
  return isProjectStub(project) ? (
    <StubProjectNode project={project} {...context} />
  ) : (
    <ProjectNode project={project} {...context} />
  );
}

/**
 * A project whose project.json cannot be loaded (written by a newer ReqManTool,
 * not valid JSON, …): greyed, with a warning icon and the reason shown inline
 * (and in a tooltip on hover, focus or touch). Activating it opens the project
 * page, which shows the load error; it has no modules and no "New module"
 * entry, and only expands to show its readable subprojects, if any.
 */
function StubProjectNode({
  project,
  forest,
  activeProjectId,
  activeAncestorIds,
  ...context
}: NodeContext & { project: ProjectStub }) {
  const navigate = useNavigate();
  const [opened, setOpened] = useState(activeAncestorIds.includes(project.id));
  // Nested only if it has subprojects: otherwise there is nothing to expand.
  const subprojects = forest.childrenOf.get(project.id) ?? [];

  return (
    <Tooltip
      label={`Read-only, cannot be opened: ${project.error}`}
      multiline
      w={360}
      position="right"
      withArrow
      openDelay={300}
      events={{ hover: true, focus: true, touch: true }}
    >
      <NavLink
        // A button, so keyboard users can focus it (showing the tooltip) and activate it.
        component="button"
        type="button"
        label={project.name}
        description={
          <Text component="span" size="xs" c="dimmed" lineClamp={2}>
            Cannot be opened: {project.error}
          </Text>
        }
        leftSection={<IconAlertTriangle size={16} />}
        c="dimmed"
        style={{ opacity: 0.6 }}
        onClick={() => navigate(`/projects/${project.id}`)}
        active={activeProjectId === project.id}
        opened={opened}
        onChange={setOpened}
        childrenOffset={20}
      >
        {subprojects.length > 0 ? (
          <>
            {opened &&
              subprojects.map((child) => (
                <ProjectTreeNode
                  key={child.id}
                  project={child}
                  forest={forest}
                  activeProjectId={activeProjectId}
                  activeAncestorIds={activeAncestorIds}
                  {...context}
                />
              ))}
          </>
        ) : undefined}
      </NavLink>
    </Tooltip>
  );
}

function ProjectNode({
  project,
  forest,
  activeProjectId,
  activeModuleId,
  activeAncestorIds,
  onRequestNewModule,
}: NodeContext & { project: Project }) {
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
          <ProjectTreeNode
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
  const { run } = useAppActions();
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
            onClick={() => run('file.newProject')}
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
            {projectsQuery.error instanceof Error ? `: ${projectsQuery.error.message}` : ''}
          </Text>
        )}
        {projectsQuery.data?.length === 0 && (
          <Text c="dimmed" size="sm" px="sm">
            No projects yet
          </Text>
        )}
        {forest.roots.map((project) => (
          <ProjectTreeNode
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
      <NewModuleModal projectId={newModuleProjectId} onClose={() => setNewModuleProjectId(null)} />
    </Stack>
  );
}
