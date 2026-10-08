import { Center, Loader, NavLink, ScrollArea, Stack, Text } from '@mantine/core';
import { useLocalStorage } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconCornerLeftUp,
  IconFolder,
  IconFolderOpen,
  IconFolderPlus,
  IconFolders,
  IconPlus,
} from '@tabler/icons-react';
import { useState, type DragEvent, type MouseEvent, type ReactNode } from 'react';
import { useAppActions } from '../actions/context';
import { FOLDER_NODE_MENU, MODULE_NODE_MENU, PROJECT_NODE_MENU, isProjectReadOnly } from '../actions/registry';
import { contextMenuTargetProps } from '../actions/shortcuts';
import type { ActionTargetOverrides, MenuEntry } from '../actions/types';
import { ApiError } from '../api/client';
import { useModules, useModuleTree, useProjects } from '../api/queries';
import { isProjectStub, type Project } from '../api/types';
import { buildProjectForest } from './projectTree';
import { ModuleTypeIcon } from './ModuleTypeIcon';
import { ProjectLockBadge } from './ProjectLockBadge';
import { buildModuleTreeNodes, folderSubtreeIds, type ModuleTreeNode } from './moduleTree';

/** Collapsed module folders (`<projectId>/<folderId>`), remembered per browser. */
const COLLAPSED_FOLDERS_STORAGE_KEY = 'reqman.moduleFolders.collapsed';

/** A module or folder being dragged in the module tree (HTML5 drag and drop within this window). */
interface TreeDrag {
  projectId: string;
  kind: 'module' | 'folder';
  id: string;
  /** For a folder: its id and its subfolders' ids (it cannot be dropped into them). */
  blocked: Set<string>;
}

/** Drag data type of module tree nodes (other drop zones ignore it). */
const TREE_DRAG_TYPE = 'application/x-reqman-module-tree';

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
 * the Module tab) arranged in module folders (click a folder to expand or
 * collapse it), plus "New module" / "New folder" / "New subproject". Right
 * click (or Shift+F10) on the project, a subproject, a folder or a module
 * opens its editing menu from the action registry; left click opens it.
 * Modules and folders can be dragged onto a folder, or onto the project name
 * for the top level.
 */
