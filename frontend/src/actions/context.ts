import { createContext, useContext } from 'react';
import type { ActionTargetOverrides, AppState } from './types';

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
}

export const AppActionsContext = createContext<AppActionsValue | null>(null);

/** The shared action registry's state and runner; requires `AppActionsProvider`. */
export function useAppActions(): AppActionsValue {
  const value = useContext(AppActionsContext);
  if (!value) throw new Error('useAppActions must be used inside AppActionsProvider');
  return value;
}
