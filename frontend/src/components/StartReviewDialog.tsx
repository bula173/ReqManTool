import { useGitProvider } from '../api/queries';
import { StartReviewModal } from './StartReviewModal';

/** “Request review” with the provider of the project's remote (names it merge or pull request). */
export function StartReviewDialog({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const providerQuery = useGitProvider(projectId);
  return (
    <StartReviewModal
      projectId={projectId}
      provider={providerQuery.data?.connection?.provider ?? null}
      onClose={onClose}
    />
  );
}
