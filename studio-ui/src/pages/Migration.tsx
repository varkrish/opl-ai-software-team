import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  PageSection,
  Title,
  Button,
  Alert,
  Spinner,
  Label,
  ExpandableSection,
  EmptyState,
  EmptyStateIcon,
  EmptyStateBody,
  EmptyStateHeader,
  EmptyStateActions,
  Card,
  CardBody,
  CardTitle,
  Split,
  SplitItem,
  Progress,
  ProgressVariant,
} from '@patternfly/react-core';
import {
  FolderOpenIcon,
  ArrowRightIcon,
  PlusCircleIcon,
  SyncAltIcon,
} from '@patternfly/react-icons';
import {
  Table,
  Thead,
  Tbody,
  Tr,
  Th,
  Td,
} from '@patternfly/react-table';
import {
  getMigrationStatus,
  getMigrationChanges,
  getJobs,
  getJob,
  restartJob,
  MigrationIssue,
  MigrationSummary,
  MigrationChanges,
} from '../api/client';
import type { JobSummary, Job } from '../types';

const severityColor = (s: string) => {
  switch (s) {
    case 'mandatory': return 'red';
    case 'optional': return 'blue';
    case 'potential': return 'grey';
    default: return 'grey';
  }
};

const statusColor = (s: string) => {
  switch (s) {
    case 'completed': return 'green';
    case 'running': return 'blue';
    case 'failed': return 'red';
    case 'skipped': return 'grey';
    case 'pending': return 'gold';
    default: return 'grey';
  }
};

