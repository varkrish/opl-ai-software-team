import React, { useState, useCallback } from 'react';
import {
  Card,
  CardTitle,
  CardBody,
  CardHeader,
  Grid,
  GridItem,
  Title,
  Progress,
  ProgressVariant,
  Label,
  DescriptionList,
  DescriptionListGroup,
  DescriptionListTerm,
  DescriptionListDescription,
  Flex,
  FlexItem,
  Button,
  Spinner,
  Split,
  SplitItem,
  Select,
  SelectOption,
  MenuToggle,
  SelectList,
} from '@patternfly/react-core';
import {
  CubesIcon,
  CheckCircleIcon,
  ClockIcon,
  ExclamationTriangleIcon,
  FolderOpenIcon,
} from '@patternfly/react-icons';
import { useNavigate, Link } from 'react-router-dom';
import { usePolling } from '../hooks/usePolling';
import { getStats, getJobs, getHealth, getJobProgress } from '../api/client';
import type { Stats, JobSummary, HealthCheck, ProgressMessage } from '../types';

const jobStatusColor = (status: string): 'green' | 'red' | 'blue' | 'orange' | 'grey' => {
  switch (status) {
    case 'running': return 'blue';
    case 'completed': return 'green';
    case 'failed':
    case 'quota_exhausted': return 'red';
    case 'cancelled': return 'orange';
    default: return 'grey';
  }
};

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const [stats, setStats] = useState<Stats | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [health, setHealth] = useState<HealthCheck | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [jobSelectOpen, setJobSelectOpen] = useState(false);
  const [activeJob, setActiveJob] = useState<{
    progress: number;
    current_phase: string;
    last_message: ProgressMessage[];
  } | null>(null);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    try {
      const [s, j, h] = await Promise.all([getStats(), getJobs(), getHealth()]);
      setStats(s);
      setJobs(j);
      setHealth(h);

      // Auto-select job if none selected
      if (!selectedJobId || !j.find(job => job.id === selectedJobId)) {
        const runningJob = j.find((job) => job.status === 'running');
        const best = runningJob || j[0];
        if (best) setSelectedJobId(best.id);
      }

      // Fetch progress for selected job
      if (selectedJobId) {
        const progress = await getJobProgress(selectedJobId);
        setActiveJob(progress);
      } else {
        setActiveJob(null);
      }
    } catch (err) {
      console.error('Error loading dashboard data:', err);
    } finally {
      setLoading(false);
    }
  }, [selectedJobId]);

  usePolling(loadData, 2000);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '4rem' }}>
        <Spinner aria-label="Loading dashboard" />
      </div>
    );
  }

  const statusColor = (s: string): 'green' | 'red' | 'blue' | 'grey' => {
    switch (s) {
      case 'healthy':
      case 'ready':
        return 'green';
      case 'unhealthy':
      case 'not_ready':
        return 'red';
      default:
        return 'grey';
    }
  };

  return (
    <>
      {/* Header */}
      <Split hasGutter style={{ marginBottom: '1.5rem' }}>
        <SplitItem isFilled>
          <Title headingLevel="h1" size="2xl" style={{ fontFamily: '"Red Hat Display", sans-serif' }}>
            Mission Control
          </Title>
          <p style={{ color: '#6A6E73', marginTop: '0.25rem' }}>Overview of your AI development crew.</p>
        </SplitItem>
        <SplitItem>
          {jobs.length > 0 && (
            <Select
              isOpen={jobSelectOpen}
              selected={selectedJobId}
              onSelect={(_event, value) => {
                setSelectedJobId(value as string);
                setJobSelectOpen(false);
              }}
              onOpenChange={(isOpen) => setJobSelectOpen(isOpen)}
              toggle={(toggleRef) => (
                <MenuToggle
                  ref={toggleRef}
                  onClick={() => setJobSelectOpen(!jobSelectOpen)}
                  isExpanded={jobSelectOpen}
                  style={{ minWidth: '220px', marginRight: '0.5rem' }}
                >
                  {selectedJobId
                    ? (() => {
                        const j = jobs.find(job => job.id === selectedJobId);
                        return j ? `${j.vision.substring(0, 25)}${j.vision.length > 25 ? '...' : ''}` : 'Select Job';
                      })()
                    : 'Select Job'}
                </MenuToggle>
              )}
            >
              <SelectList>
                {jobs.map((job) => (
                  <SelectOption key={job.id} value={job.id}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                      <span style={{ fontSize: '0.85rem' }}>{job.vision.substring(0, 35)}{job.vision.length > 35 ? '...' : ''}</span>
                      <Label isCompact color={jobStatusColor(job.status)}>
                        {job.status}
                      </Label>
                    </div>
                  </SelectOption>
                ))}
              </SelectList>
            </Select>
          )}
          <Button variant="primary" onClick={() => window.location.href = '/'}>New Project</Button>
        </SplitItem>
      </Split>

      {/* Stats Grid */}
      <Grid hasGutter lg={3} md={6} sm={12}>
        <GridItem>
          <Card isFullHeight>
            <CardHeader>
              <Flex justifyContent={{ default: 'justifyContentSpaceBetween' }} alignItems={{ default: 'alignItemsCenter' }}>
                <FlexItem><CardTitle>Total Jobs</CardTitle></FlexItem>
                <FlexItem><CubesIcon color="#6A6E73" /></FlexItem>
              </Flex>
            </CardHeader>
            <CardBody>
              <Title headingLevel="h2" size="2xl">{stats?.total_jobs ?? 0}</Title>
              <p style={{ fontSize: '0.75rem', color: '#6A6E73' }}>
                {stats?.queued ?? 0} queued
              </p>
            </CardBody>
          </Card>
        </GridItem>
        <GridItem>
          <Card isFullHeight>
            <CardHeader>
              <Flex justifyContent={{ default: 'justifyContentSpaceBetween' }} alignItems={{ default: 'alignItemsCenter' }}>
                <FlexItem><CardTitle>Running</CardTitle></FlexItem>
                <FlexItem><ClockIcon color="#6A6E73" /></FlexItem>
              </Flex>
            </CardHeader>
            <CardBody>
              <Title headingLevel="h2" size="2xl" style={{ color: '#0066CC' }}>
                {stats?.running ?? 0}
              </Title>
              <p style={{ fontSize: '0.75rem', color: '#6A6E73' }}>Active builds</p>
            </CardBody>
          </Card>
        </GridItem>
        <GridItem>
          <Card isFullHeight>
            <CardHeader>
              <Flex justifyContent={{ default: 'justifyContentSpaceBetween' }} alignItems={{ default: 'alignItemsCenter' }}>
                <FlexItem><CardTitle>Completed</CardTitle></FlexItem>
                <FlexItem><CheckCircleIcon color="#6A6E73" /></FlexItem>
              </Flex>
            </CardHeader>
            <CardBody>
              <Title headingLevel="h2" size="2xl" style={{ color: '#3E8635' }}>
                {stats?.completed ?? 0}
              </Title>
              <p style={{ fontSize: '0.75rem', color: '#6A6E73' }}>Successfully built</p>
            </CardBody>
          </Card>
        </GridItem>
        <GridItem>
          <Card isFullHeight>
            <CardHeader>
              <Flex justifyContent={{ default: 'justifyContentSpaceBetween' }} alignItems={{ default: 'alignItemsCenter' }}>
                <FlexItem><CardTitle>Failed</CardTitle></FlexItem>
                <FlexItem><ExclamationTriangleIcon color="#6A6E73" /></FlexItem>
              </Flex>
            </CardHeader>
            <CardBody>
              <Title headingLevel="h2" size="2xl" style={{ color: '#C9190B' }}>
                {stats?.failed ?? 0}
              </Title>
              <p style={{ fontSize: '0.75rem', color: '#6A6E73' }}>
                {stats?.quota_exhausted ?? 0} quota exhausted
              </p>
            </CardBody>
          </Card>
        </GridItem>
      </Grid>

      <Grid hasGutter style={{ marginTop: '1.5rem' }}>
        {/* Activity Feed */}
        <GridItem lg={8} md={12}>
          <Card isFullHeight>
            <CardHeader>
              <CardTitle>Crew Activity</CardTitle>
              <p style={{ fontSize: '0.75rem', color: '#6A6E73' }}>Real-time actions from your AI agents.</p>
            </CardHeader>
            <CardBody>
              {activeJob && activeJob.last_message.length > 0 ? (
                <div>
                  {activeJob.last_message.slice(-8).reverse().map((msg, i) => (
                    <Flex key={i} gap={{ default: 'gapMd' }} alignItems={{ default: 'alignItemsFlexStart' }} style={{ marginBottom: '1rem' }}>
                      <FlexItem>
                        <span
                          style={{
                            display: 'inline-block',
                            width: 8,
                            height: 8,
                            borderRadius: '50%',
                            backgroundColor: '#3E8635',
                            marginTop: 6,
                          }}
                        />
                      </FlexItem>
                      <FlexItem>
                        <div>
                          <span style={{ fontWeight: 600, color: '#0066CC', marginRight: 6 }}>
                            {msg.phase}
                          </span>
                          <span style={{ fontSize: '0.875rem' }}>{msg.message}</span>
                        </div>
                        <div style={{ fontSize: '0.75rem', color: '#6A6E73' }}>
                          {new Date(msg.timestamp).toLocaleTimeString()}
                        </div>
                      </FlexItem>
                    </Flex>
                  ))}
                </div>
              ) : (
                <p style={{ color: '#6A6E73', textAlign: 'center', padding: '2rem' }}>
                  No active job running. Create a new project to see activity.
                </p>
              )}
            </CardBody>
          </Card>
        </GridItem>

        {/* Sidebar Info */}
        <GridItem lg={4} md={12}>
          {/* Current Phase */}
          <Card style={{ marginBottom: '1.5rem' }}>
            <CardHeader>
              <CardTitle>Current Phase</CardTitle>
            </CardHeader>
            <CardBody>
              {activeJob ? (
                <>
                  <Flex justifyContent={{ default: 'justifyContentSpaceBetween' }} style={{ marginBottom: '0.5rem' }}>
                    <FlexItem>
                      <strong style={{ textTransform: 'capitalize' }}>{activeJob.current_phase}</strong>
                    </FlexItem>
                    <FlexItem>
                      <span style={{ color: '#6A6E73' }}>{activeJob.progress}%</span>
                    </FlexItem>
                  </Flex>
                  <Progress
                    value={activeJob.progress}
                    variant={activeJob.progress === 100 ? ProgressVariant.success : undefined}
                    aria-label="Job progress"
                  />
                </>
              ) : (
                <p style={{ color: '#6A6E73' }}>No active job</p>
              )}
            </CardBody>
          </Card>

          {/* System Status */}
          <Card>
            <CardHeader>
              <CardTitle>System Status</CardTitle>
            </CardHeader>
            <CardBody>
              <DescriptionList isHorizontal>
                <DescriptionListGroup>
                  <DescriptionListTerm>
                    <Flex alignItems={{ default: 'alignItemsCenter' }} gap={{ default: 'gapSm' }}>
                      <FlexItem>
                        <span
                          style={{
                            display: 'inline-block',
                            width: 8,
                            height: 8,
                            borderRadius: '50%',
                            backgroundColor: health?.status === 'healthy' ? '#3E8635' : '#C9190B',
                          }}
                        />
                      </FlexItem>
                      <FlexItem>API Server</FlexItem>
                    </Flex>
                  </DescriptionListTerm>
                  <DescriptionListDescription>
                    <Label color={statusColor(health?.status ?? 'unknown')}>
                      {health?.status ?? 'Unknown'}
                    </Label>
                  </DescriptionListDescription>
                </DescriptionListGroup>
                <DescriptionListGroup>
                  <DescriptionListTerm>
                    <Flex alignItems={{ default: 'alignItemsCenter' }} gap={{ default: 'gapSm' }}>
                      <FlexItem>
                        <span
                          style={{
                            display: 'inline-block',
                            width: 8,
                            height: 8,
                            borderRadius: '50%',
                            backgroundColor: '#3E8635',
                          }}
                        />
                      </FlexItem>
                      <FlexItem>Crew Studio</FlexItem>
                    </Flex>
                  </DescriptionListTerm>
                  <DescriptionListDescription>
                    <Label color="green">Running</Label>
                  </DescriptionListDescription>
                </DescriptionListGroup>
              </DescriptionList>
            </CardBody>
          </Card>
        </GridItem>
      </Grid>

      {/* Recent Jobs */}
      {jobs.length > 0 && (
        <Card style={{ marginTop: '1.5rem' }}>
          <CardHeader>
            <CardTitle>Recent Jobs</CardTitle>
          </CardHeader>
          <CardBody style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #E8E8E8', textAlign: 'left' }}>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 600, color: '#6A6E73' }}>Vision</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 600, color: '#6A6E73', width: 100 }}>Status</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 600, color: '#6A6E73', width: 120 }}>Phase</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 600, color: '#6A6E73', width: 80 }}>Progress</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 600, color: '#6A6E73', width: 160 }}>Created</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 600, color: '#6A6E73', width: 100 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.slice(0, 10).map((job) => (
                  <tr
                    key={job.id}
                    style={{
                      borderBottom: '1px solid #F0F0F0',
                      backgroundColor: job.id === selectedJobId ? '#F0F4FF' : 'transparent',
                    }}
                  >
                    <td
                      onClick={() => setSelectedJobId(job.id)}
                      style={{
                        padding: '0.625rem 1rem',
                        maxWidth: 300,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        cursor: 'pointer',
                      }}
                    >
                      {job.vision}
                    </td>
                    <td onClick={() => setSelectedJobId(job.id)} style={{ padding: '0.625rem 1rem', cursor: 'pointer' }}>
                      <Label isCompact color={jobStatusColor(job.status)}>
                        {job.status === 'quota_exhausted' ? 'quota' : job.status}
                      </Label>
                    </td>
                    <td onClick={() => setSelectedJobId(job.id)} style={{ padding: '0.625rem 1rem', color: '#6A6E73', textTransform: 'capitalize', cursor: 'pointer' }}>
                      {(job.current_phase || 'N/A').replace(/_/g, ' ')}
                    </td>
                    <td onClick={() => setSelectedJobId(job.id)} style={{ padding: '0.625rem 1rem', cursor: 'pointer' }}>
                      <Progress
                        value={job.progress}
                        size="sm"
                        title=""
                        aria-label={`${job.progress}%`}
                        variant={job.status === 'failed' ? ProgressVariant.danger : job.status === 'completed' ? ProgressVariant.success : undefined}
                      />
                    </td>
                    <td onClick={() => setSelectedJobId(job.id)} style={{ padding: '0.625rem 1rem', color: '#6A6E73', fontSize: '0.8rem', cursor: 'pointer' }}>
                      {new Date(job.created_at).toLocaleString()}
                    </td>
                    <td style={{ padding: '0.625rem 1rem' }}>
                      <Link
                        to={`/files?job=${job.id}`}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '0.35rem',
                          fontSize: '0.875rem',
                          color: 'var(--pf-v5-global--link--Color, #0066cc)',
                          textDecoration: 'underline',
                        }}
                      >
                        <FolderOpenIcon />
                        {job.status === 'completed' ? 'View files & refine' : 'View files'}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}
    </>
  );
};

export default Dashboard;
