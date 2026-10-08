import { notifications } from '@mantine/notifications';
import {
  IconAppWindow,
  IconArrowBarToLeft,
  IconArrowLeft,
  IconArrowRight,
  IconExternalLink,
  IconLayoutNavbarExpand,
  IconSquareX,
  IconX,
} from '@tabler/icons-react';
import { tabKey } from '../layout/moduleTabs';
import type { Action, ActionContext } from './types';

/**
 * Module tabs and detached module windows: the tab bar's context menu, the
 * View menu (active tab), the sidebar's module node menu ("Open in new tab",
 * "Open in new window") and the requirement navigation menu ("Open in new
 * window"). A side-by-side split view is not offered yet (backlog).
 */

type TabContext = ActionContext<'tab'>;

const POPUP_BLOCKED = 'The browser blocked the new window. Allow pop-ups for ReqManTool and try again.';

function tabIndex(ctx: TabContext): number {
  const target = ctx.target;
  return target ? ctx.tabs.findIndex((t) => tabKey(t) === tabKey(target)) : -1;
}

function moveTab(ctx: TabContext, offset: number) {
  const target = ctx.target;
  const index = tabIndex(ctx);
  if (!target || index < 0) return;
  ctx.commands.moveTab(tabKey(target), index + offset);
}

export const tabActions: Action<'tab'>[] = [
  {
    id: 'tab.close',
    element: 'tab',
    label: 'Close tab',
    icon: IconX,
    isAvailable: (ctx) => tabIndex(ctx) >= 0,
    run: (ctx) => {
      if (ctx.target) ctx.commands.closeTabs([tabKey(ctx.target)]);
    },
  },
  {
    id: 'tab.closeOthers',
    element: 'tab',
    label: 'Close other tabs',
    icon: IconSquareX,
    isAvailable: (ctx) => tabIndex(ctx) >= 0 && ctx.tabs.length > 1,
    run: (ctx) => {
      const target = ctx.target;
      if (!target) return;
      ctx.commands.closeTabs(ctx.tabs.filter((t) => tabKey(t) !== tabKey(target)).map(tabKey));
    },
  },
  {
    id: 'tab.closeToRight',
    element: 'tab',
    label: 'Close tabs to the right',
    icon: IconArrowBarToLeft,
    isAvailable: (ctx) => {
      const index = tabIndex(ctx);
      return index >= 0 && index < ctx.tabs.length - 1;
    },
    run: (ctx) => {
      const index = tabIndex(ctx);
      if (index >= 0) ctx.commands.closeTabs(ctx.tabs.slice(index + 1).map(tabKey));
    },
  },
  {
    id: 'tab.moveLeft',
    element: 'tab',
    label: 'Move tab left',
    icon: IconArrowLeft,
    isAvailable: (ctx) => tabIndex(ctx) > 0,
    run: (ctx) => moveTab(ctx, -1),
  },
  {
    id: 'tab.moveRight',
    element: 'tab',
    label: 'Move tab right',
    icon: IconArrowRight,
    isAvailable: (ctx) => {
      const index = tabIndex(ctx);
      return index >= 0 && index < ctx.tabs.length - 1;
    },
    run: (ctx) => moveTab(ctx, 1),
  },
  {
    id: 'tab.detach',
    element: 'tab',
    label: 'Detach into new window',
    icon: IconAppWindow,
    isAvailable: (ctx) => tabIndex(ctx) >= 0,
    run: (ctx) => {
      if (ctx.target && !ctx.commands.detachTab(ctx.target)) {
        notifications.show({ message: POPUP_BLOCKED, color: 'red' });
      }
    },
  },
];

export const moduleWindowActions: Action<'module'>[] = [
  {
    id: 'module.openInNewTab',
    element: 'module',
    label: 'Open in new tab',
    icon: IconLayoutNavbarExpand,
    // Detached windows have no tab bar.
    isSupported: (ctx) => !ctx.detached,
    isAvailable: (ctx) => {
      const target = ctx.target;
      return target !== null && !ctx.tabs.some((t) => t.projectId === target.projectId && t.moduleId === target.module.id);
    },
    run: (ctx) => {
      if (ctx.target) ctx.commands.openModuleTab(ctx.target.projectId, ctx.target.module.id);
    },
  },
  {
    id: 'module.openInWindow',
    element: 'module',
    label: 'Open in new window',
    icon: IconExternalLink,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      if (ctx.target && !ctx.commands.openModuleWindow(ctx.target.projectId, ctx.target.module.id)) {
        notifications.show({ message: POPUP_BLOCKED, color: 'red' });
      }
    },
  },
];

export const requirementWindowActions: Action<'requirement'>[] = [
  {
    // The requirement's module (the open one) in its own window, revealing the requirement.
    id: 'requirement.openInWindow',
    element: 'requirement',
    label: 'Open in new window',
    icon: IconExternalLink,
    isSupported: (ctx) => !ctx.detached,
    isAvailable: (ctx) => ctx.target !== null && ctx.projectId !== null && ctx.moduleId !== null,
    run: (ctx) => {
      if (!ctx.target || !ctx.projectId || !ctx.moduleId) return;
      if (!ctx.commands.openModuleWindow(ctx.projectId, ctx.moduleId, ctx.target.id)) {
        notifications.show({ message: POPUP_BLOCKED, color: 'red' });
      }
    },
  },
];

export const windowActions: Action<'app'>[] = [
  {
    id: 'window.reattach',
    element: 'app',
    label: 'Re-attach to main window',
    icon: IconLayoutNavbarExpand,
    isSupported: (ctx) => ctx.detached,
    isAvailable: (ctx) => ctx.detached && ctx.projectId !== null && ctx.moduleId !== null,
    run: (ctx) => ctx.commands.reattach(),
  },
];