/* ═══════════════════════════════════════════════════════════════════════════ */
/* Migration Page                                                            */
/* /migration        → list migration projects                               */
/* /migration/:jobId → detail view (summary + issues table)                  */
/* ═══════════════════════════════════════════════════════════════════════════ */
const Migration: React.FC = () => {
  const { jobId } = useParams<{ jobId?: string }>();
  const navigate = useNavigate();

  // ── List view state ──────────────────────────────────────────────────
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);

  // ── Detail view state ────────────────────────────────────────────────
  const [job, setJob] = useState<Job | null>(null);
  const [issues, setIssues] = useState<MigrationIssue[]>([]);
  const [summary, setSummary] = useState<MigrationSummary | null>(null);
  const [migrating, setMigrating] = useState(false);
  const [expandedIssue, setExpandedIssue] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [fileChanges, setFileChanges] = useState<MigrationChanges | null>(null);
  const [changesExpanded, setChangesExpanded] = useState(false);
  const [retryingFailed, setRetryingFailed] = useState(false);
  const [progressAccordionExpanded, setProgressAccordionExpanded] = useState(false);

  // ── Load migration projects (list view) ──────────────────────────────
  useEffect(() => {
    if (jobId) return;
    let cancelled = false;
    setJobsLoading(true);
    getJobs()
      .then((list) => {
        if (!cancelled) {
          // Show migration projects: those with [MTA] or [MTA Migration] in vision
          const migrationJobs = list.filter(
            (j) => j.vision?.includes('[MTA]') || j.vision?.includes('[MTA Migration]')
          );
          setJobs(migrationJobs);
        }
      })
      .finally(() => {
        if (!cancelled) setJobsLoading(false);
      });
    return () => { cancelled = true; };
  }, [jobId]);

  // ── Load detail + poll (detail view) ─────────────────────────────────
  const pollStatus = useCallback(async () => {
    if (!jobId) return;
    try {
      // Fetch both job details and migration status
      const [jobData, migData] = await Promise.all([
        getJob(jobId).catch(() => null),
        getMigrationStatus(jobId).catch(() => null),
      ]);
      
      if (jobData) setJob(jobData);
      
      if (migData) {
        setIssues(migData.issues);
        setSummary(migData.summary);
        if (migData.summary.running > 0 || migData.summary.pending > 0) {
          setMigrating(true);
        } else if (migData.summary.total > 0) {
          setMigrating(false);
        }
      }
      
      // Keep polling if parsing/analyzing or if migration is active
      const currentPhase = jobData?.current_phase ?? '';
      if (currentPhase === 'parsing' || currentPhase === 'analyzing' || 
          (migData && (migData.summary.running > 0 || migData.summary.pending > 0))) {
        setMigrating(true);
      }

      // Fetch file changes when migration is complete
      if (migData && migData.summary.total > 0 &&
          migData.summary.pending === 0 && migData.summary.running === 0) {
        getMigrationChanges(jobId!).then(setFileChanges).catch(() => {});
      }
    } catch {
      // Silently fail polling
    }
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    setDetailLoading(true);
    Promise.all([
      getJob(jobId).catch(() => null),
      getMigrationStatus(jobId).catch(() => null),
    ]).then(([jobData, migData]) => {
      if (cancelled) return;
      if (jobData) setJob(jobData);
      if (migData) {
        setIssues(migData.issues);
        setSummary(migData.summary);
        if (migData.summary.running > 0 || migData.summary.pending > 0) {
          setMigrating(true);
        }
      }
      // Start polling if parsing/analyzing or if migration is active
      const currentPhase = jobData?.current_phase ?? '';
      if (currentPhase === 'parsing' || currentPhase === 'analyzing' || 
          (migData && (migData.summary.running > 0 || migData.summary.pending > 0))) {
        setMigrating(true);
      }
      // Fetch file changes if migration is complete
      if (migData && migData.summary.total > 0 &&
          migData.summary.pending === 0 && migData.summary.running === 0) {
        getMigrationChanges(jobId!).then(c => { if (!cancelled) setFileChanges(c); }).catch(() => {});
      }
    }).finally(() => {
      if (!cancelled) setDetailLoading(false);
    });
    return () => { cancelled = true; };
  }, [jobId]);

  useEffect(() => {
    if (!migrating) return;
    const interval = setInterval(pollStatus, 3000);
    return () => clearInterval(interval);
  }, [migrating, pollStatus]);

  /* ═══════════════════════════════════════════════════════════════════════ */
  /* LIST VIEW: /migration                                                  */
  /* ═══════════════════════════════════════════════════════════════════════ */
  if (!jobId) {
    return (
      <PageSection>
        <Split hasGutter style={{ marginBottom: '1.5rem' }}>
          <SplitItem isFilled>
            <Title headingLevel="h1" size="xl">MTA Migration</Title>
          </SplitItem>
          <SplitItem>
            <Button
              variant="primary"
              icon={<PlusCircleIcon />}
              onClick={() => navigate('/')}
              style={{ backgroundColor: '#0066CC', border: 'none' }}
            >
              New Migration
            </Button>
          </SplitItem>
        </Split>

        {jobsLoading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '3rem' }}>
            <Spinner aria-label="Loading" />
          </div>
        ) : jobs.length === 0 ? (
          <Card>
            <CardBody>
              <EmptyState>
                <EmptyStateHeader
                  titleText="No migration projects"
                  headingLevel="h2"
                  icon={<EmptyStateIcon icon={FolderOpenIcon} />}
                />
                <EmptyStateBody>
                  Start a migration from the home page — upload your MTA report and legacy source code, and AI will handle the rest.
                </EmptyStateBody>
                <EmptyStateActions>
                  <Button variant="primary" onClick={() => navigate('/')}
                    style={{ backgroundColor: '#0066CC', border: 'none' }}>
                    Start New Migration
                  </Button>
                </EmptyStateActions>
              </EmptyState>
            </CardBody>
          </Card>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {jobs.map((j) => {
              const goal = j.vision?.replace(/^\[MTA[^\]]*\]\s*/, '') ?? j.id;
              return (
                <Card
                  key={j.id}
                  isClickable
                  isSelectable
                  onClick={() => navigate(`/migration/${j.id}`)}
                  style={{ cursor: 'pointer' }}
                >
                  <CardTitle>
                    <Split hasGutter>
                      <SplitItem isFilled>
                        <span style={{ fontWeight: 600 }}>{goal.slice(0, 80)}{goal.length > 80 ? '…' : ''}</span>
                      </SplitItem>
                      <SplitItem>
                        <Label color={
                          j.status === 'completed' ? 'green'
                            : j.status === 'running' ? 'blue'
                            : j.status === 'failed' ? 'red'
                            : 'grey'
                        }>
                          {j.status}
                        </Label>
                      </SplitItem>
                    </Split>
                  </CardTitle>
                  <CardBody>
                    <span style={{ fontSize: '0.8125rem', color: '#6A6E73' }}>
                      Created {new Date(j.created_at).toLocaleDateString()} · Job {j.id.slice(0, 8)}…
                    </span>
                  </CardBody>
                </Card>
              );
            })}
          </div>
        )}
      </PageSection>
    );
  }

  /* ═══════════════════════════════════════════════════════════════════════ */
  /* DETAIL VIEW: /migration/:jobId                                         */
  /* ═══════════════════════════════════════════════════════════════════════ */
  if (detailLoading) {
    return (
      <PageSection>
        <div style={{ display: 'flex', justifyContent: 'center', padding: '3rem' }}>
          <Spinner aria-label="Loading" />
        </div>
      </PageSection>
    );
  }

  const migrationGoal = job?.vision?.replace(/^\[MTA[^\]]*\]\s*/, '') ?? '';
  const currentPhase = job?.current_phase ?? 'unknown';
  const lastMsg = job?.last_message && job.last_message.length > 0 
    ? job.last_message[job.last_message.length - 1].message 
    : '';
  
  const completedPct = summary && summary.total > 0
    ? Math.round(((summary.completed + summary.failed + summary.skipped) / summary.total) * 100)
    : 0;

  // Mandatory (high severity) progress: fixed count / total
  const mandatoryIssues = issues.filter((i) => i.severity === 'mandatory');
  const mandatoryTotal = mandatoryIssues.length;
  const mandatoryFixed = mandatoryIssues.filter((i) => i.status === 'completed').length;
  const mandatoryPct = mandatoryTotal > 0 ? Math.round((mandatoryFixed / mandatoryTotal) * 100) : 100;

  // Show parsing/analyzing phase even when no issues yet
  const showParsingProgress = (currentPhase === 'parsing' || currentPhase === 'analyzing') && summary?.total === 0;

  return (
    <PageSection>
      {/* Header */}
      <Split hasGutter style={{ marginBottom: '1rem' }}>
        <SplitItem isFilled>
          <Title headingLevel="h1" size="xl">MTA Migration</Title>
          {migrationGoal && (
            <p style={{ fontSize: '0.9375rem', color: '#6A6E73', marginTop: '0.25rem' }}>
              {migrationGoal}
            </p>
          )}
        </SplitItem>
        <SplitItem>
          <Button variant="link" icon={<ArrowRightIcon />} onClick={() => navigate('/migration')}>
            All Migrations
          </Button>
        </SplitItem>
      </Split>

      {/* Compact progress strip (collapsible) */}
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
      {(showParsingProgress || (summary && summary.total > 0)) && (
        <div
          style={{
            marginBottom: '0.75rem',
            border: '1px solid #D2D2D2',
            borderRadius: 6,
            background: '#FAFAFA',
            fontSize: '0.8125rem',
          }}
        >
          <ExpandableSection
            isExpanded={progressAccordionExpanded}
            onToggle={() => setProgressAccordionExpanded(!progressAccordionExpanded)}
            toggleContent={
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap', lineHeight: 1.4 }}>
                {showParsingProgress ? (
                  <>
                    <SyncAltIcon style={{ color: '#0066CC', fontSize: '0.75rem', animation: 'spin 2s linear infinite' }} />
                    <span style={{ fontWeight: 600 }}>
                      {currentPhase === 'parsing' ? 'Parsing…' : 'Analyzing…'}
                    </span>
                    {lastMsg && <span style={{ color: '#6A6E73', fontSize: '0.75rem' }}>— {lastMsg}</span>}
                  </>
                ) : summary ? (
                  <>
                    {migrating && <SyncAltIcon style={{ color: '#0066CC', fontSize: '0.75rem', animation: 'spin 2s linear infinite' }} />}
                    <span style={{ fontWeight: 600 }}>{migrating ? 'In progress' : 'Complete'}</span>
                    <span style={{ color: '#6A6E73' }}>
                      {summary.completed + summary.failed + summary.skipped}/{summary.total}
                    </span>
                    <span style={{ color: '#B8BBBE' }}>·</span>
                    <span>{completedPct}%</span>
                    {mandatoryTotal > 0 && (
                      <>
                        <span style={{ color: '#B8BBBE' }}>·</span>
                        <span>Mandatory {mandatoryFixed}/{mandatoryTotal}</span>
                      </>
                    )}
                    <span style={{ color: '#B8BBBE' }}>·</span>
                    <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }} data-testid="migration-summary">
                      <Label color="grey" isCompact>Total: {summary.total}</Label>
                      {summary.running > 0 && <Label color="blue" isCompact>Running: {summary.running}</Label>}
                      <Label color="green" isCompact>Done: {summary.completed}</Label>
                      {summary.failed > 0 && <Label color="red" isCompact>Failed: {summary.failed}</Label>}
                    </span>
                  </>
                ) : null}
              </span>
            }
          >
            <div style={{ padding: '0.5rem 0.75rem 0.625rem', borderTop: '1px solid #E8E8E8' }}>
              {showParsingProgress && (
                <>
                  <Progress
                    value={job?.progress ?? 0}
                    title={currentPhase === 'parsing' ? 'Parsing' : 'Analysis'}
                    size="sm"
                  />
                  <p style={{ fontSize: '0.6875rem', color: '#6A6E73', marginTop: '0.25rem' }}>
                    {currentPhase === 'parsing' ? 'Issues will appear shortly' : 'AI is analyzing — may take a few minutes'}
                  </p>
                </>
              )}
              {summary && summary.total > 0 && (
                <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <Progress
                      value={completedPct}
                      title="Migration progress"
                      size="sm"
                      variant={summary.failed > 0 ? ProgressVariant.warning : undefined}
                    />
                  </div>
                  {mandatoryTotal > 0 && (
                    <div style={{ flex: 1, minWidth: 200 }} data-testid="migration-mandatory-progress">
                      <Progress
                        value={mandatoryPct}
                        title={`Mandatory: ${mandatoryFixed}/${mandatoryTotal} fixed`}
                        size="sm"
                      />
                    </div>
                  )}
                  {summary.failed > 0 && !migrating && (
                    <Button
                      variant="link"
                      size="sm"
                      isDisabled={retryingFailed}
                      style={{ padding: 0, fontSize: '0.75rem' }}
                      onClick={async () => {
                        if (!jobId || retryingFailed) return;
                        setRetryingFailed(true);
                        try {
                          await restartJob(jobId);
                          pollStatus();
                        } catch (e) {
                          console.error('Retry failed tasks failed:', e);
                        } finally {
                          setRetryingFailed(false);
                        }
                      }}
                      icon={retryingFailed ? <Spinner size="sm" /> : undefined}
                    >
                      {retryingFailed ? 'Retrying…' : `Retry ${summary.failed} failed`}
                    </Button>
                  )}
                </div>
              )}
            </div>
          </ExpandableSection>
        </div>
      )}

      {/* Main content (full width) */}
      <div>
      {/* No issues yet (only show if not parsing/analyzing) */}
      {(!summary || summary.total === 0) && !migrating && !showParsingProgress && (
        <Card style={{ marginBottom: '1rem' }}>
          <CardBody>
            <EmptyState>
              <EmptyStateHeader
                titleText="Waiting for migration"
                headingLevel="h3"
                icon={<EmptyStateIcon icon={SyncAltIcon} />}
              />
              <EmptyStateBody>
                The migration agent is analysing the MTA report. Issues will appear here as they are discovered and processed.
              </EmptyStateBody>
            </EmptyState>
          </CardBody>
        </Card>
      )}

      {/* File Changes Summary */}
      {fileChanges && fileChanges.total_files > 0 && (
        <Card style={{ marginBottom: '1rem' }}>
          <CardTitle>
            <Split hasGutter>
              <SplitItem isFilled>
                <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <FolderOpenIcon style={{ color: '#0066CC' }} />
                  File Changes Summary
                  <Label isCompact color="blue">{fileChanges.total_files} files</Label>
                </span>
              </SplitItem>
              <SplitItem>
                <span style={{ fontSize: '0.8125rem', display: 'flex', gap: '0.75rem' }}>
                  <span style={{ color: '#3E8635', fontWeight: 600 }}>+{fileChanges.total_insertions}</span>
                  <span style={{ color: '#C9190B', fontWeight: 600 }}>-{fileChanges.total_deletions}</span>
                  <span style={{ color: '#6A6E73', fontSize: '0.75rem' }}>
                    ({fileChanges.baseline_commit} → {fileChanges.head_commit})
                  </span>
                </span>
              </SplitItem>
            </Split>
          </CardTitle>
          <CardBody style={{ padding: 0 }}>
            {/* Always show top files, expandable for full list */}
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #E8E8E8', textAlign: 'left' }}>
                  <th style={{ padding: '0.5rem 1rem', fontWeight: 600, color: '#6A6E73' }}>File</th>
                  <th style={{ padding: '0.5rem 1rem', fontWeight: 600, color: '#6A6E73', width: 80, textAlign: 'center' }}>Change</th>
                  <th style={{ padding: '0.5rem 1rem', fontWeight: 600, color: '#6A6E73', width: 80, textAlign: 'right' }}>Added</th>
                  <th style={{ padding: '0.5rem 1rem', fontWeight: 600, color: '#6A6E73', width: 80, textAlign: 'right' }}>Removed</th>
                  <th style={{ padding: '0.5rem 1rem', fontWeight: 600, color: '#6A6E73', width: 200 }}>Impact</th>
                </tr>
              </thead>
              <tbody>
                {(changesExpanded ? fileChanges.files : fileChanges.files.slice(0, 8)).map((f) => {
                  const total = f.insertions + f.deletions;
                  const insPct = total > 0 ? (f.insertions / total) * 100 : 0;
                  const changeLabel = f.change_type === 'A' ? 'Added' : f.change_type === 'D' ? 'Deleted' : f.change_type === 'R' ? 'Renamed' : 'Modified';
                  const changeColor = f.change_type === 'A' ? '#3E8635' : f.change_type === 'D' ? '#C9190B' : '#0066CC';
                  return (
                    <tr key={f.path} style={{ borderBottom: '1px solid #F0F0F0' }}>
                      <td style={{ padding: '0.4rem 1rem', fontFamily: 'monospace', fontSize: '0.75rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 0 }}>
                        {f.path}
                      </td>
                      <td style={{ padding: '0.4rem 1rem', textAlign: 'center' }}>
                        <Label isCompact style={{ color: changeColor, borderColor: changeColor, background: 'transparent', border: `1px solid ${changeColor}` }}>
                          {changeLabel}
                        </Label>
                      </td>
                      <td style={{ padding: '0.4rem 1rem', textAlign: 'right', color: '#3E8635', fontWeight: 500, fontFamily: 'monospace' }}>
                        {f.insertions > 0 ? `+${f.insertions}` : '—'}
                      </td>
                      <td style={{ padding: '0.4rem 1rem', textAlign: 'right', color: '#C9190B', fontWeight: 500, fontFamily: 'monospace' }}>
                        {f.deletions > 0 ? `-${f.deletions}` : '—'}
                      </td>
                      <td style={{ padding: '0.4rem 1rem' }}>
                        {total > 0 && (
                          <div style={{ display: 'flex', height: '8px', borderRadius: '4px', overflow: 'hidden', background: '#F0F0F0' }}>
                            <div style={{ width: `${insPct}%`, background: '#3E8635', transition: 'width 0.3s' }} />
                            <div style={{ width: `${100 - insPct}%`, background: '#C9190B', transition: 'width 0.3s' }} />
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {fileChanges.files.length > 8 && (
              <div style={{ padding: '0.5rem 1rem', textAlign: 'center', borderTop: '1px solid #F0F0F0' }}>
                <Button variant="link" onClick={() => setChangesExpanded(!changesExpanded)}
                  style={{ fontSize: '0.8125rem' }}>
                  {changesExpanded
                    ? 'Show fewer files'
                    : `Show all ${fileChanges.files.length} files (+${fileChanges.files.length - 8} more)`}
                </Button>
              </div>
            )}
          </CardBody>
        </Card>
      )}

      {/* Issues table */}
      {issues.length > 0 && (
        <Card>
          <CardTitle>Migration Issues</CardTitle>
          <CardBody style={{ padding: 0 }}>
            <Table aria-label="Migration issues" data-testid="migration-issues-table" style={{ tableLayout: 'fixed' }}>
              <Thead>
                <Tr>
                  <Th>ID</Th>
                  <Th>Title</Th>
                  <Th>Severity</Th>
                  <Th>Effort</Th>
                  <Th>Status</Th>
                  <Th style={{ width: '25%', minWidth: 160 }}>Files</Th>
                </Tr>
              </Thead>
              <Tbody>
                {issues.map((issue) => {
                  let filesText = '';
                  try {
                    const files = typeof issue.files === 'string' ? JSON.parse(issue.files) : issue.files;
                    filesText = Array.isArray(files) ? files.join(', ') : String(issue.files ?? '');
                  } catch {
                    filesText = String(issue.files ?? '');
                  }
                  return (
                  <React.Fragment key={issue.id}>
                    <Tr
                      style={{ cursor: 'pointer' }}
                      onClick={() =>
                        setExpandedIssue(expandedIssue === issue.id ? null : issue.id)
                      }
                    >
                      <Td>{issue.id}</Td>
                      <Td>{issue.title}</Td>
                      <Td>
                        <Label color={severityColor(issue.severity)}>{issue.severity}</Label>
                      </Td>
                      <Td>{issue.effort}</Td>
                      <Td>
                        <Label color={statusColor(issue.status)}>{issue.status}</Label>
                      </Td>
                      <Td
                        style={{
                          wordBreak: 'break-word',
                          whiteSpace: 'normal',
                          maxWidth: '25%',
                        }}
                      >
                        {filesText}
                      </Td>
                    </Tr>
                    {expandedIssue === issue.id && (
                      <Tr>
                        <Td colSpan={6}>
                          <ExpandableSection isExpanded toggleText="">
                            <div style={{ padding: '8px 0' }}>
                              <strong>Description:</strong>
                              <p>{issue.description}</p>
                              <strong>Migration Hint:</strong>
                              <p style={{ fontFamily: 'monospace', background: '#f5f5f5', padding: 8, borderRadius: 4 }}>
                                {issue.migration_hint}
                              </p>
                              {issue.error && (
                                <Alert variant="danger" title="Error" isInline>
                                  {issue.error}
                                </Alert>
                              )}
                            </div>
                          </ExpandableSection>
                        </Td>
                      </Tr>
                    )}
                  </React.Fragment>
                  );
                })}
              </Tbody>
            </Table>
          </CardBody>
        </Card>
      )}
      </div>
    </PageSection>
  );
};

export default Migration;
