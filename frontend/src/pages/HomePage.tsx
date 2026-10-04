import { Alert, Button, Center, Loader, Stack, Text, Title } from '@mantine/core';
import { IconFolderPlus, IconInfoCircle } from '@tabler/icons-react';
import { useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useProjects } from '../api/queries';
import { NewProjectModal } from '../components/NewProjectModal';

export function HomePage() {
  const projectsQuery = useProjects();
  const [newProjectOpened, setNewProjectOpened] = useState(false);

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

  if (projectsQuery.data && projectsQuery.data.length > 0) {
    return <Navigate to={`/projects/${projectsQuery.data[0].id}`} replace />;
  }

  return (
    <Center h="60vh">
      <Stack align="center" gap="sm">
        <Title order={2}>No projects yet</Title>
        <Text c="dimmed">Create your first project to start tracking requirements.</Text>
        <Button leftSection={<IconFolderPlus size={16} />} onClick={() => setNewProjectOpened(true)}>
          Create your first project
        </Button>
      </Stack>
      <NewProjectModal opened={newProjectOpened} onClose={() => setNewProjectOpened(false)} />
    </Center>
  );
}