export function ProjectTab({ projectId, activeModuleId, onPickProject, onOpenModule }: ProjectTabProps) {
  const projectsQuery = useProjects();
  const { state, run, isAvailable, openMenu } = useAppActions();
  const [collapsed, setCollapsed] = useLocalStorage<string[]>({
    key: COLLAPSED_FOLDERS_STORAGE_KEY,
    defaultValue: [],
    getInitialValueInEffect: false,
  });
  const [drag, setDrag] = useState<TreeDrag | null>(null);
  // The drop target under the pointer: a folder id, or '' for the top level (project row).
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  const projects = projectsQuery.data ?? [];
  const project = projectId ? projects.find((p) => p.id === projectId) : undefined;
  const readable = project !== undefined && !isProjectStub(project);
  const modulesQuery = useModules(readable ? projectId! : undefined);
  const treeQuery = useModuleTree(readable ? projectId! : undefined);

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
  const canAddSubproject = isAvailable('project.newSubproject', { project });
  // Modules and folders of a locked or read-only project cannot be moved (no drag and drop).
  const editable = readable && !isProjectReadOnly(state, projectId);
  const moduleIds = (modulesQuery.data ?? []).map((m) => m.id);
  // Modules shown flat (in display order) until the tree has loaded, or if it failed to load.
  const nodes = buildModuleTreeNodes(treeQuery.data, modulesQuery.data ?? []);
  const menuProps = (label: string, entries: MenuEntry[], targets: ActionTargetOverrides) => ({
    ...contextMenuTargetProps,
    onContextMenu: (event: MouseEvent) => {
      event.preventDefault();
      openMenu({ x: event.clientX, y: event.clientY, label, entries, targets });
    },
  });

  // --- drag and drop: drop a module or folder onto a folder (or the project row: top level) ---
  const canDrop = (targetFolderId: string | null) =>
    editable &&
    drag !== null &&
    drag.projectId === projectId &&
    (targetFolderId === null || !drag.blocked.has(targetFolderId));
  const dragProps = (kind: 'module' | 'folder', id: string, blocked: Set<string> = new Set()) =>
    editable
      ? {
          draggable: true,
          onDragStart: (event: DragEvent) => {
            event.stopPropagation();
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData(TREE_DRAG_TYPE, JSON.stringify({ projectId, kind, id }));
            setDrag({ projectId, kind, id, blocked });
          },
          onDragEnd: () => {
            setDrag(null);
            setDropTarget(null);
          },
        }
      : {};
  const dropProps = (targetFolderId: string | null) => ({
    onDragOver: (event: DragEvent) => {
      if (!canDrop(targetFolderId) || !event.dataTransfer.types.includes(TREE_DRAG_TYPE)) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = 'move';
      setDropTarget(targetFolderId ?? '');
    },
    onDragLeave: () => setDropTarget((current) => (current === (targetFolderId ?? '') ? null : current)),
    onDrop: (event: DragEvent) => {
      if (!canDrop(targetFolderId) || !drag) return;
      event.preventDefault();
      event.stopPropagation();
      const moved = drag;
      setDrag(null);
      setDropTarget(null);
      const request =
        moved.kind === 'module'
          ? state.commands.moduleFolders.moveModule(projectId, moved.id, targetFolderId)
          : state.commands.moduleFolders.moveFolder(projectId, moved.id, targetFolderId);
      request.catch((err) =>
        notifications.show({ message: err instanceof ApiError ? err.message : 'Failed to move', color: 'red' }),
      );
    },
  });
  const dropHighlight = (targetFolderId: string | null) =>
    dropTarget === (targetFolderId ?? '') ? { outline: '2px dashed var(--mantine-primary-color-filled)' } : undefined;

  const toggleFolder = (folderId: string) => {
    const key = `${projectId}/${folderId}`;
    setCollapsed((current) => (current.includes(key) ? current.filter((k) => k !== key) : [...current, key]));
  };
  const renderNode = (node: ModuleTreeNode, loadedProject: Project): ReactNode => {
    const position = { parentId: node.parentId, index: node.index, siblingCount: node.siblingCount };
    if (node.kind === 'module') {
      const { module } = node;
      return (
        <NavLink
          key={`module:${module.id}`}
          label={module.name}
          description={module.prefix}
          leftSection={<ModuleTypeIcon module={module} size={14} />}
          active={state.projectId === projectId && activeModuleId === module.id}
          onClick={() => onOpenModule(projectId, module.id)}
          {...dragProps('module', module.id)}
          {...menuProps(module.name, MODULE_NODE_MENU, {
            module: { projectId, module, moduleIds, position },
            project: loadedProject,
          })}
        />
      );
    }
    const opened = !collapsed.includes(`${projectId}/${node.id}`);
    return (
      <NavLink
        key={`folder:${node.id}`}
        label={node.name}
        leftSection={opened ? <IconFolderOpen size={14} /> : <IconFolder size={14} />}
        opened={opened}
        onChange={() => toggleFolder(node.id)}
        childrenOffset={14}
        style={dropHighlight(node.id)}
        {...dragProps('folder', node.id, folderSubtreeIds(node.entry))}
        {...dropProps(node.id)}
        {...menuProps(node.name, FOLDER_NODE_MENU, {
          folder: { projectId, folder: node.entry, position },
          project: loadedProject,
        })}
      >
        {node.children.length === 0 ? (
          <Text c="dimmed" size="xs" px="sm" py={2}>
            Empty folder
          </Text>
        ) : (
          node.children.map((child) => renderNode(child, loadedProject))
        )}
      </NavLink>
    );
  };

  return (
    <Stack gap={0} h="100%">
      <Stack gap={0} px="xs" pt="xs">
        <Text
          fw={600}
          size="sm"
          px="sm"
          truncate="end"
          style={dropHighlight(null)}
          title={editable ? 'Drop a module or folder here to move it to the top level' : undefined}
          {...(editable ? dropProps(null) : {})}
          {...menuProps(project.name, PROJECT_NODE_MENU, { project })}
        >
          {project.name}{' '}
          {!isProjectStub(project) && <ProjectLockBadge lock={project.locked} compact />}
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
            {...menuProps(child.name, PROJECT_NODE_MENU, { project: child })}
          />
        ))}
        {canAddSubproject && (
          <NavLink
            label="New subproject"
            leftSection={<IconFolderPlus size={14} />}
            onClick={() => run('project.newSubproject', { project })}
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
            {treeQuery.isError && (
              <Text c="red" size="xs" px="sm" py={2}>
                Failed to load module folders; modules are listed without them
              </Text>
            )}
            {nodes.map((node) => renderNode(node, project))}
            {editable && (
              <>
                <NavLink
                  label="New module"
                  leftSection={<IconPlus size={14} />}
                  onClick={() => run('project.newModule', { project })}
                  c="dimmed"
                />
                <NavLink
                  label="New folder"
                  leftSection={<IconFolderPlus size={14} />}
                  onClick={() => run('project.newFolder', { project })}
                  c="dimmed"
                />
              </>
            )}
          </>
        )}
      </ScrollArea>
    </Stack>
  );
}
