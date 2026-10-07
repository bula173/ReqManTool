import type { DialogRequest } from '../actions/types';
import { AboutModal } from '../components/AboutModal';
import { AddProjectFromRemoteModal } from '../components/AddProjectFromRemoteModal';
import { DataDirModal } from '../components/DataDirSettings';
import { ImportCsvModal } from '../components/ImportCsvModal';
import { ImportTemplateModal } from '../components/ImportTemplateModal';
import { KeyboardShortcutsModal } from '../components/KeyboardShortcutsModal';
import { NewProjectModal } from '../components/NewProjectModal';
import { RowStylesModal } from '../components/RowStylesModal';
import { SaveAsTemplateModal } from '../components/SaveAsTemplateModal';

/**
 * The app-wide dialogs that registry actions open (`commands.openDialog`).
 * One dialog at a time; each one is the existing component, mounted while
 * requested so it starts fresh every time.
 */
export function AppDialogs({ dialog, onClose }: { dialog: DialogRequest | null; onClose: () => void }) {
  if (!dialog) return null;
  switch (dialog.kind) {
    case 'newProject':
      return (
        <NewProjectModal
          opened
          onClose={onClose}
          defaultParentProjectId={dialog.parentProjectId}
          defaultTemplateId={dialog.templateId}
        />
      );
    case 'addProjectFromRemote':
      return <AddProjectFromRemoteModal opened onClose={onClose} />;
    case 'dataDir':
      return <DataDirModal opened onClose={onClose} />;
    case 'saveAsTemplate':
      return <SaveAsTemplateModal opened onClose={onClose} project={dialog.project} />;
    case 'rowStyles':
      return <RowStylesModal opened onClose={onClose} project={dialog.project} />;
    case 'importTemplate':
      return <ImportTemplateModal opened onClose={onClose} />;
    case 'importCsv':
      return (
        <ImportCsvModal
          opened
          onClose={onClose}
          projectId={dialog.projectId}
          moduleId={dialog.moduleId}
          moduleName={dialog.moduleName}
        />
      );
    case 'shortcuts':
      return <KeyboardShortcutsModal opened onClose={onClose} />;
    case 'about':
      return <AboutModal opened onClose={onClose} />;
  }
}
