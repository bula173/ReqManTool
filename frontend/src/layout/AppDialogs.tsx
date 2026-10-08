import { useMemo } from 'react';
import type { DialogRequest } from '../actions/types';
import { applicableAttributes } from '../api/applicability';
import { useProject, useRequirements } from '../api/queries';
import type { Requirement } from '../api/types';
import { AboutModal } from '../components/AboutModal';
import { AddProjectFromRemoteModal } from '../components/AddProjectFromRemoteModal';
import { AttributeModulesModal } from '../components/AttributeModulesModal';
import { DataDirModal } from '../components/DataDirSettings';
import { ImportCsvModal } from '../components/ImportCsvModal';
import { ImportTemplateModal } from '../components/ImportTemplateModal';
import { KeyboardShortcutsModal } from '../components/KeyboardShortcutsModal';
import { LinkModal } from '../components/LinkModal';
import { MoveRequirementModal } from '../components/MoveRequirementModal';
import { NewModuleModal } from '../components/NewModuleModal';
import { NewProjectModal } from '../components/NewProjectModal';
import { RequirementFormModal } from '../components/RequirementFormModal';
import { RowStylesModal } from '../components/RowStylesModal';
import { SaveAsTemplateModal } from '../components/SaveAsTemplateModal';

type AddRequirementRequest = Extract<DialogRequest, { kind: 'addRequirement' }>;

/** The requirement form, with the attributes that apply to the module (kept stable so the form is not re-seeded). */
function AddRequirementDialog({ request, onClose }: { request: AddRequirementRequest; onClose: () => void }) {
  const projectQuery = useProject(request.projectId);
  const requirementsQuery = useRequirements(request.projectId, request.moduleId);
  const projectAttributes = projectQuery.data?.attributes;
  const attributes = useMemo(
    () => applicableAttributes(projectAttributes ?? [], request.moduleId),
    [projectAttributes, request.moduleId],
  );
  // "Add sibling" places the new requirement right after the clicked one: name it in the title.
  const afterId = useMemo(() => {
    if (request.insertIndex === undefined || request.insertIndex === 0) return undefined;
    const siblings = (requirementsQuery.data ?? [])
      .filter((r: Requirement) => r.parent_id === request.parentId)
      .sort((a, b) => a.order - b.order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    return siblings[request.insertIndex - 1]?.id;
  }, [requirementsQuery.data, request.insertIndex, request.parentId]);
  return (
    <RequirementFormModal
      projectId={request.projectId}
      moduleId={request.moduleId}
      attributes={attributes}
      opened={projectQuery.data !== undefined}
      parentId={request.parentId}
      insertIndex={request.insertIndex}
      afterId={afterId}
      onClose={onClose}
    />
  );
}

/** Move to parent: the parent choices follow the module's live requirement list. */
function MoveRequirementDialog({
  projectId,
  moduleId,
  source,
  onClose,
}: {
  projectId: string;
  moduleId: string;
  source: Requirement;
  onClose: () => void;
}) {
  const requirementsQuery = useRequirements(projectId, moduleId);
  return (
    <MoveRequirementModal
      projectId={projectId}
      moduleId={moduleId}
      source={source}
      allRequirements={requirementsQuery.data ?? []}
      onClose={onClose}
    />
  );
}

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
    case 'newModule':
      return <NewModuleModal projectId={dialog.projectId} onClose={onClose} />;
    case 'addRequirement':
      return <AddRequirementDialog request={dialog} onClose={onClose} />;
    case 'linkRequirement':
      return (
        <LinkModal projectId={dialog.projectId} moduleId={dialog.moduleId} source={dialog.source} onClose={onClose} />
      );
    case 'moveRequirement':
      return (
        <MoveRequirementDialog
          projectId={dialog.projectId}
          moduleId={dialog.moduleId}
          source={dialog.source}
          onClose={onClose}
        />
      );
    case 'attributeModules':
      return <AttributeModulesModal projectId={dialog.projectId} attribute={dialog.attribute} onClose={onClose} />;
    case 'shortcuts':
      return <KeyboardShortcutsModal opened onClose={onClose} />;
    case 'about':
      return <AboutModal opened onClose={onClose} />;
  }
}
