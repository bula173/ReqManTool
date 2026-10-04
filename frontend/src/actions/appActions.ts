import {
  IconBook,
  IconFileExport,
  IconFileImport,
  IconFolderOpen,
  IconFolderPlus,
  IconInfoCircle,
  IconKeyboard,
  IconLayoutSidebarRight,
  IconMoon,
  IconTable,
  IconTemplate,
  IconUpload,
} from '@tabler/icons-react';
import { API_BASE_URL } from '../api/client';
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

/** Exports are offered while a module is open (the export itself covers the whole project). */
const exportAction = (format: ExportFormat): Action<'app'> => ({
  id: `file.export.${format}`,
  element: 'app',
  label: EXPORT_LABELS[format],
  icon: IconFileExport,
  isAvailable: (ctx) => ctx.projectId !== null && ctx.module !== null,
  run: (ctx) => {
    if (ctx.projectId) ctx.commands.download(exportUrl(ctx.projectId, format));
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

export const appActions: Action<'app'>[] = [
  // --- File ---------------------------------------------------------------
  {
    id: 'file.newProject',
    element: 'app',
    label: 'New project',
    icon: IconFolderPlus,
    opensDialog: true,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'newProject' }),
  },
  {
    // A new project whose parent is the open project (the project page's button).
    id: 'project.newSubproject',
    element: 'app',
    label: 'New subproject',
    icon: IconFolderPlus,
    opensDialog: true,
    isAvailable: (ctx) => ctx.project !== null,
    run: (ctx) => {
      if (ctx.project) ctx.commands.openDialog({ kind: 'newProject', parentProjectId: ctx.project.id });
    },
  },
  {
    id: 'file.openFolder',
    element: 'app',
    label: 'Open project folder',
    icon: IconFolderOpen,
    opensDialog: true,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'dataDir' }),
  },
  {
    id: 'file.newProjectFromTemplate',
    element: 'app',
    label: 'New project from template',
    icon: IconTemplate,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'newProject' }),
    items: templateItems,
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
    id: 'file.saveAsTemplate',
    element: 'app',
    label: 'Save as template',
    icon: IconTemplate,
    opensDialog: true,
    isAvailable: (ctx) => ctx.project !== null,
    run: (ctx) => {
      if (ctx.project) ctx.commands.openDialog({ kind: 'saveAsTemplate', project: ctx.project });
    },
  },
  {
    id: 'file.importCsv',
    element: 'app',
    label: 'Import CSV',
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
    id: 'view.toggleDetailPanel',
    element: 'app',
    label: 'Requirement detail panel',
    icon: IconLayoutSidebarRight,
    isAvailable: () => true,
    isChecked: (ctx) => ctx.detailPanelOpen,
    run: (ctx) => ctx.commands.setDetailPanelOpen(!ctx.detailPanelOpen),
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
