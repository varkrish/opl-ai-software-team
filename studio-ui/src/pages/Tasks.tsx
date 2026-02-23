import React, { useState, useCallback } from 'react';
import {
  Card,
  CardBody,
  Title,
  Label,
  Button,
  Modal,
  ModalVariant,
  Form,
  FormGroup,
  TextArea,
  Spinner,
  Progress,
  ProgressMeasureLocation,
  ProgressVariant,
} from '@patternfly/react-core';
import {
  PlusCircleIcon,
  CubesIcon,
  PencilAltIcon,
  PaletteIcon,
  CogIcon,
  CodeIcon,
  DesktopIcon,
  CheckCircleIcon,
  OutlinedClockIcon,
  InProgressIcon,
  ExclamationCircleIcon,
} from '@patternfly/react-icons';
import { usePolling } from '../hooks/usePolling';
import { getJobs, getJobTasks, createJob } from '../api/client';
import { groupTasksIntoColumns } from '../utils/taskGrouping';
import type { JobSummary, KanbanColumn, Task } from '../types';
import JobSearchSelect from '../components/JobSearchSelect';

/* ── Phase visual config ── */
const agentIcons: Record<string, React.ReactNode> = {
  'Meta Agent': <CubesIcon />,
  'Product Owner': <PencilAltIcon />,
  'Designer': <PaletteIcon />,
  'Tech Architect': <CogIcon />,
  'Dev Crew': <CodeIcon />,
  'Frontend Crew': <DesktopIcon />,
};

const agentColors: Record<string, string> = {
  'Meta Agent': '#6753AC',
  'Product Owner': '#0066CC',
  'Designer': '#EC7A08',
  'Tech Architect': '#F0AB00',
  'Dev Crew': '#3E8635',
  'Frontend Crew': '#009596',
};

const columnMeta: Record<string, { color: string; headerBg: string; icon: React.ReactNode }> = {
  todo:          { color: '#6A6E73', headerBg: '#F0F0F0', icon: <OutlinedClockIcon /> },
  'in-progress': { color: '#F0AB00', headerBg: '#FFF4E0', icon: <InProgressIcon /> },
  completed:     { color: '#3E8635', headerBg: '#E9F5E6', icon: <CheckCircleIcon /> },
  failed:        { color: '#C9190B', headerBg: '#FFF5F5', icon: <ExclamationCircleIcon /> },
};

