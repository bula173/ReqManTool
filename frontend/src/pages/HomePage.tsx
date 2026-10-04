import { Alert, Button, Center, Loader, Stack, Text, Title } from '@mantine/core';
import { IconFolderPlus, IconInfoCircle } from '@tabler/icons-react';
import { Navigate } from 'react-router-dom';
import { useAppActions } from '../actions/context';
import { useProjects } from '../api/queries';
import { isProjectStub } from '../api/types';

export function HomePage() {
  const projectsQuery = useProjects();
  const { run } = useAppActions();

  if (projectsQuery.isLoading) {
    return (
      <Center h="60vh">
        <Loader />
      </Center>
    );
  }

  if (projectsQuery.isError) {
    return (
      <Alert icon={<IconInfoCircle size={16} />} color="red" title="Failed to load projects">
        {projectsQuery.error instanceof Error ? projectsQuery.error.message : 'Unknown error'}
      </Alert>
    );
  }

  const projects = projectsQuery.data ?? [];
  // A stub (a project that cannot be loaded) cannot be opened: start on the first real project.
  const firstOpenable = projects.find((p) => !isProjectStub(p));
  if (firstOpenable) {
    return <Navigate to={`/projects/${firstOpenable.id}`} replace />;
  }

  if (projects.length > 0) {
    return (
      <Alert icon={<IconInfoCircle size={16} />} color="orange" title="No project can be opened">
        <Stack gap="xs">
          <Text size="sm">
            Every project in this data directory failed to load. Their files were not changed.
          </Text>
          {projects.filter(isProjectStub).map((stub) => (
            <Text key={stub.id} size="sm">
              {stub.name}: {stub.error}
            </Text>
          ))}
          <Button
            leftSection={<IconFolderPlus size={16} />}
            onClick={() => run('file.newProject')}
            w="fit-content"
          >
            Create a project
          </Button>
        </Stack>
      </Alert>
    );
  }

  return (
    <Center h="60vh">
      <Stack align="center" gap="sm">
        <Title order={2}>No projects yet</Title>
        <Text c="dimmed">Create your first project to start tracking requirements.</Text>
        <Button leftSection={<IconFolderPlus size={16} />} onClick={() => run('file.newProject')}>
          Create your first project
        </Button>
      </Stack>
    </Center>
  );
}
