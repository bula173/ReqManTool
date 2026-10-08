import {
  IconBook,
  IconColumns,
  IconColumnsOff,
  IconDeviceFloppy,
  IconCloudDownload,
  IconCloudPlus,
  IconCloudUpload,
  IconFileExport,
  IconFileImport,
  IconFolderOpen,
  IconFolderPlus,
  IconHistory,
  IconInfoCircle,
  IconKeyboard,
  IconLayoutSidebar,
  IconLayoutSidebarRight,
  IconLineHeight,
  IconMoon,
  IconPalette,
  IconShieldCheck,
  IconTable,
  IconTemplate,
  IconUpload,
} from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { API_BASE_URL, ApiError } from '../api/client';
import { moduleTypeOf } from '../api/moduleTypes';
import {
  hasColumnWidthOverrides,
  hasRowHeightOverrides,
  moduleTableKey,
  resetColumnWidths,
  resetRowHeights,
  viewColumnWidths,
} from '../hooks/columnLayout';
import type { Action, ActionContext, DynamicMenuItem, ExportFormat } from './types';

/** Application-wide actions: the File, View and Help menus. */

type AppContext = ActionContext<'app'>;

export const REPOSITORY_URL = 'https://github.com/bula173/ReqManTool';

/** The documentation links of Help ▸ Documentation. */
export const DOCUMENTATION_LINKS: { key: string; label: string; url: string }[] = [
  { key: 'readme', label: 'User guide (README)', url: `${REPOSITORY_URL}#readme` },
  { key: 'architecture', label: 'Architecture', url: `${REPOSITORY_URL}/blob/main/docs/ARCHITECTURE.md` },
  { key: 'backlog', label: 'Features and backlog', url: `${REPOSITORY_URL}/blob/main/docs/BACKLOG.md` },
  { key: 'api', label: 'REST API reference (this server)', url: `${API_BASE_URL}/docs` },
];

/** Download URL of a project export (the exports cover the whole project). */
export function exportUrl(projectId: string, format: ExportFormat): string {
  return `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/export/${format}`;
}

const EXPORT_LABELS: Record<ExportFormat, string> = {
  reqif: 'ReqIF',
  csv: 'CSV',
  xlsx: 'Excel (XLSX)',
  docx: 'Word (DOCX)',
};

/** Exports cover the whole project, so they are offered whenever a project is open (module or not). */
const exportAction = (format: ExportFormat): Action<'app'> => ({
  id: `file.export.${format}`,
  element: 'app',
  label: EXPORT_LABELS[format],
  icon: IconFileExport,
  isAvailable: (ctx) => ctx.project !== null,
  run: (ctx) => {
    if (ctx.project) ctx.commands.download(exportUrl(ctx.project.id, format));
  },
});

function templateItems(ctx: AppContext): DynamicMenuItem[] {
  const { templates } = ctx;
  if (templates.isLoading) return [{ key: 'loading', label: 'Loading templates…', disabled: true }];
  if (templates.isError) return [{ key: 'error', label: 'Failed to load templates', disabled: true }];
  if (templates.data.length === 0) return [{ key: 'empty', label: 'No templates', disabled: true }];
  return templates.data.map((template) => ({
    key: template.id,
    label: `${template.name}${template.source === 'user' ? ' (user)' : ''}${template.error ? ' (invalid)' : ''}…`,
    disabled: template.error !== null,
    description: template.error ?? (template.description || undefined),
    run: () => ctx.commands.openDialog({ kind: 'newProject', templateId: template.id }),
  }));
}

function viewItems(ctx: AppContext): DynamicMenuItem[] {
  return [
    {
      key: '__all__',
      label: 'All attributes',
      checked: ctx.activeViewId === null,
      run: () => ctx.commands.setActiveView(null),
    },
    ...ctx.views.map((view) => ({
      key: view.id,
      label: view.name,
      checked: ctx.activeViewId === view.id,
      run: () => ctx.commands.setActiveView(view.id),
    })),
  ];
}

/** The open module's requirements table key (per view), or null outside a module. */
function openTableKey(ctx: AppContext): string | null {
  return ctx.projectId && ctx.module ? moduleTableKey(ctx.projectId, ctx.module.id, ctx.activeViewId) : null;
}

function activeView(ctx: AppContext) {
  return ctx.module ? (ctx.views.find((view) => view.id === ctx.activeViewId) ?? null) : null;
}

function showError(fallback: string) {
  return (err: unknown) =>
    notifications.show({ message: err instanceof ApiError ? err.message : fallback, color: 'red' });
}

