const { useState, useEffect, useCallback } = React;

const API_BASE = '/api';

// PatternFly CSS classes - using direct class names
const PF = {
    Page: 'pf-v5-c-page',
    PageHeader: 'pf-v5-c-page__header',
    PageMain: 'pf-v5-c-page__main',
    PageSection: 'pf-v5-c-page__main-section',
    Card: 'pf-v5-c-card',
    CardTitle: 'pf-v5-c-card__title',
    CardBody: 'pf-v5-c-card__body',
    CardHeader: 'pf-v5-c-card__header',
    CardActions: 'pf-v5-c-card__actions',
    Grid: 'pf-v5-l-grid',
    GridItem: 'pf-v5-l-grid__item',
    Button: 'pf-v5-c-button',
    ButtonPrimary: 'pf-v5-c-button pf-m-primary',
    Form: 'pf-v5-c-form',
    FormGroup: 'pf-v5-c-form__group',
    FormLabel: 'pf-v5-c-form__label',
    FormControl: 'pf-v5-c-form-control',
    Label: 'pf-v5-c-label',
    DataList: 'pf-v5-c-data-list',
    DataListItem: 'pf-v5-c-data-list__item',
    DataListItemRow: 'pf-v5-c-data-list__item-row',
    DataListItemCells: 'pf-v5-c-data-list__item-cells',
    DataListCell: 'pf-v5-c-data-list__cell',
    Spinner: 'pf-v5-c-spinner',
    Progress: 'pf-v5-c-progress',
    ProgressBar: 'pf-v5-c-progress__bar',
    ProgressIndicator: 'pf-v5-c-progress__indicator',
    Flex: 'pf-v5-l-flex',
    FlexColumn: 'pf-v5-l-flex pf-m-column',
    Title: 'pf-v5-c-title',
    Modal: 'pf-v5-c-backdrop',
    ModalBox: 'pf-v5-c-modal-box',
    ModalBoxHeader: 'pf-v5-c-modal-box__header',
    ModalBoxTitle: 'pf-v5-c-modal-box__title',
    ModalBoxBody: 'pf-v5-c-modal-box__body',
    DescriptionList: 'pf-v5-c-description-list',
    DescriptionListGroup: 'pf-v5-c-description-list__group',
    DescriptionListTerm: 'pf-v5-c-description-list__term',
    DescriptionListDescription: 'pf-v5-c-description-list__description',
    CodeBlock: 'pf-v5-c-code-block',
    CodeBlockCode: 'pf-v5-c-code-block__code',
    Alert: 'pf-v5-c-alert',
    AlertDanger: 'pf-v5-c-alert pf-m-danger'
};

