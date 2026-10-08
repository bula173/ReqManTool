import { createContext, useContext } from 'react';
import type { ActionTargetOverrides, AppState, MenuRequest } from './types';

export interface AppActionsValue {
  state: AppState;
  /**
   * Runs a registry action if it is available. `targets` holds the clicked
   * element keyed by element type (e.g. `{ requirement: row }`) for context
   * menus and row buttons; without an entry for the action's element type,
   * its default target is used (the selected requirement for requirement
   * actions). Returns whether it ran.
   */
  run: (id: string, targets?: ActionTargetOverrides) => boolean;
  /** Whether `run` would run the action now (for enabling buttons). */
  isAvailable: (id: string, targets?: ActionTargetOverrides) => boolean;
  /**
   * Shows a context menu (a `MenuEntry[]` layout with its clicked targets) at
   * a screen position. One menu at a time: it closes on Escape, an outside
   * click or an item click, and gives focus back to where it was.
   */
  openMenu: (request: MenuRequest) => void;
}

export const AppActionsContext = createContext<AppActionsValue | null>(null);

/** The shared action registry's state and runner; requires `AppActionsProvider`. */
export function useAppActions(): AppActionsValue {
  const value = useContext(AppActionsContext);
  if (!value) throw new Error('useAppActions must be used inside AppActionsProvider');
  return value;
}
