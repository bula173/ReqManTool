import { IconBrandGit, IconCloudPlus, IconGitPullRequest, IconPlugConnected } from '@tabler/icons-react';
import type { Action, ActionContext } from './types';

/**
 * GitLab / GitHub integration. Every provider call runs on the server with
 * the connection's keychain token. Publishing and reviews are git remote
 * operations like Push: they never change project data, so they stay
 * available while a project is locked (not `edits`).
 */

/** The open project has its own repository (with commits) on `current` or another branch. */
function hasCommits(ctx: ActionContext<'app'>): boolean {
  const state = ctx.gitStatus?.state;
  return ctx.project !== null && (state === 'ready' || state === 'paused') && ctx.gitStatus?.head != null;
}

export const providerActions: Action<'app'>[] = [
  {
    // Tools ▸ GitLab / GitHub connections (settings dialog).
    id: 'tools.providerConnections',
    element: 'app',
    label: 'GitLab / GitHub connections',
    icon: IconPlugConnected,
    opensDialog: true,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'providerConnections' }),
  },
  {
    // File ▸ Add project from GitLab / GitHub: browse the user's repositories and clone one.
    id: 'file.addProjectFromProvider',
    element: 'app',
    label: 'Add project from GitLab / GitHub',
    icon: IconCloudPlus,
    opensDialog: true,
    isAvailable: () => true,
    run: (ctx) => ctx.commands.openDialog({ kind: 'addProjectFromProvider' }),
  },
  {
    // Create the remote repository, set it as the remote, push. Only without a remote.
    id: 'project.publish',
    element: 'app',
    label: 'Publish project to GitLab / GitHub',
    icon: IconBrandGit,
    opensDialog: true,
    isAvailable: (ctx) => hasCommits(ctx) && ctx.gitStatus?.remote?.url == null,
    run: (ctx) => {
      if (!ctx.project) return;
      ctx.commands.openDialog({
        kind: 'publishProject',
        projectId: ctx.project.id,
        projectName: ctx.project.name,
        projectDescription: ctx.project.description,
      });
    },
  },
  {
    // Push `current` to a review branch and open a merge / pull request.
    id: 'project.requestReview',
    element: 'app',
    label: 'Request review (merge / pull request)',
    icon: IconGitPullRequest,
    opensDialog: true,
    isAvailable: (ctx) => hasCommits(ctx) && ctx.gitStatus?.remote?.url != null,
    run: (ctx) => {
      if (!ctx.project) return;
      ctx.commands.openDialog({ kind: 'startReview', projectId: ctx.project.id });
    },
  },
];
