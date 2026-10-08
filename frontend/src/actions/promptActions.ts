import { IconClipboardText, IconSparkles } from '@tabler/icons-react';
import type { Action } from './types';

/**
 * The AI prompt library: copy a generated prompt into any external AI chat,
 * then paste its CSV answer into the CSV import. ReqManTool itself never
 * contacts an AI service. Opening the Prompts dialog is read-only (also for
 * a locked project); pasting a result imports, so it `edits`.
 */

export const appPromptActions: Action<'app'>[] = [
  {
    // Tools ▸ AI prompts: the open module (target) and selected requirement.
    id: 'tools.aiPrompts',
    element: 'app',
    label: 'AI prompts',
    icon: IconSparkles,
    opensDialog: true,
    isAvailable: (ctx) => ctx.projectId !== null && ctx.module !== null,
    run: (ctx) => {
      if (!ctx.projectId || !ctx.module) return;
      ctx.commands.openDialog({
        kind: 'aiPrompts',
        projectId: ctx.projectId,
        moduleId: ctx.module.id,
        requirementId: ctx.selectedRequirement?.id ?? null,
      });
    },
  },
  {
    // Tools ▸ Paste AI result: the CSV import of the open module, with the paste box.
    id: 'tools.pasteAiResult',
    element: 'app',
    edits: true,
    editsModule: true,
    label: 'Paste AI result',
    icon: IconClipboardText,
    opensDialog: true,
    isAvailable: (ctx) => ctx.projectId !== null && ctx.module !== null,
    run: (ctx) => {
      if (!ctx.projectId || !ctx.module) return;
      ctx.commands.openDialog({
        kind: 'importCsv',
        projectId: ctx.projectId,
        moduleId: ctx.module.id,
        moduleName: ctx.module.name,
        input: 'paste',
      });
    },
  },
];

export const requirementPromptActions: Action<'requirement'>[] = [
  {
    // Requirement context menu: prompts about the clicked requirement (and its children).
    id: 'requirement.aiPrompts',
    element: 'requirement',
    label: 'AI prompts',
    icon: IconSparkles,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null && ctx.projectId !== null && ctx.module !== null,
    run: (ctx) => {
      if (!ctx.target || !ctx.projectId || !ctx.module) return;
      ctx.commands.openDialog({
        kind: 'aiPrompts',
        projectId: ctx.projectId,
        moduleId: ctx.module.id,
        requirementId: ctx.target.id,
      });
    },
  },
];

export const modulePromptActions: Action<'module'>[] = [
  {
    // Module node menu: prompts with this module as the target, no selection.
    id: 'module.aiPrompts',
    element: 'module',
    label: 'AI prompts',
    icon: IconSparkles,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      if (!ctx.target) return;
      ctx.commands.openDialog({
        kind: 'aiPrompts',
        projectId: ctx.target.projectId,
        moduleId: ctx.target.module.id,
        requirementId: null,
      });
    },
  },
];