function App() {
    const [jobs, setJobs] = useState([]);
    const [stats, setStats] = useState({ total_jobs: 0, running: 0, completed: 0, failed: 0 });
    const [currentJobId, setCurrentJobId] = useState(null);
    const [workspaceFiles, setWorkspaceFiles] = useState([]);
    const [tasks, setTasks] = useState([]);
    const [phaseStats, setPhaseStats] = useState({});
    const [loading, setLoading] = useState(true);
    const [modalOpen, setModalOpen] = useState(false);
    const [modalContent, setModalContent] = useState(null);
    const [formData, setFormData] = useState({
        vision: '',
        design_specs_path: '',
        design_specs_urls: ''
    });

    const loadStats = useCallback(async () => {
        try {
            const response = await fetch(`${API_BASE}/stats`);
            if (response.ok) {
                const data = await response.json();
                setStats(data);
            }
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }, []);

    const loadJobs = useCallback(async () => {
        try {
            const response = await fetch(`${API_BASE}/jobs`);
            if (response.ok) {
                const data = await response.json();
                setJobs(data.jobs || []);
            }
        } catch (error) {
            console.error('Error loading jobs:', error);
        }
    }, []);

    const loadWorkspaceFiles = useCallback(async (jobId = null) => {
        try {
            const url = jobId 
                ? `${API_BASE}/workspace/files?job_id=${jobId}`
                : `${API_BASE}/workspace/files`;
            const response = await fetch(url);
            if (response.ok) {
                const data = await response.json();
                setWorkspaceFiles(data.files || []);
            }
        } catch (error) {
            console.error('Error loading files:', error);
        }
    }, []);

    const loadTasks = useCallback(async (jobId) => {
        try {
            const response = await fetch(`${API_BASE}/jobs/${jobId}/tasks`);
            if (response.ok) {
                const data = await response.json();
                setTasks(data.tasks || []);
                setPhaseStats(data.phase_stats || {});
            }
        } catch (error) {
            console.error('Error loading tasks:', error);
        }
    }, []);

    useEffect(() => {
        const loadAll = async () => {
            setLoading(true);
            await Promise.all([loadStats(), loadJobs(), loadWorkspaceFiles()]);
            setLoading(false);
        };
        loadAll();

        const interval = setInterval(() => {
            loadStats();
            loadJobs();
            if (currentJobId) {
                loadTasks(currentJobId);
                loadWorkspaceFiles(currentJobId);
            }
        }, 2000);

        return () => clearInterval(interval);
    }, [loadStats, loadJobs, loadWorkspaceFiles, currentJobId, loadTasks]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        try {
            const response = await fetch(`${API_BASE}/jobs`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    vision: formData.vision,
                    design_specs_path: formData.design_specs_path || null,
                    design_specs_urls: formData.design_specs_urls.split(',').map(u => u.trim()).filter(u => u) || []
                })
            });

            if (response.ok) {
                const result = await response.json();
                setFormData({ vision: '', design_specs_path: '', design_specs_urls: '' });
                alert(`Job created! ID: ${result.job_id}`);
                loadJobs();
            } else {
                const error = await response.json();
                alert(`Error: ${error.error || 'Failed to create job'}`);
            }
        } catch (error) {
            alert(`Error: ${error.message}`);
        }
    };

    const handleJobSelect = async (jobId) => {
        setCurrentJobId(jobId);
        await Promise.all([loadTasks(jobId), loadWorkspaceFiles(jobId)]);
        
        try {
            const response = await fetch(`${API_BASE}/jobs/${jobId}`);
            if (response.ok) {
                const job = await response.json();
                setModalContent(job);
                setModalOpen(true);
            }
        } catch (error) {
            console.error('Error loading job details:', error);
        }
    };

    const getStatusColor = (status) => {
        switch(status) {
            case 'running': return 'blue';
            case 'completed': return 'green';
            case 'failed': return 'red';
            case 'quota_exhausted': return 'orange';
            default: return 'grey';
        }
    };

    const tasksByStatus = {
        todo: tasks.filter(t => !t.status || t.status === 'registered' || t.status === 'created'),
        in_progress: tasks.filter(t => t.status === 'in_progress'),
        completed: tasks.filter(t => t.status === 'completed' || t.status === 'skipped')
    };

    // Calculate task percentages
    const totalTasks = tasks.length || 1; // Avoid division by zero
    const completedPct = Math.round((tasksByStatus.completed.length / totalTasks) * 100);
    const inProgressPct = Math.round((tasksByStatus.in_progress.length / totalTasks) * 100);
    const pendingPct = Math.round((tasksByStatus.todo.length / totalTasks) * 100);

    // Get current job for display
    const currentJob = currentJobId ? jobs.find(j => j.id === currentJobId) : null;

    return (
        <div className={PF.Page}>
            <header className={PF.PageHeader} style={{ backgroundColor: '#ee0000' }}>
                <div className="pf-v5-c-page__header-brand">
                    <a href="/" className="pf-v5-c-page__header-brand-link">
                        <span style={{ color: 'white', fontWeight: 'bold', fontSize: '1.5rem', fontFamily: '"Red Hat Display"' }}>
                            CREW STUDIO
                        </span>
                    </a>
                </div>
                <div className="pf-v5-c-page__header-tools">
                    <div className="pf-v5-c-page__header-tools-group">
                        <div className={`${PF.Label} pf-m-green`}>
                            <span className="pf-v5-c-label__content">Connected</span>
                        </div>
                    </div>
                </div>
            </header>

            <main className={PF.PageMain}>
                <section className={`${PF.PageSection} pf-m-light`}>
                    <div className={`${PF.Grid} pf-m-all-12-col-on-sm pf-m-all-6-col-on-md pf-m-all-3-col-on-xl pf-m-gutter`}>
                        <div className={PF.GridItem}>
                            <div className={PF.Card}>
                                <div className={PF.CardTitle}>Total Jobs</div>
                                <div className={PF.CardBody}>
                                    <h2 className="pf-v5-u-font-size-2xl pf-v5-u-font-weight-bold">{stats.total_jobs || 0}</h2>
                                </div>
                            </div>
                        </div>
                        <div className={PF.GridItem}>
                            <div className={PF.Card}>
                                <div className={PF.CardTitle}>Running</div>
                                <div className={PF.CardBody}>
                                    <h2 className="pf-v5-u-font-size-2xl pf-v5-u-font-weight-bold pf-v5-u-info-color-100">{stats.running || 0}</h2>
                                </div>
                            </div>
                        </div>
                        <div className={PF.GridItem}>
                            <div className={PF.Card}>
                                <div className={PF.CardTitle}>Completed</div>
                                <div className={PF.CardBody}>
                                    <h2 className="pf-v5-u-font-size-2xl pf-v5-u-font-weight-bold pf-v5-u-success-color-100">{stats.completed || 0}</h2>
                                </div>
                            </div>
                        </div>
                        <div className={PF.GridItem}>
                            <div className={PF.Card}>
                                <div className={PF.CardTitle}>Failed</div>
                                <div className={PF.CardBody}>
                                    <h2 className="pf-v5-u-font-size-2xl pf-v5-u-font-weight-bold pf-v5-u-danger-color-100">{stats.failed || 0}</h2>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                <section className={PF.PageSection}>
                    <div className={`${PF.Grid} pf-m-gutter`}>
                        <div className={`${PF.GridItem} pf-m-12-col pf-m-7-col-on-lg`}>
                            {currentJobId && currentJob && (
                                <>
                                    {/* Task Statistics Cards */}
                                    <div className={`${PF.Grid} pf-m-all-12-col-on-sm pf-m-all-4-col-on-md pf-m-gutter pf-v5-u-mb-lg`}>
                                        <div className={PF.GridItem}>
                                            <div className={PF.Card} style={{ background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', border: 'none' }}>
                                                <div className={PF.CardBody}>
                                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                                        <div>
                                                            <div style={{ color: 'rgba(255,255,255,0.9)', fontSize: '0.875rem', marginBottom: '0.5rem', fontWeight: '500' }}>
                                                                Tasks Completed
                                                            </div>
                                                            <div style={{ color: 'white', fontSize: '2.5rem', fontWeight: 'bold', lineHeight: '1' }}>
                                                                {completedPct}%
                                                            </div>
                                                            <div style={{ color: 'rgba(255,255,255,0.8)', fontSize: '0.75rem', marginTop: '0.5rem' }}>
                                                                {tasksByStatus.completed.length} of {totalTasks} tasks
                                                            </div>
                                                        </div>
                                                        <div style={{ fontSize: '3rem', opacity: 0.3 }}>✅</div>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                        <div className={PF.GridItem}>
                                            <div className={PF.Card} style={{ background: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)', border: 'none' }}>
                                                <div className={PF.CardBody}>
                                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                                        <div>
                                                            <div style={{ color: 'rgba(255,255,255,0.9)', fontSize: '0.875rem', marginBottom: '0.5rem', fontWeight: '500' }}>
                                                                In Progress
                                                            </div>
                                                            <div style={{ color: 'white', fontSize: '2.5rem', fontWeight: 'bold', lineHeight: '1' }}>
                                                                {inProgressPct}%
                                                            </div>
                                                            <div style={{ color: 'rgba(255,255,255,0.8)', fontSize: '0.75rem', marginTop: '0.5rem' }}>
                                                                {tasksByStatus.in_progress.length} of {totalTasks} tasks
                                                            </div>
                                                        </div>
                                                        <div style={{ fontSize: '3rem', opacity: 0.3 }}>⚡</div>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                        <div className={PF.GridItem}>
                                            <div className={PF.Card} style={{ background: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)', border: 'none' }}>
                                                <div className={PF.CardBody}>
                                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                                        <div>
                                                            <div style={{ color: 'rgba(255,255,255,0.9)', fontSize: '0.875rem', marginBottom: '0.5rem', fontWeight: '500' }}>
                                                                Pending
                                                            </div>
                                                            <div style={{ color: 'white', fontSize: '2.5rem', fontWeight: 'bold', lineHeight: '1' }}>
                                                                {pendingPct}%
                                                            </div>
                                                            <div style={{ color: 'rgba(255,255,255,0.8)', fontSize: '0.75rem', marginTop: '0.5rem' }}>
                                                                {tasksByStatus.todo.length} of {totalTasks} tasks
                                                            </div>
                                                        </div>
                                                        <div style={{ fontSize: '3rem', opacity: 0.3 }}>📋</div>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    {/* Current Job Info */}
                                    <div className={`${PF.Card} pf-v5-u-mb-lg`} style={{ borderLeft: '4px solid #ee0000' }}>
                                        <div className={PF.CardHeader}>
                                            <div className="pf-v5-c-card__header-main">
                                                <div className={PF.CardTitle} style={{ fontSize: '1.1rem' }}>
                                                    🎯 {currentJob.vision.length > 80 ? currentJob.vision.substring(0, 80) + '...' : currentJob.vision}
                                                </div>
                                            </div>
                                            <div className={PF.CardActions}>
                                                <span className={`${PF.Label} pf-m-${getStatusColor(currentJob.status)}`}>
                                                    <span className="pf-v5-c-label__content">{currentJob.status}</span>
                                                </span>
                                            </div>
                                        </div>
                                        <div className={PF.CardBody}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                                                <div>
                                                    <span style={{ fontSize: '0.875rem', color: '#6a6e73' }}>Current Phase: </span>
                                                    <span style={{ fontSize: '0.875rem', fontWeight: 'bold', color: '#151515' }}>{currentJob.current_phase || 'N/A'}</span>
                                                </div>
                                                <div>
                                                    <span style={{ fontSize: '0.875rem', color: '#6a6e73' }}>Progress: </span>
                                                    <span style={{ fontSize: '0.875rem', fontWeight: 'bold', color: '#151515' }}>{currentJob.progress || 0}%</span>
                                                </div>
                                            </div>
                                            {currentJob.status === 'running' && (
                                                <div className={`${PF.Progress}`}>
                                                    <div className={PF.ProgressBar} role="progressbar" aria-valuenow={currentJob.progress || 0} aria-valuemin="0" aria-valuemax="100">
                                                        <div className={PF.ProgressIndicator} style={{ width: `${currentJob.progress || 0}%` }}></div>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    </div>

                                    {/* Phase Progress - More compact */}
                                    {Object.keys(phaseStats).length > 0 && (
                                        <div className={`${PF.Card} pf-v5-u-mb-lg`}>
                                            <div className={PF.CardHeader}>
                                                <div className={PF.CardTitle}>📊 Phase Breakdown</div>
                                            </div>
                                            <div className={PF.CardBody}>
                                                <div className={`${PF.Grid} pf-m-all-12-col-on-sm pf-m-all-6-col-on-md pf-m-all-3-col-on-xl pf-m-gutter`}>
                                                    {Object.entries(phaseStats).map(([phase, stats]) => {
                                                        const pct = Math.round((stats.completed / stats.total) * 100) || 0;
                                                        return (
                                                            <div key={phase} className={PF.GridItem}>
                                                                <div style={{ textAlign: 'center', padding: '1rem', backgroundColor: '#f5f5f5', borderRadius: '8px' }}>
                                                                    <div style={{ fontSize: '0.75rem', fontWeight: 'bold', color: '#6a6e73', marginBottom: '0.5rem', textTransform: 'uppercase' }}>
                                                                        {phase}
                                                                    </div>
                                                                    <div style={{ fontSize: '2rem', fontWeight: 'bold', color: pct === 100 ? '#3e8635' : '#0066cc', marginBottom: '0.5rem' }}>
                                                                        {pct}%
                                                                    </div>
                                                                    <div style={{ fontSize: '0.7rem', color: '#8a8d90' }}>
                                                                        {stats.completed}/{stats.total} tasks
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            </div>
                                        </div>
                                    )}

                                    {/* Task Kanban Board - Collapsible */}
                                    <div className={PF.Card}>
                                        <div className={PF.CardHeader}>
                                            <div className={PF.CardTitle}>🗂️ Task Details</div>
                                        </div>
                                        <div className={PF.CardBody}>
                                            <div className={`${PF.Grid} pf-m-gutter`}>
                                            <div className={`${PF.GridItem} pf-m-4-col`}>
                                                <h4 className="pf-v5-u-mb-sm pf-v5-u-font-size-sm" style={{ fontWeight: 'bold', color: '#151515' }}>TO DO ({tasksByStatus.todo.length})</h4>
                                                <div className={`${PF.FlexColumn} pf-m-space-items-sm`} style={{ maxHeight: '400px', overflowY: 'auto' }}>
                                                    {tasksByStatus.todo.length === 0 ? (
                                                        <div style={{ textAlign: 'center', padding: '2rem', color: '#8a8d90', fontSize: '0.875rem' }}>
                                                            No pending tasks
                                                        </div>
                                                    ) : (
                                                        tasksByStatus.todo.slice(0, 10).map(task => (
                                                            <div key={task.task_id} className={`${PF.Card} pf-m-compact`} style={{ borderLeft: '4px solid #c7c7c7', marginBottom: '0.5rem', backgroundColor: '#fafafa' }}>
                                                                <div className={PF.CardBody} style={{ padding: '0.75rem' }}>
                                                                    <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', fontWeight: 'bold', color: '#8a8d90', marginBottom: '0.25rem' }}>
                                                                        {task.task_type}
                                                                    </div>
                                                                    <div style={{ fontSize: '0.85rem', marginBottom: '0.25rem', color: '#151515' }}>
                                                                        {task.description.length > 50 ? task.description.substring(0, 50) + '...' : task.description}
                                                                    </div>
                                                                    <div style={{ fontSize: '0.7rem', color: '#8a8d90' }}>
                                                                        📁 {task.phase}
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        ))
                                                    )}
                                                    {tasksByStatus.todo.length > 10 && (
                                                        <div style={{ textAlign: 'center', padding: '0.5rem', color: '#6a6e73', fontSize: '0.75rem' }}>
                                                            + {tasksByStatus.todo.length - 10} more tasks
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                            <div className={`${PF.GridItem} pf-m-4-col`}>
                                                <h4 className="pf-v5-u-mb-sm pf-v5-u-font-size-sm" style={{ fontWeight: 'bold', color: '#151515' }}>IN PROGRESS ({tasksByStatus.in_progress.length})</h4>
                                                <div className={`${PF.FlexColumn} pf-m-space-items-sm`} style={{ maxHeight: '400px', overflowY: 'auto' }}>
                                                    {tasksByStatus.in_progress.length === 0 ? (
                                                        <div style={{ textAlign: 'center', padding: '2rem', color: '#8a8d90', fontSize: '0.875rem' }}>
                                                            No active tasks
                                                        </div>
                                                    ) : (
                                                        tasksByStatus.in_progress.map(task => (
                                                            <div key={task.task_id} className={`${PF.Card} pf-m-compact`} style={{ borderLeft: '4px solid #f0ab00', marginBottom: '0.5rem', backgroundColor: '#fffbf0' }}>
                                                                <div className={PF.CardBody} style={{ padding: '0.75rem' }}>
                                                                    <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', fontWeight: 'bold', color: '#f0ab00', marginBottom: '0.25rem' }}>
                                                                        {task.task_type}
                                                                    </div>
                                                                    <div style={{ fontSize: '0.85rem', marginBottom: '0.25rem', color: '#151515', fontWeight: '500' }}>
                                                                        {task.description.length > 50 ? task.description.substring(0, 50) + '...' : task.description}
                                                                    </div>
                                                                    <div style={{ fontSize: '0.7rem', color: '#8a8d90' }}>
                                                                        📁 {task.phase}
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        ))
                                                    )}
                                                </div>
                                            </div>
                                            <div className={`${PF.GridItem} pf-m-4-col`}>
                                                <h4 className="pf-v5-u-mb-sm pf-v5-u-font-size-sm" style={{ fontWeight: 'bold', color: '#151515' }}>COMPLETED ({tasksByStatus.completed.length})</h4>
                                                <div className={`${PF.FlexColumn} pf-m-space-items-sm`} style={{ maxHeight: '400px', overflowY: 'auto' }}>
                                                    {tasksByStatus.completed.length === 0 ? (
                                                        <div style={{ textAlign: 'center', padding: '2rem', color: '#8a8d90', fontSize: '0.875rem' }}>
                                                            No completed tasks yet
                                                        </div>
                                                    ) : (
                                                        tasksByStatus.completed.slice(0, 10).map(task => (
                                                            <div key={task.task_id} className={`${PF.Card} pf-m-compact`} style={{ borderLeft: '4px solid #3e8635', marginBottom: '0.5rem', backgroundColor: '#f0fdf4' }}>
                                                                <div className={PF.CardBody} style={{ padding: '0.75rem' }}>
                                                                    <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', fontWeight: 'bold', color: '#3e8635', marginBottom: '0.25rem' }}>
                                                                        {task.task_type}
                                                                    </div>
                                                                    <div style={{ fontSize: '0.85rem', marginBottom: '0.25rem', color: '#151515' }}>
                                                                        {task.description.length > 50 ? task.description.substring(0, 50) + '...' : task.description}
                                                                    </div>
                                                                    <div style={{ fontSize: '0.7rem', color: '#8a8d90' }}>
                                                                        📁 {task.phase}
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        ))
                                                    )}
                                                    {tasksByStatus.completed.length > 10 && (
                                                        <div style={{ textAlign: 'center', padding: '0.5rem', color: '#6a6e73', fontSize: '0.75rem' }}>
                                                            + {tasksByStatus.completed.length - 10} more tasks
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </>
                            )}

                            <div className={`${PF.Card} pf-v5-u-mb-lg`}>
                                <div className={PF.CardTitle}>🚀 Create New Build Job</div>
                                <div className={PF.CardBody}>
                                    <form className={PF.Form} onSubmit={handleSubmit}>
                                        <div className={PF.FormGroup}>
                                            <div className="pf-v5-c-form__group-label">
                                                <label className={PF.FormLabel} htmlFor="vision">
                                                    <span className="pf-v5-c-form__label-text">Project Vision / Idea.md</span>
                                                    <span className="pf-v5-c-form__label-required" aria-hidden="true">*</span>
                                                </label>
                                            </div>
                                            <div className="pf-v5-c-form__group-control">
                                                <textarea
                                                    className={PF.FormControl}
                                                    id="vision"
                                                    name="vision"
                                                    value={formData.vision}
                                                    onChange={(e) => setFormData({...formData, vision: e.target.value})}
                                                    placeholder="Describe your project vision..."
                                                    rows={5}
                                                    required
                                                />
                                            </div>
                                        </div>
                                        <div className={PF.FormGroup}>
                                            <div className="pf-v5-c-form__group-label">
                                                <label className={PF.FormLabel} htmlFor="design-specs-path">Design Specs Directory</label>
                                            </div>
                                            <div className="pf-v5-c-form__group-control">
                                                <input
                                                    className={PF.FormControl}
                                                    type="text"
                                                    id="design-specs-path"
                                                    name="design_specs_path"
                                                    value={formData.design_specs_path}
                                                    onChange={(e) => setFormData({...formData, design_specs_path: e.target.value})}
                                                    placeholder="/path/to/specs"
                                                />
                                            </div>
                                        </div>
                                        <div className={PF.FormGroup}>
                                            <div className="pf-v5-c-form__group-label">
                                                <label className={PF.FormLabel} htmlFor="design-specs-urls">Design Spec URLs</label>
                                            </div>
                                            <div className="pf-v5-c-form__group-control">
                                                <input
                                                    className={PF.FormControl}
                                                    type="text"
                                                    id="design-specs-urls"
                                                    name="design_specs_urls"
                                                    value={formData.design_specs_urls}
                                                    onChange={(e) => setFormData({...formData, design_specs_urls: e.target.value})}
                                                    placeholder="https://docs.example.com"
                                                />
                                            </div>
                                        </div>
                                        <div className={`${PF.FormGroup} pf-m-action`}>
                                            <button className={PF.ButtonPrimary} type="submit">Start Build Job</button>
                                        </div>
                                    </form>
                                </div>
                            </div>

                            <div className={PF.Card}>
                                <div className={PF.CardTitle}>📁 Workspace Files</div>
                                <div className={PF.CardBody}>
                                    {loading ? (
                                        <div className="pf-v5-l-bullseye" style={{ padding: '2rem' }}>
                                            <span className={`${PF.Spinner} pf-m-md`} role="progressbar" aria-label="Loading files">
                                                <span className="pf-v5-c-spinner__clipper"></span>
                                                <span className="pf-v5-c-spinner__lead-ball"></span>
                                                <span className="pf-v5-c-spinner__tail-ball"></span>
                                            </span>
                                        </div>
                                    ) : workspaceFiles.length === 0 ? (
                                        <div style={{ textAlign: 'center', padding: '2rem', color: '#6a6e73' }}>
                                            No files in workspace yet
                                        </div>
                                    ) : (
                                        <div className={PF.DataList} aria-label="Workspace files">
                                            {workspaceFiles.slice(0, 20).map((file, index) => (
                                                <div key={index} className={PF.DataListItem} onClick={() => {
                                                    fetch(`${API_BASE}/workspace/files/${file.path}${currentJobId ? `?job_id=${currentJobId}` : ''}`)
                                                        .then(r => r.json())
                                                        .then(data => {
                                                            setModalContent({ type: 'file', path: file.path, content: data.content });
                                                            setModalOpen(true);
                                                        });
                                                }} style={{ cursor: 'pointer' }}>
                                                    <div className={PF.DataListItemRow}>
                                                        <div className={PF.DataListItemCells}>
                                                            <div className={PF.DataListCell}>📄</div>
                                                            <div className={PF.DataListCell}>
                                                                <span style={{ fontWeight: 'bold' }}>{file.path}</span>
                                                            </div>
                                                            <div className={`${PF.DataListCell} pf-m-align-right`}>
                                                                <span style={{ fontSize: '0.875rem', color: '#6a6e73' }}>
                                                                    {formatFileSize(file.size)}
                                                                </span>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </div>
                        </div>

                        <div className={`${PF.GridItem} pf-m-12-col pf-m-5-col-on-lg`}>
                            <div className={PF.Card} style={{ height: '100%' }}>
                                <div className={PF.CardTitle}>📊 Job History</div>
                                <div className={PF.CardBody}>
                                    {loading ? (
                                        <div className="pf-v5-l-bullseye" style={{ padding: '2rem' }}>
                                            <span className={`${PF.Spinner} pf-m-md`} role="progressbar" aria-label="Loading jobs">
                                                <span className="pf-v5-c-spinner__clipper"></span>
                                                <span className="pf-v5-c-spinner__lead-ball"></span>
                                                <span className="pf-v5-c-spinner__tail-ball"></span>
                                            </span>
                                        </div>
                                    ) : jobs.length === 0 ? (
                                        <div style={{ textAlign: 'center', padding: '2rem', color: '#6a6e73' }}>
                                            No jobs yet. Create one to get started!
                                        </div>
                                    ) : (
                                        <div className={`${PF.FlexColumn} pf-m-space-items-md`}>
                                            {jobs.sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).map(job => (
                                                <div
                                                    key={job.id}
                                                    className={`${PF.Card} ${job.id === currentJobId ? 'pf-m-selected' : ''}`}
                                                    onClick={() => handleJobSelect(job.id)}
                                                    style={{ 
                                                        cursor: 'pointer',
                                                        borderLeft: `4px solid ${
                                                            job.status === 'running' ? '#0066cc' :
                                                            job.status === 'completed' ? '#3e8635' :
                                                            job.status === 'failed' ? '#c9190b' :
                                                            job.status === 'quota_exhausted' ? '#f0ab00' : '#8a8d90'
                                                        }`
                                                    }}
                                                >
                                                    <div className={PF.CardHeader}>
                                                        <div className="pf-v5-c-card__header-main">
                                                            <div className={PF.CardTitle} style={{ fontSize: '0.875rem' }}>{job.vision}</div>
                                                        </div>
                                                        <div className={PF.CardActions}>
                                                            <span className={`${PF.Label} pf-m-${getStatusColor(job.status)}`}>
                                                                <span className="pf-v5-c-label__content">{job.status}</span>
                                                            </span>
                                                        </div>
                                                    </div>
                                                    <div className={PF.CardBody} style={{ fontSize: '0.75rem', color: '#6a6e73' }}>
                                                        <div className={`${PF.Flex} pf-m-justify-content-space-between`}>
                                                            <span>📅 {formatDate(job.created_at)}</span>
                                                            <span>📊 {job.current_phase || 'N/A'}</span>
                                                        </div>
                                                        {job.status === 'running' && (
                                                            <div className={`${PF.Progress} pf-m-sm`} style={{ marginTop: '0.5rem' }}>
                                                                <div className={PF.ProgressBar} role="progressbar" aria-valuenow={job.progress || 0} aria-valuemin="0" aria-valuemax="100">
                                                                    <div className={PF.ProgressIndicator} style={{ width: `${job.progress || 0}%` }}></div>
                                                                </div>
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                </section>
            </main>

            {modalOpen && (
                <div className={PF.Modal} style={{ display: 'block' }} onClick={() => setModalOpen(false)}>
                    <div className="pf-v5-l-bullseye">
                        <div className={`${PF.ModalBox} pf-m-lg`} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
                            <button className={`${PF.Button} pf-m-plain`} type="button" aria-label="Close" onClick={() => setModalOpen(false)}>
                                <i className="fas fa-times" aria-hidden="true"></i>
                            </button>
                            <header className={PF.ModalBoxHeader}>
                                <h1 className={PF.ModalBoxTitle} id="modal-title">
                                    {modalContent?.type === 'file' ? `File: ${modalContent.path}` : `Job: ${modalContent?.id?.substring(0, 8)}...`}
                                </h1>
                            </header>
                            <div className={PF.ModalBoxBody}>
                                {modalContent?.type === 'file' ? (
                                    <div className={PF.CodeBlock}>
                                        <div className="pf-v5-c-code-block__content">
                                            <pre className="pf-v5-c-code-block__pre"><code className={PF.CodeBlockCode}>{modalContent.content}</code></pre>
                                        </div>
                                    </div>
                                ) : modalContent ? (
                                    <>
                                        <div className={PF.DescriptionList}>
                                            <div className={PF.DescriptionListGroup}>
                                                <dt className={PF.DescriptionListTerm}>Vision</dt>
                                                <dd className={PF.DescriptionListDescription}>{modalContent.vision}</dd>
                                            </div>
                                            <div className={PF.DescriptionListGroup}>
                                                <dt className={PF.DescriptionListTerm}>Status</dt>
                                                <dd className={PF.DescriptionListDescription}>
                                                    <span className={`${PF.Label} pf-m-${getStatusColor(modalContent.status)}`}>
                                                        <span className="pf-v5-c-label__content">{modalContent.status}</span>
                                                    </span>
                                                </dd>
                                            </div>
                                            <div className={PF.DescriptionListGroup}>
                                                <dt className={PF.DescriptionListTerm}>Phase</dt>
                                                <dd className={PF.DescriptionListDescription}>{modalContent.current_phase || 'N/A'}</dd>
                                            </div>
                                        </div>
                                        {modalContent.error && (
                                            <div className={`${PF.AlertDanger} pf-m-inline`} style={{ marginTop: '1rem' }}>
                                                <div className="pf-v5-c-alert__icon"><i className="fas fa-exclamation-circle"></i></div>
                                                <h4 className="pf-v5-c-alert__title">Error Details</h4>
                                                <div className="pf-v5-c-alert__description">
                                                    <pre style={{ fontSize: '0.75rem', whiteSpace: 'pre-wrap' }}>{modalContent.error}</pre>
                                                </div>
                                            </div>
                                        )}
                                    </>
                                ) : null}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function formatDate(dateString) {
    if (!dateString) return 'N/A';
    return new Date(dateString).toLocaleString();
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

ReactDOM.render(<App />, document.getElementById('root'));