export const appActions: Action<'app'>[] = [
  // --- File ---------------------------------------------------------------
  {
    id: 'file.newProject',
    requires: ['createProjects'],
    element: 'app',
    label: 'New project',
    icon: IconFolderPlus,
    opensDialog: true,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'newProject' }),
  },
  {
    // Clones a remote git repository into the data directory as a project.
    id: 'file.addProjectFromRemote',
    requires: ['createProjects', 'connections'],
    element: 'app',
    label: 'Add project from remote',
    icon: IconCloudPlus,
    opensDialog: true,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'addProjectFromRemote' }),
  },
  {
    id: 'file.openFolder',
    requires: ['serverAdmin'],
    element: 'app',
    label: 'Open project folder',
    icon: IconFolderOpen,
    opensDialog: true,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'dataDir' }),
  },
  {
    id: 'file.newProjectFromTemplate',
    requires: ['createProjects'],
    element: 'app',
    label: 'New project from template',
    icon: IconTemplate,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'newProject' }),
    items: templateItems,
    // The list is fetched only once this submenu (or a template picker) opens.
    onItemsShown: (ctx) => ctx.commands.requestTemplates(),
  },
  {
    id: 'file.importTemplate',
    element: 'app',
    label: 'Import template file',
    icon: IconUpload,
    opensDialog: true,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'importTemplate' }),
  },
  {
    // Creates the open project's git repository (or its `current` branch);
    // offered only while the server reports that it can (see GitStatus).
    id: 'project.enableHistory',
    requires: ['projectAdmin'],
    element: 'app',
    label: 'Enable history (git)',
    icon: IconHistory,
    isAvailable: (ctx) => ctx.project !== null && ctx.gitStatus?.can_enable === true,
    run: (ctx) => {
      ctx.commands.enableHistory().then(
        (status) =>
          notifications.show({
            message:
              status.state === 'ready'
                ? 'History enabled: every change is now committed to the project repository'
                : (status.message ?? 'History could not be enabled'),
            color: status.state === 'ready' ? 'green' : 'yellow',
          }),
        (err) =>
          notifications.show({
            message: err instanceof ApiError ? err.message : 'Failed to enable history',
            color: 'red',
          }),
      );
    },
  },
  {
    // Explicit push of the open project to its configured remote (Git panel parity).
    id: 'project.gitPush',
    requires: ['projectEditor'],
    element: 'app',
    label: 'Push to remote',
    icon: IconCloudUpload,
    isAvailable: (ctx) => ctx.project !== null && ctx.gitStatus?.remote?.url != null,
    run: (ctx) => {
      ctx.commands.gitPush().then(
        (result) =>
          notifications.show({
            message: `Pushed ${result.pushed.length} ref${result.pushed.length === 1 ? '' : 's'} to ${result.url}`,
            color: 'green',
          }),
        (err) =>
          notifications.show({
            message: err instanceof ApiError ? err.message : 'Push failed',
            color: 'red',
          }),
      );
    },
  },
  {
    // Explicit fetch + fast-forward of the open project from its remote.
    id: 'project.gitPull',
    requires: ['projectEditor'],
    element: 'app',
    label: 'Pull from remote',
    icon: IconCloudDownload,
    isAvailable: (ctx) => ctx.project !== null && ctx.gitStatus?.remote?.url != null,
    run: (ctx) => {
      ctx.commands.gitPull().then(
        (result) =>
          notifications.show({
            message: result.updated
              ? 'Pulled: the project was updated'
              : `Already up to date${result.notes.length > 0 ? ` (${result.notes.join(' ')})` : ''}`,
            color: result.updated ? 'green' : 'gray',
          }),
        (err) =>
          notifications.show({
            message: err instanceof ApiError ? err.message : 'Pull failed',
            color: 'red',
          }),
      );
    },
  },
  {
    // Imports into the open module; listed as File ▸ Import ▸ CSV.
    id: 'file.importCsv',
    element: 'app',
    edits: true,
    label: 'CSV',
    icon: IconFileImport,
    opensDialog: true,
    isAvailable: (ctx) => ctx.projectId !== null && ctx.module !== null,
    run: (ctx) => {
      if (ctx.projectId && ctx.module) {
        ctx.commands.openDialog({
          kind: 'importCsv',
          projectId: ctx.projectId,
          moduleId: ctx.module.id,
          moduleName: ctx.module.name,
        });
      }
    },
  },
  exportAction('reqif'),
  exportAction('csv'),
  exportAction('xlsx'),
  exportAction('docx'),

  // --- View ---------------------------------------------------------------
  {
    id: 'view.attributeView',
    element: 'app',
    label: 'Attribute view',
    icon: IconTable,
    isAvailable: (ctx) => ctx.module !== null,
    run: (ctx) => ctx.commands.setActiveView(null),
    items: viewItems,
  },
  {
    // The computed, read-only Verification status column of requirements modules (per browser).
    id: 'view.verificationColumn',
    element: 'app',
    label: 'Verification status column',
    icon: IconShieldCheck,
    isAvailable: (ctx) => ctx.module !== null && moduleTypeOf(ctx.module) === 'requirements',
    isChecked: (ctx) => ctx.verificationColumn,
    run: (ctx) => ctx.commands.setVerificationColumn(!ctx.verificationColumn),
  },
  {
    // The left panel (project/module tree): collapses to a rail; an overlay on narrow screens.
    id: 'view.toggleLeftPanel',
    element: 'app',
    label: 'Navigation panel',
    icon: IconLayoutSidebar,
    shortcuts: [{ key: 'b', mod: true }],
    isAvailable: () => true,
    isChecked: (ctx) => ctx.leftPanelOpen,
    run: (ctx) => ctx.commands.setLeftPanelOpen(!ctx.leftPanelOpen),
  },
  {
    // The module page's right panel: collapses to a rail; an overlay on narrow screens.
    id: 'view.toggleDetailPanel',
    element: 'app',
    label: 'Requirement detail panel',
    icon: IconLayoutSidebarRight,
    shortcuts: [{ key: 'b', mod: true, alt: true }],
    isAvailable: () => true,
    isChecked: (ctx) => ctx.detailPanelOpen,
    run: (ctx) => ctx.commands.setDetailPanelOpen(!ctx.detailPanelOpen),
  },
  {
    // This browser's widths of the open table (module + view); wrap toggles are kept.
    id: 'view.resetColumnWidths',
    element: 'app',
    label: 'Reset column widths',
    icon: IconColumns,
    isAvailable: (ctx) => {
      const key = openTableKey(ctx);
      return key !== null && hasColumnWidthOverrides(key);
    },
    run: (ctx) => {
      const key = openTableKey(ctx);
      if (key) resetColumnWidths(key);
    },
  },
  {
    // This browser's fixed row heights of the open table (module + view): every row fits its content again.
    id: 'view.resetRowHeights',
    element: 'app',
    label: 'Reset row heights',
    icon: IconLineHeight,
    isAvailable: (ctx) => {
      const key = openTableKey(ctx);
      return key !== null && hasRowHeightOverrides(key);
    },
    run: (ctx) => {
      const key = openTableKey(ctx);
      if (key) resetRowHeights(key);
    },
  },
  {
    // Shares the current widths through the active view in project.json.
    id: 'view.saveColumnWidths',
    element: 'app',
    edits: true,
    label: 'Save column widths to view',
    icon: IconDeviceFloppy,
    isAvailable: (ctx) => activeView(ctx) !== null && ctx.project !== null,
    run: (ctx) => {
      const view = activeView(ctx);
      const key = openTableKey(ctx);
      if (!view || !key || !ctx.project) return;
      ctx.commands.setViewColumnWidths(view.id, viewColumnWidths(key, view, ctx.project.attributes)).then(
        () => {
          // The view now holds these widths; this browser's copies are redundant.
          resetColumnWidths(key);
          notifications.show({ message: `Column widths saved to view "${view.name}"`, color: 'green' });
        },
        showError('Failed to save the column widths'),
      );
    },
  },
  {
    id: 'view.removeViewColumnWidths',
    element: 'app',
    edits: true,
    label: 'Remove column widths from view',
    icon: IconColumnsOff,
    isAvailable: (ctx) => activeView(ctx)?.column_widths !== undefined,
    run: (ctx) => {
      const view = activeView(ctx);
      if (!view) return;
      ctx.commands.setViewColumnWidths(view.id, null).then(
        () => notifications.show({ message: `Column widths removed from view "${view.name}"`, color: 'green' }),
        showError('Failed to remove the column widths'),
      );
    },
  },
  {
    // Project-wide row font/background colors by enum attribute value.
    id: 'view.rowStyles',
    element: 'app',
    edits: true,
    label: 'Row styles',
    icon: IconPalette,
    opensDialog: true,
    isAvailable: (ctx) => ctx.project !== null,
    run: (ctx) => {
      if (ctx.project) ctx.commands.openDialog({ kind: 'rowStyles', project: ctx.project });
    },
  },
  {
    id: 'view.toggleDarkMode',
    element: 'app',
    label: 'Dark mode',
    icon: IconMoon,
    isAvailable: () => true,
    isChecked: (ctx) => ctx.colorScheme === 'dark',
    run: (ctx) => ctx.commands.setColorScheme(ctx.colorScheme === 'dark' ? 'light' : 'dark'),
  },

  // --- Help ---------------------------------------------------------------
  {
    id: 'help.shortcuts',
    element: 'app',
    label: 'Keyboard shortcuts',
    icon: IconKeyboard,
    opensDialog: true,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'shortcuts' }),
  },
  {
    id: 'help.documentation',
    element: 'app',
    label: 'Documentation',
    icon: IconBook,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openExternal(DOCUMENTATION_LINKS[0].url),
    items: (ctx) =>
      DOCUMENTATION_LINKS.map((link) => ({
        key: link.key,
        label: link.label,
        description: link.url,
        run: () => ctx.commands.openExternal(link.url),
      })),
  },
  {
    id: 'help.about',
    element: 'app',
    label: 'About ReqManTool',
    icon: IconInfoCircle,
    opensDialog: true,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'about' }),
  },
];
