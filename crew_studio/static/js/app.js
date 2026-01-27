// API base URL
const API_BASE = '/api';

// State
let jobs = [];
let stats = {};
let currentJobId = null;
let dashboardInterval = null;

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    loadStats();
    loadJobs();
    loadWorkspaceFiles();
    
    // Auto-refresh every 2 seconds
    setInterval(() => {
        loadStats();
        loadJobs();
        if (currentJobId) {
            loadImplementationDashboard(currentJobId);
        }
    }, 2000);
});

function setupEventListeners() {
    // Job form submission
    document.getElementById('job-form').addEventListener('submit', handleJobSubmit);
}

async function handleJobSubmit(e) {
    e.preventDefault();
    
    const formData = {
        vision: document.getElementById('vision').value,
        design_specs_path: document.getElementById('design-specs-path').value || null,
        design_specs_urls: document.getElementById('design-specs-urls').value
            .split(',')
            .map(url => url.trim())
            .filter(url => url) || []
    };
    
    try {
        const response = await fetch(`${API_BASE}/jobs`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(formData)
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Failed to create job');
        }
        
        const result = await response.json();
        
        // Reset form
        document.getElementById('job-form').reset();
        
        // Show success message
        alert(`Job created successfully! Job ID: ${result.job_id}`);
        
        // Reload jobs
        loadJobs();
        
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

async function loadStats() {
    try {
        const response = await fetch(`${API_BASE}/stats`);
        if (!response.ok) throw new Error('Failed to load stats');
        
        stats = await response.json();
        updateStatsDisplay();
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

function updateStatsDisplay() {
    document.getElementById('total-jobs').textContent = stats.total_jobs || 0;
    document.getElementById('running-jobs').textContent = stats.running || 0;
    document.getElementById('completed-jobs').textContent = stats.completed || 0;
    document.getElementById('failed-jobs').textContent = stats.failed || 0;
}

async function loadJobs() {
    try {
        const response = await fetch(`${API_BASE}/jobs`);
        if (!response.ok) throw new Error('Failed to load jobs');
        
        const data = await response.json();
        jobs = data.jobs || [];
        renderJobs();
    } catch (error) {
        console.error('Error loading jobs:', error);
        document.getElementById('jobs-list').innerHTML = 
            '<p class="pf-v5-u-text-align-center pf-v5-u-color-200">Error loading jobs</p>';
    }
}

function renderJobs() {
    const container = document.getElementById('jobs-list');
    
    if (jobs.length === 0) {
        container.innerHTML = '<p class="pf-v5-u-text-align-center pf-v5-u-color-200">No jobs yet. Create one to get started!</p>';
        return;
    }
    
    const sortedJobs = [...jobs].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    
    container.innerHTML = sortedJobs.map(job => `
        <div class="pf-v5-c-card pf-m-selectable ${job.id === currentJobId ? 'pf-m-selected' : ''}" onclick="showJobDetails('${job.id}')" style="cursor: pointer; border-left: 4px solid ${getStatusColor(job.status)}">
            <div class="pf-v5-c-card__header">
                <div class="pf-v5-c-card__header-main">
                    <div class="pf-v5-u-font-weight-bold pf-v5-u-text-truncate">${escapeHtml(job.vision)}</div>
                </div>
                <div class="pf-v5-c-card__actions">
                    <span class="pf-v5-c-label ${getStatusLabelClass(job.status)}">
                        <span class="pf-v5-c-label__content">${job.status}</span>
                    </span>
                </div>
            </div>
            <div class="pf-v5-c-card__body pf-v5-u-font-size-xs pf-v5-u-color-200">
                <div class="pf-v5-l-flex pf-m-justify-content-space-between">
                    <span>📅 ${formatDate(job.created_at)}</span>
                    <span>📊 ${job.current_phase || 'N/A'}</span>
                </div>
                ${job.status === 'running' ? `
                    <div class="pf-v5-c-progress pf-m-sm pf-v5-u-mt-sm" id="progress-${job.id}">
                        <div class="pf-v5-c-progress__bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${job.progress || 0}">
                            <div class="pf-v5-c-progress__indicator" style="width: ${job.progress || 0}%"></div>
                        </div>
                    </div>
                ` : ''}
            </div>
        </div>
    `).join('');
}

function getStatusColor(status) {
    switch(status) {
        case 'running': return 'var(--pf-v5-global--info-color--100)';
        case 'completed': return 'var(--pf-v5-global--success-color--100)';
        case 'failed': return 'var(--pf-v5-global--danger-color--100)';
        case 'quota_exhausted': return 'var(--pf-v5-global--warning-color--100)';
        default: return 'var(--pf-v5-global--BorderColor--100)';
    }
}

function getStatusLabelClass(status) {
    switch(status) {
        case 'running': return 'pf-m-blue';
        case 'completed': return 'pf-m-green';
        case 'failed': return 'pf-m-red';
        case 'quota_exhausted': return 'pf-m-orange';
        default: return 'pf-m-grey';
    }
}

async function showJobDetails(jobId) {
    currentJobId = jobId;
    document.getElementById('implementation-dashboard').style.display = 'block';
    loadImplementationDashboard(jobId);
    renderJobs(); // Refresh selection state
    
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}`);
        if (!response.ok) throw new Error('Failed to load job details');
        
        const job = await response.json();
        const modal = document.getElementById('job-modal-backdrop');
        const modalTitle = document.getElementById('modal-title');
        const modalBody = document.getElementById('modal-body');
        
        modalTitle.textContent = `Job: ${job.id.substring(0, 8)}...`;
        
        let html = `
            <div class="pf-v5-c-description-list pf-m-horizontal">
                <div class="pf-v5-c-description-list__group">
                    <dt class="pf-v5-c-description-list__term">Vision</dt>
                    <dd class="pf-v5-c-description-list__description">${escapeHtml(job.vision)}</dd>
                </div>
                <div class="pf-v5-c-description-list__group">
                    <dt class="pf-v5-c-description-list__term">Status</dt>
                    <dd class="pf-v5-c-description-list__description">
                        <span class="pf-v5-c-label ${getStatusLabelClass(job.status)}">${job.status}</span>
                    </dd>
                </div>
                <div class="pf-v5-c-description-list__group">
                    <dt class="pf-v5-c-description-list__term">Phase</dt>
                    <dd class="pf-v5-c-description-list__description">${job.current_phase || 'N/A'}</dd>
                </div>
            </div>
            
            <div class="pf-v5-u-mt-lg">
                <h3 class="pf-v5-u-mb-sm">Timeline</h3>
                <ul class="pf-v5-c-list pf-m-plain">
                    <li>Created: ${formatDate(job.created_at)}</li>
                    ${job.started_at ? `<li>Started: ${formatDate(job.started_at)}</li>` : ''}
                    ${job.completed_at ? `<li>Completed: ${formatDate(job.completed_at)}</li>` : ''}
                </ul>
            </div>
        `;
        
        if (job.error) {
            html += `
                <div class="pf-v5-c-alert pf-m-danger pf-m-inline pf-v5-u-mt-lg">
                    <div class="pf-v5-c-alert__icon"><i class="fas fa-exclamation-circle"></i></div>
                    <h4 class="pf-v5-c-alert__title">Error Details</h4>
                    <div class="pf-v5-c-alert__description">
                        <pre class="pf-v5-u-font-size-xs">${escapeHtml(job.error)}</pre>
                    </div>
                </div>
            `;
        }
        
        modalBody.innerHTML = html;
        modal.style.display = 'block';
        
    } catch (error) {
        alert(`Error loading job details: ${error.message}`);
    }
}

function closeModal() {
    document.getElementById('job-modal-backdrop').style.display = 'none';
}

async function loadWorkspaceFiles(jobId = null) {
    try {
        const url = jobId 
            ? `${API_BASE}/workspace/files?job_id=${jobId}`
            : `${API_BASE}/workspace/files`;
        const response = await fetch(url);
        if (!response.ok) throw new Error('Failed to load files');
        
        const data = await response.json();
        renderWorkspaceFiles(data.files || []);
    } catch (error) {
        console.error('Error loading workspace files:', error);
        document.getElementById('workspace-files').innerHTML = '<p class="pf-v5-u-text-align-center pf-v5-u-color-200">Error loading files</p>';
    }
}

function renderWorkspaceFiles(files) {
    const container = document.getElementById('workspace-files');
    
    if (files.length === 0) {
        container.innerHTML = '<p class="pf-v5-u-text-align-center pf-v5-u-color-200 pf-v5-u-p-lg">No files in workspace yet</p>';
        return;
    }
    
    const sortedFiles = [...files].sort((a, b) => new Date(b.modified) - new Date(a.modified));
    
    container.innerHTML = sortedFiles.slice(0, 20).map(file => `
        <div class="pf-v5-c-data-list__item" onclick="viewFile('${file.path}', currentJobId)" style="cursor: pointer;">
            <div class="pf-v5-c-data-list__item-row">
                <div class="pf-v5-c-data-list__item-control">
                    <div class="pf-v5-c-data-list__item-action">📄</div>
                </div>
                <div class="pf-v5-c-data-list__item-content">
                    <div class="pf-v5-c-data-list__cell">
                        <span class="pf-v5-u-font-weight-bold">${file.path}</span>
                    </div>
                    <div class="pf-v5-c-data-list__cell pf-m-align-right">
                        <span class="pf-v5-u-font-size-xs pf-v5-u-color-200">${formatFileSize(file.size)}</span>
                    </div>
                </div>
            </div>
        </div>
    `).join('');
}

async function viewFile(filePath, jobId = null) {
    try {
        const url = jobId 
            ? `${API_BASE}/workspace/files/${filePath}?job_id=${jobId}`
            : `${API_BASE}/workspace/files/${filePath}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error('Failed to load file');
        
        const data = await response.json();
        
        const modal = document.getElementById('job-modal-backdrop');
        const modalTitle = document.getElementById('modal-title');
        const modalBody = document.getElementById('modal-body');
        
        modalTitle.textContent = `File: ${filePath}`;
        modalBody.innerHTML = `
            <div class="pf-v5-c-code-block">
                <div class="pf-v5-c-code-block__content">
                    <pre class="pf-v5-c-code-block__pre"><code class="pf-v5-c-code-block__code">${escapeHtml(data.content)}</code></pre>
                </div>
            </div>
        `;
        
        modal.style.display = 'block';
        
    } catch (error) {
        alert(`Error loading file: ${error.message}`);
    }
}

async function loadJobFiles(jobId) {
    currentJobId = jobId;
    document.getElementById('implementation-dashboard').style.display = 'block';
    loadImplementationDashboard(jobId);
    await loadWorkspaceFiles(jobId);
    document.getElementById('workspace-files').scrollIntoView({ behavior: 'smooth' });
}

async function loadImplementationDashboard(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/tasks`);
        if (!response.ok) return;
        
        const data = await response.json();
        renderImplementationDashboard(data);
    } catch (error) {
        console.error('Error loading dashboard:', error);
    }
}

function renderImplementationDashboard(data) {
    const tasks = data.tasks || [];
    const statsContainer = document.getElementById('dashboard-stats');
    const colTodo = document.getElementById('col-todo');
    const colInProgress = document.getElementById('col-in-progress');
    const colCompleted = document.getElementById('col-completed');
    
    const completedCount = tasks.filter(t => t.status === 'completed').length;
    const inProgressCount = tasks.filter(t => t.status === 'in_progress').length;
    const totalCount = tasks.length;
    
    statsContainer.innerHTML = `
        <span class="pf-v5-c-label pf-m-grey pf-v5-u-mr-xs">Total: ${totalCount}</span>
        <span class="pf-v5-c-label pf-m-green pf-v5-u-mr-xs">Done: ${completedCount}</span>
        <span class="pf-v5-c-label pf-m-blue">Doing: ${inProgressCount}</span>
    `;
    
    colTodo.innerHTML = '';
    colInProgress.innerHTML = '';
    colCompleted.innerHTML = '';
    
    tasks.forEach(task => {
        const taskEl = document.createElement('div');
        taskEl.className = `pf-v5-c-card pf-m-compact task-card ${task.status}`;
        taskEl.innerHTML = `
            <div class="pf-v5-c-card__body pf-v5-u-p-sm">
                <div class="pf-v5-u-font-size-xs pf-v5-u-color-200 pf-v5-u-text-uppercase pf-v5-u-font-weight-bold">${task.task_type}</div>
                <div class="pf-v5-u-font-size-sm pf-v5-u-mt-xs">${escapeHtml(task.description)}</div>
                <div class="pf-v5-u-font-size-xs pf-v5-u-color-300 pf-v5-u-mt-xs">${task.phase}</div>
            </div>
        `;
        
        if (task.status === 'completed' || task.status === 'skipped') {
            colCompleted.appendChild(taskEl);
        } else if (task.status === 'in_progress') {
            colInProgress.appendChild(taskEl);
        } else {
            colTodo.appendChild(taskEl);
        }
    });
}

// Utility functions
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatDate(dateString) {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleString();
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}