const Tasks: React.FC = () => {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [columns, setColumns] = useState<KanbanColumn[]>([]);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [vision, setVision] = useState('');
  const [creating, setCreating] = useState(false);
  const loadData = useCallback(async () => {
    try {
      const res = await getJobs(1, 100);
      setJobs(res.jobs);

      let jobId = selectedJobId;
      if (!jobId || !res.jobs.find((job) => job.id === jobId)) {
        const running = res.jobs.find((job) => job.status === 'running');
        jobId = running?.id || res.jobs[0]?.id || null;
        setSelectedJobId(jobId);
      }

      if (jobId) {
        const { tasks } = await getJobTasks(jobId);
        setColumns(groupTasksIntoColumns(tasks));
      } else {
        setColumns([]);
      }
    } catch {
      // API not available
    } finally {
      setLoading(false);
    }
  }, [selectedJobId]);

  usePolling(loadData, 2000);

  const handleCreateJob = async () => {
    if (!vision.trim()) return;
    setCreating(true);
    try {
      await createJob(vision.trim());
      setVision('');
      setIsModalOpen(false);
      await loadData();
    } catch (err) {
      console.error('Error creating job:', err);
    } finally {
      setCreating(false);
    }
  };

  const handleJobSelect = (jobId: string) => {
    setSelectedJobId(jobId);
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '4rem' }}>
        <Spinner aria-label="Loading tasks" />
      </div>
    );
  }

  const selectedJob = jobs.find((j) => j.id === selectedJobId);

  return (
    <>
      {/* ── Header ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div>
          <Title headingLevel="h1" size="2xl" style={{ fontFamily: '"Red Hat Display", sans-serif' }}>
            Task Board
          </Title>
          <p style={{ color: '#6A6E73', marginTop: '0.25rem' }}>Kanban view of agent activities by phase.</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <JobSearchSelect
            selectedJobId={selectedJobId}
            onSelect={handleJobSelect}
          />
          <Button variant="primary" icon={<PlusCircleIcon />} onClick={() => setIsModalOpen(true)}>
            New Job
          </Button>
        </div>
      </div>

      {/* ── Kanban Board ── */}
      {columns.length > 0 ? (
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'stretch' }}>
          {columns.map((col) => {
            const meta = columnMeta[col.id] || columnMeta.todo;
            return (
              <div key={col.id} style={{ flex: '1 1 0', minWidth: 0 }}>
                {/* Column header */}
                <div
                  style={{
                    backgroundColor: meta.headerBg,
                    borderRadius: '8px 8px 0 0',
                    padding: '0.75rem 1rem',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    borderBottom: `3px solid ${meta.color}`,
                  }}
                >
                  <span style={{ color: meta.color, fontSize: '1rem' }}>{meta.icon}</span>
                  <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{col.title}</span>
                  <Label isCompact variant="outline" style={{ marginLeft: 'auto' }}>{col.tasks.length}</Label>
                </div>

                {/* Column body */}
                <div
                  style={{
                    backgroundColor: '#FAFAFA',
                    borderRadius: '0 0 8px 8px',
                    padding: '0.75rem',
                    minHeight: 350,
                    maxHeight: 600,
                    overflowY: 'auto',
                  }}
                >
                  {col.tasks.length === 0 ? (
                    <p style={{ textAlign: 'center', color: '#6A6E73', padding: '2rem', fontSize: '0.85rem' }}>
                      No tasks
                    </p>
                  ) : (
                    col.tasks.map((task: Task) => {
                      const color = agentColors[task.agent] || '#6A6E73';
                      return (
                        <div
                          key={task.task_id}
                          style={{
                            backgroundColor: '#fff',
                            borderRadius: 8,
                            borderLeft: `4px solid ${color}`,
                            padding: '0.75rem 1rem',
                            marginBottom: '0.75rem',
                            boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
                            opacity: task.status === 'pending' ? 0.65 : 1,
                          }}
                        >
                          {/* Agent name + icon row */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                            <span style={{ color, fontSize: '1rem' }}>{agentIcons[task.agent]}</span>
                            <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{task.agent}</span>
                          </div>

                          {/* Description */}
                          <div style={{ fontSize: '0.8rem', color: '#4F5255', marginBottom: 8 }}>
                            {task.description}
                          </div>

                          {/* Progress bar for in-progress tasks */}
                          {task.status === 'in_progress' && (
                            <div style={{ marginBottom: 4 }}>
                              <Progress
                                value={task.progress}
                                measureLocation={ProgressMeasureLocation.inside}
                                variant={task.progress >= 100 ? ProgressVariant.success : undefined}
                                aria-label={`${task.agent} progress`}
                                style={{ '--pf-v5-c-progress__bar--before--BackgroundColor': color } as React.CSSProperties}
                              />
                              {task.subtasks_total > 0 && (
                                <div style={{ fontSize: '0.7rem', color: '#6A6E73', marginTop: 4 }}>
                                  {task.subtasks_completed} / {task.subtasks_total} subtasks done
                                </div>
                              )}
                            </div>
                          )}

                          {/* Completed badge */}
                          {task.status === 'completed' && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.7rem', color: '#3E8635' }}>
                              <CheckCircleIcon />
                              {task.subtasks_total > 0
                                ? <span>{task.subtasks_completed} / {task.subtasks_total} subtasks</span>
                                : <span>Phase complete</span>}
                            </div>
                          )}

                          {/* Failed indicator */}
                          {task.status === 'failed' && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.7rem', color: '#C9190B' }}>
                              <ExclamationCircleIcon />
                              <span>Phase failed</span>
                            </div>
                          )}

                          {/* Pending label */}
                          {task.status === 'pending' && (
                            <div style={{ fontSize: '0.7rem', color: '#6A6E73' }}>
                              Waiting for earlier phases...
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <Card>
          <CardBody>
            <p style={{ textAlign: 'center', color: '#6A6E73', padding: '3rem' }}>
              No jobs found. Create a new job to see tasks here.
            </p>
          </CardBody>
        </Card>
      )}

      {/* ── New Job Modal ── */}
      <Modal
        variant={ModalVariant.medium}
        title="Create New Build Job"
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        actions={[
          <Button
            key="create"
            variant="primary"
            onClick={handleCreateJob}
            isLoading={creating}
            isDisabled={!vision.trim() || creating}
          >
            Start Build Job
          </Button>,
          <Button key="cancel" variant="link" onClick={() => setIsModalOpen(false)}>
            Cancel
          </Button>,
        ]}
      >
        <Form>
          <FormGroup label="Project Vision / Idea" isRequired fieldId="vision">
            <TextArea
              id="vision"
              value={vision}
              onChange={(_event, value) => setVision(value)}
              placeholder="Describe your project vision..."
              rows={6}
              isRequired
              aria-label="Project vision"
            />
          </FormGroup>
        </Form>
      </Modal>
    </>
  );
};

export default Tasks;
