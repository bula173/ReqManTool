/** The left panel's tabs and the per-browser storage of the active one (shared by the sidebar and actions). */
export type LeftPanelTab = 'workspace' | 'project' | 'module';

export const LEFT_PANEL_TABS: LeftPanelTab[] = ['workspace', 'project', 'module'];
export const LEFT_PANEL_TAB_STORAGE_KEY = 'reqman.leftPanel.tab';
