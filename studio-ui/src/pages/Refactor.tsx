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
    getRefactorStatus,
    getJobs,
    getJob,
    restartJob,
    RefactorTask,
    RefactorSummary,
} from '../api/client';
import type { JobSummary, Job } from '../types';

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
/* Refactor Page                                                             */
/* /refactor        → list refactor projects                                 */
/* /refactor/:jobId → detail view (summary + tasks table)                    */
/* ═══════════════════════════════════════════════════════════════════════════ */
const Refactor: React.FC = () => {
    const { jobId } = useParams<{ jobId?: string }>();
    const navigate = useNavigate();

    // ── List view state ──────────────────────────────────────────────────
    const [jobs, setJobs] = useState<JobSummary[]>([]);
    const [jobsLoading, setJobsLoading] = useState(false);

    // ── Detail view state ────────────────────────────────────────────────
    const [job, setJob] = useState<Job | null>(null);
    const [tasks, setTasks] = useState<RefactorTask[]>([]);
    const [summary, setSummary] = useState<RefactorSummary | null>(null);
    const [refactoring, setRefactoring] = useState(false);
    const [expandedTask, setExpandedTask] = useState<string | null>(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [retryingFailed, setRetryingFailed] = useState(false);
    const [progressAccordionExpanded, setProgressAccordionExpanded] = useState(false);

    // ── Load refactor projects (list view) ──────────────────────────────
    useEffect(() => {
        if (jobId) return;
        let cancelled = false;
        setJobsLoading(true);
        getJobs()
            .then((list) => {
                if (!cancelled) {
                    // Show refactor projects: those with "refactor" in vision or phase
                    const refactorJobs = list.filter(
                        (j) =>
                            j.vision?.toLowerCase().includes('refactor') ||
                            j.current_phase?.toLowerCase().includes('refactor') ||
                            j.vision?.includes('[Refactor]')
                    );
                    setJobs(refactorJobs);
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
            const [jobData, refData] = await Promise.all([
                getJob(jobId).catch(() => null),
                getRefactorStatus(jobId).catch(() => null),
            ]);

            if (jobData) setJob(jobData);

            if (refData) {
                setTasks(refData.tasks || []);
                setSummary(refData.summary);
                if (refData.summary.running > 0 || refData.summary.pending > 0) {
                    setRefactoring(true);
                } else if (refData.summary.total > 0) {
                    setRefactoring(false);
                }
            }

            const currentPhase = jobData?.current_phase ?? '';
            if (currentPhase.includes('refactor') && (jobData?.status === 'running')) {
                setRefactoring(true);
            } else if (jobData?.status !== 'running') {
                setRefactoring(false);
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
            getRefactorStatus(jobId).catch(() => null),
        ]).then(([jobData, refData]) => {
            if (cancelled) return;
            if (jobData) setJob(jobData);
            if (refData) {
                setTasks(refData.tasks || []);
                setSummary(refData.summary);
                if (refData.summary.running > 0 || refData.summary.pending > 0) {
                    setRefactoring(true);
                }
            }
            const currentPhase = jobData?.current_phase ?? '';
            if (currentPhase.includes('refactor') && (jobData?.status === 'running')) {
                setRefactoring(true);
            }
        }).finally(() => {
            if (!cancelled) setDetailLoading(false);
        });
        return () => { cancelled = true; };
    }, [jobId]);

    useEffect(() => {
        if (!refactoring) return;
        const interval = setInterval(pollStatus, 3000);
        return () => clearInterval(interval);
    }, [refactoring, pollStatus]);

    /* ═══════════════════════════════════════════════════════════════════════ */
    /* LIST VIEW: /refactor                                                   */
    /* ═══════════════════════════════════════════════════════════════════════ */
    if (!jobId) {
        return (
            <PageSection>
                <Split hasGutter style={{ marginBottom: '1.5rem' }}>
                    <SplitItem isFilled>
                        <Title headingLevel="h1" size="xl">Code Refactoring</Title>
                    </SplitItem>
                    <SplitItem>
                        <Button
                            variant="primary"
                            icon={<PlusCircleIcon />}
                            onClick={() => navigate('/')}
                            style={{ backgroundColor: '#0066CC', border: 'none' }}
                        >
                            New Refactor
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
                                    titleText="No refactor projects"
                                    headingLevel="h2"
                                    icon={<EmptyStateIcon icon={FolderOpenIcon} />}
                                />
                                <EmptyStateBody>
                                    Start a refactor from the home page — specify your target stack and preferences, and AI will modernize your code.
                                </EmptyStateBody>
                                <EmptyStateActions>
                                    <Button variant="primary" onClick={() => navigate('/')}
                                        style={{ backgroundColor: '#0066CC', border: 'none' }}>
                                        Start New Refactor
                                    </Button>
                                </EmptyStateActions>
                            </EmptyState>
                        </CardBody>
                    </Card>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                        {jobs.map((j) => {
                            const goal = j.vision?.replace(/^\[Refactor[^\]]*\]\s*/, '') ?? j.id;
                            return (
                                <Card
                                    key={j.id}
                                    isClickable
                                    isSelectable
                                    onClick={() => navigate(`/refactor/${j.id}`)}
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
    /* DETAIL VIEW: /refactor/:jobId                                          */
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

    const refactorGoal = job?.vision?.replace(/^\[Refactor[^\]]*\]\s*/, '') ?? '';
    const currentPhase = job?.current_phase ?? 'unknown';
    const lastMsg = job?.last_message && job.last_message.length > 0
        ? job.last_message[job.last_message.length - 1].message
        : '';

    const completedPct = summary && summary.total > 0
        ? Math.round(((summary.completed + summary.failed + summary.skipped) / summary.total) * 100)
        : 0;

    return (
        <PageSection>
            {/* Header */}
            <Split hasGutter style={{ marginBottom: '1rem' }}>
                <SplitItem isFilled>
                    <Title headingLevel="h1" size="xl">Code Refactoring</Title>
                    {refactorGoal && (
                        <p style={{ fontSize: '0.9375rem', color: '#6A6E73', marginTop: '0.25rem' }}>
                            {refactorGoal}
                        </p>
                    )}
                </SplitItem>
                <SplitItem>
                    <Button variant="link" icon={<ArrowRightIcon />} onClick={() => navigate('/refactor')}>
                        All Refactors
                    </Button>
                </SplitItem>
            </Split>

            {/* Progress strip */}
            <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
            {(summary && summary.total > 0) && (
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
                                {refactoring && <SyncAltIcon style={{ color: '#0066CC', fontSize: '0.75rem', animation: 'spin 2s linear infinite' }} />}
                                <span style={{ fontWeight: 600 }}>{refactoring ? 'In progress' : 'Complete'}</span>
                                <span style={{ color: '#6A6E73' }}>
                                    {summary.completed + summary.failed + summary.skipped}/{summary.total} tasks
                                </span>
                                <span style={{ color: '#B8BBBE' }}>·</span>
                                <span>{completedPct}%</span>
                                <span style={{ color: '#B8BBBE' }}>·</span>
                                <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
                                    <Label color="grey" isCompact>Total: {summary.total}</Label>
                                    {summary.running > 0 && <Label color="blue" isCompact>Running: {summary.running}</Label>}
                                    <Label color="green" isCompact>Done: {summary.completed}</Label>
                                    {summary.failed > 0 && <Label color="red" isCompact>Failed: {summary.failed}</Label>}
                                </span>
                            </span>
                        }
                    >
                        <div style={{ padding: '0.5rem 0.75rem 0.625rem', borderTop: '1px solid #E8E8E8' }}>
                            <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'flex-start', flexWrap: 'wrap' }}>
                                <div style={{ flex: 1, minWidth: 200 }}>
                                    <Progress
                                        value={completedPct}
                                        title="Refactor progress"
                                        size="sm"
                                        variant={summary.failed > 0 ? ProgressVariant.warning : undefined}
                                    />
                                </div>
                                {summary.failed > 0 && !refactoring && (
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
                        </div>
                    </ExpandableSection>
                </div>
            )}

            {/* Main content */}
            <div>
                {(!summary || summary.total === 0) && !refactoring && (
                    <Card style={{ marginBottom: '1rem' }}>
                        <CardBody>
                            <EmptyState>
                                <EmptyStateHeader
                                    titleText="Waiting for refactor tasks"
                                    headingLevel="h3"
                                    icon={<EmptyStateIcon icon={SyncAltIcon} />}
                                />
                                <EmptyStateBody>
                                    The refactor architect is analyzing the codebase. Tasks will appear here once the plan is created.
                                </EmptyStateBody>
                            </EmptyState>
                        </CardBody>
                    </Card>
                )}

                {/* Tasks table */}
                {tasks.length > 0 && (
                    <Card>
                        <CardTitle>Refactor Tasks</CardTitle>
                        <CardBody style={{ padding: 0 }}>
                            <Table aria-label="Refactor tasks" style={{ tableLayout: 'fixed' }}>
                                <Thead>
                                    <Tr>
                                        <Th style={{ width: '15%' }}>ID</Th>
                                        <Th style={{ width: '35%' }}>File</Th>
                                        <Th style={{ width: '15%' }}>Action</Th>
                                        <Th style={{ width: '15%' }}>Status</Th>
                                        <Th style={{ width: '20%' }}>Progress</Th>
                                    </Tr>
                                </Thead>
                                <Tbody>
                                    {tasks.map((task) => (
                                        <React.Fragment key={task.id}>
                                            <Tr
                                                style={{ cursor: 'pointer' }}
                                                onClick={() =>
                                                    setExpandedTask(expandedTask === task.id ? null : task.id)
                                                }
                                            >
                                                <Td style={{ fontSize: '0.75rem', fontFamily: 'monospace' }}>{task.id}</Td>
                                                <Td style={{ fontSize: '0.8125rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{task.file_path}</Td>
                                                <Td>
                                                    <Label isCompact color={task.action === 'create' ? 'cyan' : task.action === 'modify' ? 'blue' : 'orange'}>
                                                        {task.action}
                                                    </Label>
                                                </Td>
                                                <Td>
                                                    <Label color={statusColor(task.status)}>{task.status}</Label>
                                                </Td>
                                                <Td>
                                                    {task.status === 'running' && <Spinner size="sm" />}
                                                    {task.status === 'completed' && <span style={{ color: '#3E8635' }}>✓</span>}
                                                    {task.status === 'failed' && <span style={{ color: '#C9190B' }}>✗</span>}
                                                </Td>
                                            </Tr>
                                            {expandedTask === task.id && (
                                                <Tr>
                                                    <Td colSpan={5}>
                                                        <ExpandableSection isExpanded toggleText="">
                                                            <div style={{ padding: '8px 0', fontSize: '0.875rem' }}>
                                                                <strong>Instruction:</strong>
                                                                <p style={{ marginTop: '0.5rem', marginBottom: '1rem' }}>{task.instruction}</p>

                                                                {task.error && (
                                                                    <Alert variant="danger" title="Error" isInline style={{ marginBottom: '1rem' }}>
                                                                        {task.error}
                                                                    </Alert>
                                                                )}

                                                                <div style={{ fontSize: '0.75rem', color: '#6A6E73' }}>
                                                                    Created: {new Date(task.created_at).toLocaleString()}
                                                                    {task.completed_at && ` · Completed: ${new Date(task.completed_at).toLocaleString()}`}
                                                                </div>
                                                            </div>
                                                        </ExpandableSection>
                                                    </Td>
                                                </Tr>
                                            )}
                                        </React.Fragment>
                                    ))}
                                </Tbody>
                            </Table>
                        </CardBody>
                    </Card>
                )}
            </div>
        </PageSection>
    );
};

export default Refactor;
