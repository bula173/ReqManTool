import { useMemo } from 'react';
import type { DialogRequest } from '../actions/types';
import { applicableAttributes } from '../api/applicability';
import { useModule, useProject, useRequirements } from '../api/queries';
import type { Requirement } from '../api/types';
import { AboutModal } from '../components/AboutModal';
import { useAppActions } from '../actions/context';
import { AddProjectFromProviderModal } from '../components/AddProjectFromProviderModal';
import { AddProjectFromRemoteModal } from '../components/AddProjectFromRemoteModal';
import { ProviderConnectionsModal } from '../components/ProviderConnectionsModal';
import { PublishProjectModal } from '../components/PublishProjectModal';
import { StartReviewDialog } from '../components/StartReviewDialog';
import { AttributeModulesModal } from '../components/AttributeModulesModal';
import { CheckOutModuleModal } from '../components/CheckOutModuleModal';
import { DataDirModal } from '../components/DataDirSettings';
import { DropLinkModal } from '../components/DropLinkModal';
import { ImportCsvModal } from '../components/ImportCsvModal';
import { PromptsModal } from '../components/PromptsModal';
import { ImportTemplateModal } from '../components/ImportTemplateModal';
import { ImportTestResultsModal } from '../components/ImportTestResultsModal';
import { KeyboardShortcutsModal } from '../components/KeyboardShortcutsModal';
import { LinkModal } from '../components/LinkModal';
import { LockProjectModal } from '../components/LockProjectModal';
import { ModuleFolderNameModal } from '../components/ModuleFolderNameModal';
import { MoveRequirementModal } from '../components/MoveRequirementModal';
import { MoveToFolderModal } from '../components/MoveToFolderModal';
import { NewModuleModal } from '../components/NewModuleModal';
import { NewProjectModal } from '../components/NewProjectModal';
import { ProjectReferencesModal } from '../components/ProjectReferencesModal';
import { RemoveProjectModal } from '../components/RemoveProjectModal';
import { RenameProjectModal } from '../components/RenameProjectModal';
import { RequirementFormModal } from '../components/RequirementFormModal';
import { RowStylesModal } from '../components/RowStylesModal';
import { ApplySkeletonModal } from '../components/ApplySkeletonModal';
import { SaveAsTemplateModal } from '../components/SaveAsTemplateModal';
import { SaveModuleAsTemplateModal } from '../components/SaveModuleAsTemplateModal';
import { SaveLayoutAsViewModal } from '../components/SaveLayoutAsViewModal';
import { ViewEditorModal } from '../components/ViewEditorModal';

type AddRequirementRequest = Extract<DialogRequest, { kind: 'addRequirement' }>;

/** The requirement form, with the attributes that apply to the module (kept stable so the form is not re-seeded). */
function AddRequirementDialog({ request, onClose }: { request: AddRequirementRequest; onClose: () => void }) {
  const projectQuery = useProject(request.projectId);
  const moduleQuery = useModule(request.projectId, request.moduleId);
  const requirementsQuery = useRequirements(request.projectId, request.moduleId);
  const projectAttributes = projectQuery.data?.attributes;
  const moduleType = moduleQuery.data?.type;
  const attributes = useMemo(
    () => applicableAttributes(projectAttributes ?? [], { id: request.moduleId, type: moduleType }),
    [projectAttributes, request.moduleId, moduleType],
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
      opened={projectQuery.data !== undefined && moduleQuery.data !== undefined}
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
  const { run, state } = useAppActions();
  if (!dialog) return null;
  const manageConnections = () => run('tools.providerConnections');
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
    case 'providerConnections':
      return <ProviderConnectionsModal onClose={onClose} />;
    case 'addProjectFromProvider':
      return <AddProjectFromProviderModal onClose={onClose} onManageConnections={manageConnections} />;
    case 'publishProject':
      return (
        <PublishProjectModal
          projectId={dialog.projectId}
          projectName={dialog.projectName}
          projectDescription={dialog.projectDescription}
          onClose={onClose}
          onManageConnections={manageConnections}
        />
      );
    case 'startReview':
      return <StartReviewDialog projectId={dialog.projectId} onClose={onClose} />;
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
          initialInput={dialog.input}
          initialMode={dialog.mode}
        />
      );
    case 'aiPrompts':
      return (
        <PromptsModal
          projectId={dialog.projectId}
          moduleId={dialog.moduleId}
          requirementId={dialog.requirementId}
          onClose={onClose}
        />
      );
    case 'importTestResults':
      return (
        <ImportTestResultsModal
          onClose={onClose}
          projectId={dialog.projectId}
          moduleId={dialog.moduleId}
          moduleName={dialog.moduleName}
        />
      );
    case 'newModule':
      return (
        <NewModuleModal
          projectId={dialog.projectId}
          folderId={dialog.folderId ?? null}
          fromTemplate={dialog.fromTemplate ?? false}
          onClose={onClose}
        />
      );
    case 'saveModuleAsTemplate':
      return (
        <SaveModuleAsTemplateModal
          projectId={dialog.projectId}
          moduleId={dialog.moduleId}
          moduleName={dialog.moduleName}
          onClose={onClose}
        />
      );
    case 'applySkeleton':
      return (
        <ApplySkeletonModal
          projectId={dialog.projectId}
          moduleId={dialog.moduleId}
          moduleName={dialog.moduleName}
          moduleType={dialog.moduleType}
          onClose={onClose}
        />
      );
    case 'newModuleFolder':
      return <ModuleFolderNameModal projectId={dialog.projectId} parentId={dialog.parentId} onClose={onClose} />;
    case 'renameModuleFolder':
      return (
        <ModuleFolderNameModal
          projectId={dialog.projectId}
          rename={{ folderId: dialog.folderId, name: dialog.name }}
          onClose={onClose}
        />
      );
    case 'moveToFolder':
      return <MoveToFolderModal projectId={dialog.projectId} item={dialog.item} onClose={onClose} />;
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
    case 'dropLink': {
      // Completing a two-step link: drop its pending source once linked.
      const { pendingSource } = dialog;
      return (
        <DropLinkModal
          projectId={dialog.projectId}
          fromId={dialog.fromId}
          toId={dialog.toId}
          options={dialog.options}
          onClose={onClose}
          onLinked={pendingSource ? () => state.commands.cancelLink(pendingSource) : undefined}
        />
      );
    }
    case 'projectReferences':
      return <ProjectReferencesModal project={dialog.project} onClose={onClose} />;
    case 'renameProject':
      return <RenameProjectModal project={dialog.project} onClose={onClose} />;
    case 'lockProject':
      return <LockProjectModal project={dialog.project} onClose={onClose} />;
    case 'checkOutModule':
      return <CheckOutModuleModal projectId={dialog.projectId} module={dialog.module} onClose={onClose} />;
    case 'removeProject':
      return (
        <RemoveProjectModal
          projectId={dialog.projectId}
          projectName={dialog.projectName}
          dataDir={dialog.dataDir}
          onClose={onClose}
        />
      );
    case 'saveLayoutAsView':
      return (
        <SaveLayoutAsViewModal
          projectId={dialog.projectId}
          layout={dialog.layout}
          source={dialog.source}
          onCreated={(view) => state.commands.setActiveView(view.id)}
          onClose={onClose}
        />
      );
    case 'editView':
      return <ViewEditorModal projectId={dialog.projectId} viewId={dialog.viewId} onClose={onClose} />;
    case 'shortcuts':
      return <KeyboardShortcutsModal opened onClose={onClose} />;
    case 'about':
      return <AboutModal opened onClose={onClose} />;
  }
}
