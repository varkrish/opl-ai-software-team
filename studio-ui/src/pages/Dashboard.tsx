import React, { useState, useCallback, useRef } from 'react';
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
  Dropdown,
  DropdownItem,
  DropdownList,
  Pagination,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
  SearchInput,
  MenuToggle,
  Select,
  SelectOption,
  SelectList,
} from '@patternfly/react-core';
import {
  CubesIcon,
  CheckCircleIcon,
  ClockIcon,
  ExclamationTriangleIcon,
  EllipsisVIcon,
  SortAmountDownIcon,
  SortAmountUpIcon,
} from '@patternfly/react-icons';
import { useNavigate } from 'react-router-dom';
import { usePolling } from '../hooks/usePolling';
import { getStats, getJobs, getHealth, getJobProgress, restartJob } from '../api/client';
import JobSearchSelect from '../components/JobSearchSelect';
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

const DEFAULT_PER_PAGE = 10;
const PER_PAGE_OPTIONS = [
  { value: 10, title: '10' },
  { value: 20, title: '20' },
  { value: 50, title: '50' },
];

const STATUS_OPTIONS = ['running', 'completed', 'failed', 'queued', 'cancelled', 'quota_exhausted'];
type SortCol = 'vision' | 'status' | 'current_phase' | 'progress' | 'created_at';

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const [stats, setStats] = useState<Stats | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobsTotal, setJobsTotal] = useState(0);
  const [jobsPage, setJobsPage] = useState(1);
  const [jobsPerPage, setJobsPerPage] = useState(DEFAULT_PER_PAGE);
  const [health, setHealth] = useState<HealthCheck | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [actionsOpenJobId, setActionsOpenJobId] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<{
    progress: number;
    current_phase: string;
    last_message: ProgressMessage[];
  } | null>(null);
  const [loading, setLoading] = useState(true);

  const [searchText, setSearchText] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [statusFilterOpen, setStatusFilterOpen] = useState(false);
  const [sortBy, setSortBy] = useState<SortCol>('created_at');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout>>();

  const loadData = useCallback(async (overrides?: {
    page?: number; perPage?: number; search?: string;
    status?: string; sort?: SortCol; order?: 'asc' | 'desc';
  }) => {
    const page = overrides?.page ?? jobsPage;
    const perPage = overrides?.perPage ?? jobsPerPage;
    const search = overrides?.search ?? searchText;
    const status = overrides?.status ?? statusFilter;
    const sort = overrides?.sort ?? sortBy;
    const order = overrides?.order ?? sortOrder;
    try {
      const [s, jobsResp, h] = await Promise.all([
        getStats(),
        getJobs(page, perPage, search || undefined, {
          status: status || undefined,
          sortBy: sort,
          sortOrder: order,
        }),
        getHealth(),
      ]);
      setStats(s);
      setJobs(jobsResp.jobs);
      setJobsTotal(jobsResp.total);
      setHealth(h);

      const j = jobsResp.jobs;
      if (!selectedJobId || !j.find((job) => job.id === selectedJobId)) {
        const runningJob = j.find((job) => job.status === 'running');
        const best = runningJob || j[0];
        if (best) setSelectedJobId(best.id);
      }

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
  }, [selectedJobId, jobsPage, jobsPerPage, searchText, statusFilter, sortBy, sortOrder]);

  usePolling(loadData, 2000);

  const handleSort = (col: SortCol) => {
    const newOrder = sortBy === col && sortOrder === 'asc' ? 'desc' : 'asc';
    setSortBy(col);
    setSortOrder(newOrder);
    setJobsPage(1);
    loadData({ page: 1, sort: col, order: newOrder });
  };

  const handleSearch = (value: string) => {
    setSearchText(value);
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    searchDebounceRef.current = setTimeout(() => {
      setJobsPage(1);
      loadData({ page: 1, search: value });
    }, 300);
  };

  const handleStatusFilter = (value: string) => {
    setStatusFilter(value);
    setStatusFilterOpen(false);
    setJobsPage(1);
    loadData({ page: 1, status: value });
  };

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
          <Flex alignItems={{ default: 'alignItemsCenter' }} gap={{ default: 'gapSm' }}>
            <FlexItem>
              <JobSearchSelect
                selectedJobId={selectedJobId}
                onSelect={setSelectedJobId}
                style={{ minWidth: 240 }}
              />
            </FlexItem>
            <FlexItem>
              <Button variant="primary" onClick={() => window.location.href = '/'}>New Project</Button>
            </FlexItem>
          </Flex>
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
      {(jobs.length > 0 || jobsTotal > 0) && (
        <Card style={{ marginTop: '1.5rem' }}>
          <CardHeader>
            <CardTitle>Recent Jobs</CardTitle>
            {jobsTotal > 0 && (
              <p style={{ fontSize: '0.75rem', color: '#6A6E73', marginTop: '0.25rem' }}>
                {jobsTotal} job{jobsTotal !== 1 ? 's' : ''} total
              </p>
            )}
          </CardHeader>
          <CardBody style={{ padding: 0 }}>
            <Toolbar style={{ padding: '0.5rem 1rem', borderBottom: '1px solid #E8E8E8' }}>
              <ToolbarContent>
                <ToolbarItem>
                  <SearchInput
                    placeholder="Search by vision..."
                    value={searchText}
                    onChange={(_e, value) => handleSearch(value)}
                    onClear={() => handleSearch('')}
                    style={{ minWidth: 220 }}
                  />
                </ToolbarItem>
                <ToolbarItem>
                  <Select
                    isOpen={statusFilterOpen}
                    selected={statusFilter || undefined}
                    onSelect={(_e, value) => handleStatusFilter(value as string)}
                    onOpenChange={setStatusFilterOpen}
                    toggle={(ref) => (
                      <MenuToggle
                        ref={ref}
                        onClick={() => setStatusFilterOpen(!statusFilterOpen)}
                        isExpanded={statusFilterOpen}
                        style={{ minWidth: 140 }}
                      >
                        {statusFilter || 'All statuses'}
                      </MenuToggle>
                    )}
                  >
                    <SelectList>
                      <SelectOption value="">All statuses</SelectOption>
                      {STATUS_OPTIONS.map((s) => (
                        <SelectOption key={s} value={s}>
                          <Label isCompact color={jobStatusColor(s)}>{s}</Label>
                        </SelectOption>
                      ))}
                    </SelectList>
                  </Select>
                </ToolbarItem>
              </ToolbarContent>
            </Toolbar>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem', tableLayout: 'fixed' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #E8E8E8', textAlign: 'left' }}>
                  {([
                    ['Vision', 'vision', '35%'],
                    ['Status', 'status', '9%'],
                    ['Phase', 'current_phase', '11%'],
                    ['Progress', 'progress', '14%'],
                    ['Created', 'created_at', '16%'],
                  ] as [string, SortCol, string][]).map(([label, col, width]) => (
                    <th
                      key={col}
                      onClick={() => handleSort(col)}
                      style={{
                        padding: '0.75rem 1rem', fontWeight: 600, color: '#6A6E73',
                        width, cursor: 'pointer', userSelect: 'none',
                      }}
                    >
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        {label}
                        {sortBy === col && (
                          sortOrder === 'asc' ? <SortAmountUpIcon style={{ fontSize: '0.7rem' }} /> : <SortAmountDownIcon style={{ fontSize: '0.7rem' }} />
                        )}
                      </span>
                    </th>
                  ))}
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 600, color: '#6A6E73', width: '5%' }}></th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
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
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        cursor: 'pointer',
                      }}
                    >
                      {job.vision.startsWith('[MTA') ? (
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', overflow: 'hidden' }}>
                          <Label isCompact color="blue" style={{ flexShrink: 0 }}>MTA</Label>
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {job.vision.replace(/^\[MTA[^\]]*\]\s*/, '') || job.vision}
                          </span>
                        </span>
                      ) : job.vision.startsWith('[Refactor') ? (
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', overflow: 'hidden' }}>
                          <Label isCompact color="cyan" style={{ flexShrink: 0 }}>Refactor</Label>
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {job.vision.replace(/^\[Refactor[^\]]*\]\s*/, '') || job.vision}
                          </span>
                        </span>
                      ) : job.vision}
                    </td>
                    <td onClick={() => setSelectedJobId(job.id)} style={{ padding: '0.625rem 1rem', cursor: 'pointer' }}>
                      <Label isCompact color={jobStatusColor(job.status)}>
                        {job.status === 'quota_exhausted' ? 'quota' : job.status}
                      </Label>
                    </td>
                    <td onClick={() => setSelectedJobId(job.id)} style={{ padding: '0.625rem 1rem', color: '#6A6E73', textTransform: 'capitalize', cursor: 'pointer' }}>
                      {(job.current_phase || 'N/A').replace(/_/g, ' ')}
                    </td>
                    <td onClick={() => setSelectedJobId(job.id)} style={{ padding: '0.625rem 1rem', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <Progress
                            value={job.progress}
                            size="sm"
                            title=""
                            measureLocation="none"
                            aria-label={`${job.progress}%`}
                            variant={job.status === 'failed' ? ProgressVariant.danger : job.status === 'completed' ? ProgressVariant.success : undefined}
                          />
                        </div>
                        <span style={{ fontSize: '0.75rem', color: '#6A6E73', flexShrink: 0 }}>
                          {job.progress}%
                        </span>
                      </div>
                    </td>
                    <td onClick={() => setSelectedJobId(job.id)} style={{ padding: '0.625rem 1rem', color: '#6A6E73', fontSize: '0.8rem', cursor: 'pointer' }}>
                      {new Date(job.created_at).toLocaleString()}
                    </td>
                    <td style={{ padding: '0.625rem 1rem' }}>
                      <Dropdown
                        isOpen={actionsOpenJobId === job.id}
                        onSelect={() => setActionsOpenJobId(null)}
                        onOpenChange={(isOpen) => { if (!isOpen) setActionsOpenJobId(null); }}
                        toggle={(toggleRef) => (
                          <MenuToggle
                            ref={toggleRef}
                            variant="plain"
                            onClick={() => setActionsOpenJobId(actionsOpenJobId === job.id ? null : job.id)}
                            isExpanded={actionsOpenJobId === job.id}
                            style={{ padding: '0.25rem' }}
                          >
                            <EllipsisVIcon />
                          </MenuToggle>
                        )}
                        popperProps={{ position: 'right' }}
                      >
                        <DropdownList>
                          <DropdownItem
                            key="files"
                            onClick={() => navigate(
                              job.vision.startsWith('[MTA') ? `/migration/${job.id}` :
                                job.vision.startsWith('[Refactor') ? `/refactor/${job.id}` :
                                  `/files?job=${job.id}`
                            )}
                          >
                            {job.vision.startsWith('[MTA') ? 'View migration' :
                              job.vision.startsWith('[Refactor') ? 'View refactor' :
                                'View files'}
                          </DropdownItem>
                          {['failed', 'cancelled', 'quota_exhausted', 'completed'].includes(job.status) && (
                            <DropdownItem
                              key="restart"
                              onClick={async () => {
                                try {
                                  await restartJob(job.id);
                                  window.location.reload();
                                } catch (err) {
                                  console.error('Restart failed:', err);
                                }
                              }}
                            >
                              Restart job
                            </DropdownItem>
                          )}
                          {job.vision.startsWith('[MTA') && job.status === 'failed' && (
                            <DropdownItem
                              key="retry-failed"
                              onClick={async () => {
                                try {
                                  await restartJob(job.id);
                                  window.location.reload();
                                } catch (err) {
                                  console.error('Retry failed tasks failed:', err);
                                }
                              }}
                              style={{ color: '#C9190B' }}
                            >
                              Retry failed tasks
                            </DropdownItem>
                          )}
                        </DropdownList>
                      </Dropdown>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {jobsTotal > 0 && (
              <Pagination
                itemCount={jobsTotal}
                page={jobsPage}
                perPage={jobsPerPage}
                perPageOptions={PER_PAGE_OPTIONS}
                onSetPage={(_e, newPage) => {
                  setJobsPage(newPage);
                  loadData({ page: newPage });
                }}
                onPerPageSelect={(_e, newPerPage, newPage) => {
                  setJobsPerPage(newPerPage);
                  setJobsPage(newPage);
                  loadData({ page: newPage, perPage: newPerPage });
                }}
                variant="bottom"
                widgetId="dashboard-jobs-pagination"
              />
            )}
          </CardBody>
        </Card>
      )}
    </>
  );
};

export default Dashboard;
