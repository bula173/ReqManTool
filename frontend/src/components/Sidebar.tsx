import { Loader, NavLink, ScrollArea, Stack, Tabs, Text, Tooltip } from '@mantine/core';
import { useLocalStorage } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconChevronDown,
  IconChevronRight,
  IconFileText,
  IconFolderPlus,
  IconFolders,
  IconListTree,
  IconSitemap,
} from '@tabler/icons-react';
import { useEffect, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAppActions } from '../actions/context';
import { useProjects } from '../api/queries';
import { isProjectStub, type Project, type ProjectListEntry, type ProjectStub } from '../api/types';
import { ModuleOutline } from './ModuleOutline';
import { ProjectTab } from './ProjectTab';
import { ancestorIds, buildProjectForest, type ProjectForest } from './projectTree';

interface NodeContext {
  forest: ProjectForest;
  /** The project chosen for the Project tab (highlighted). */
  activeProjectId: string | undefined;
  /** Ancestors of the active project: start expanded so the active project is visible. */
  activeAncestorIds: string[];
  onPickProject: (projectId: string) => void;
}

/**
 * The expand/collapse chevron of a project node with subprojects. It only
 * toggles the node: the event never reaches the row, so it neither picks the
 * project nor leaves the Workspace tab.
 */
function ExpandToggle({ name, opened, onToggle }: { name: string; opened: boolean; onToggle: () => void }) {
  const toggle = (event: MouseEvent | KeyboardEvent) => {
    event.preventDefault();
    event.stopPropagation();
    onToggle();
  };
  return (
    <span
      role="button"
      tabIndex={0}
      aria-label={opened ? `Collapse ${name}` : `Expand ${name}`}
      aria-expanded={opened}
      style={{ display: 'inline-flex', padding: 2, borderRadius: 'var(--mantine-radius-sm)' }}
      onClick={toggle}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') toggle(event);
      }}
    >
      {opened ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
    </span>
  );
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
 * page, which shows the load error; it only expands to show its readable
 * subprojects, if any.
 */
function StubProjectNode({
  project,
  forest,
  activeProjectId,
  activeAncestorIds,
  onPickProject,
}: NodeContext & { project: ProjectStub }) {
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
        onClick={() => onPickProject(project.id)}
        active={activeProjectId === project.id}
        opened={opened}
        onChange={setOpened}
        rightSection={
          subprojects.length > 0 ? (
            <ExpandToggle name={project.name} opened={opened} onToggle={() => setOpened((o) => !o)} />
          ) : undefined
        }
        disableRightSectionRotation
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
                  onPickProject={onPickProject}
                />
              ))}
          </>
        ) : undefined}
      </NavLink>
    </Tooltip>
  );
}

/** A project of the Workspace tab, with its subproject tree. Picking it chooses it for the Project tab. */
function ProjectNode({
  project,
  forest,
  activeProjectId,
  activeAncestorIds,
  onPickProject,
}: NodeContext & { project: Project }) {
  const projectId = project.id;
  const [opened, setOpened] = useState(activeAncestorIds.includes(projectId));
  const subprojects = forest.childrenOf.get(projectId) ?? [];

  return (
    <NavLink
      label={project.name}
      leftSection={<IconFolders size={16} />}
      active={activeProjectId === projectId}
      opened={opened}
      onChange={setOpened}
      onClick={() => onPickProject(projectId)}
      rightSection={
        subprojects.length > 0 ? (
          <ExpandToggle name={project.name} opened={opened} onToggle={() => setOpened((o) => !o)} />
        ) : undefined
      }
      disableRightSectionRotation
      childrenOffset={20}
    >
      {/* Always an element when there are subprojects (even collapsed), so NavLink treats the node as expandable. */}
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
                onPickProject={onPickProject}
              />
            ))}
        </>
      ) : undefined}
    </NavLink>
  );
}

/** The Workspace tab: every project of the data folder, as the subproject tree. */
function WorkspaceTab({
  activeProjectId,
  onPickProject,
}: {
  activeProjectId: string | undefined;
  onPickProject: (projectId: string) => void;
}) {
  const projectsQuery = useProjects();
  const { run } = useAppActions();

  const projects = projectsQuery.data ?? [];
  const forest = buildProjectForest(projects);
  const activeAncestorIds = activeProjectId ? ancestorIds(projects, activeProjectId) : [];

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
            activeProjectId={activeProjectId}
            activeAncestorIds={activeAncestorIds}
            onPickProject={onPickProject}
          />
        ))}
      </ScrollArea>
    </Stack>
  );
}

type LeftPanelTab = 'workspace' | 'project' | 'module';

const LEFT_PANEL_TABS: LeftPanelTab[] = ['workspace', 'project', 'module'];
const TAB_STORAGE_KEY = 'reqman.leftPanel.tab';
const PROJECT_STORAGE_KEY = 'reqman.leftPanel.projectId';

/**
 * The left panel: three drill-down tabs. Workspace lists every project
 * (picking one opens it and shows the Project tab), Project lists the chosen
 * project's subprojects and modules (picking a module opens it and shows the
 * Module tab), Module outlines the open module's requirement tree.
 *
 * The open project/module live in the route (URL); the active tab and the
 * project last chosen (for the Project tab on routes without a project) are
 * kept per browser. Every tab reads the shared queries, so live sync
 * refreshes all three.
 */
export function Sidebar() {
  const params = useParams<{ projectId?: string; moduleId?: string }>();
  const navigate = useNavigate();
  const [storedTab, setTab] = useLocalStorage<LeftPanelTab>({
    key: TAB_STORAGE_KEY,
    defaultValue: 'workspace',
    getInitialValueInEffect: false,
  });
  const [storedProjectId, setStoredProjectId] = useLocalStorage<string | null>({
    key: PROJECT_STORAGE_KEY,
    defaultValue: null,
    getInitialValueInEffect: false,
  });
  const tab: LeftPanelTab = LEFT_PANEL_TABS.includes(storedTab) ? storedTab : 'workspace';

  // The route's project is the chosen one; remember it for routes without a project.
  const routeProjectId = params.projectId;
  useEffect(() => {
    if (routeProjectId) setStoredProjectId(routeProjectId);
  }, [routeProjectId, setStoredProjectId]);
  const chosenProjectId = routeProjectId ?? storedProjectId ?? null;
  const moduleId = routeProjectId ? params.moduleId : undefined;

  const pickProject = (projectId: string) => {
    setStoredProjectId(projectId);
    navigate(`/projects/${projectId}`);
  };

  return (
    <Tabs
      value={tab}
      onChange={(value) => {
        if (value && LEFT_PANEL_TABS.includes(value as LeftPanelTab)) setTab(value as LeftPanelTab);
      }}
      style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}
    >
      <Tabs.List grow>
        {/* Compact, so the three tabs fit the panel width on one row. */}
        <Tabs.Tab value="workspace" px={6} leftSection={<IconSitemap size={14} />}>
          Workspace
        </Tabs.Tab>
        <Tabs.Tab value="project" px={6} leftSection={<IconFolders size={14} />}>
          Project
        </Tabs.Tab>
        <Tabs.Tab value="module" px={6} leftSection={<IconListTree size={14} />}>
          Module
        </Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value="workspace" style={{ flex: 1, minHeight: 0 }}>
        <WorkspaceTab
          activeProjectId={chosenProjectId ?? undefined}
          onPickProject={(projectId) => {
            pickProject(projectId);
            setTab('project');
          }}
        />
      </Tabs.Panel>
      <Tabs.Panel value="project" style={{ flex: 1, minHeight: 0 }}>
        <ProjectTab
          projectId={chosenProjectId}
          activeModuleId={moduleId}
          onPickProject={pickProject}
          onOpenModule={(projectId, openedModuleId) => {
            navigate(`/projects/${projectId}/modules/${openedModuleId}`);
            setTab('module');
          }}
        />
      </Tabs.Panel>
      <Tabs.Panel value="module" style={{ flex: 1, minHeight: 0 }}>
        {routeProjectId && moduleId ? (
          <ModuleOutline key={`${routeProjectId}/${moduleId}`} projectId={routeProjectId} moduleId={moduleId} />
        ) : (
          <Stack gap={4} p="sm">
            <IconFileText size={20} color="var(--mantine-color-dimmed)" />
            <Text c="dimmed" size="sm">
              No module is open. Open one from the Project tab.
            </Text>
          </Stack>
        )}
      </Tabs.Panel>
    </Tabs>
  );
}
