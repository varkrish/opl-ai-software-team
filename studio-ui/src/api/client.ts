import axios from 'axios';
import type {
  Stats,
  Job,
  JobSummary,
  JobProgress,
  Task,
  Agent,
  WorkspaceFile,
  HealthCheck,
  BackendOption,
  Refinement,
} from '../types';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  headers: { 'Content-Type': 'application/json' },
});

// ── Stats ───────────────────────────────────────────────────────────────────
export async function getStats(): Promise<Stats> {
  const { data } = await api.get<Stats>('/api/stats');
  return data;
}

// ── Backends ────────────────────────────────────────────────────────────────
export async function getBackends(): Promise<BackendOption[]> {
  const { data } = await api.get<{ backends: BackendOption[] }>('/api/backends');
  return data.backends;
}

// ── Jobs ────────────────────────────────────────────────────────────────────
export async function getJobs(): Promise<JobSummary[]> {
  const { data } = await api.get<{ jobs: JobSummary[] }>('/api/jobs');
  return data.jobs;
}

export async function getJob(jobId: string): Promise<Job> {
  const { data } = await api.get<Job>(`/api/jobs/${jobId}`);
  return data;
}

export async function createJob(
  vision: string,
  documents?: File[],
  githubUrls?: string[],
  backend?: string
): Promise<{ job_id: string; status: string; documents: number; github_repos: number }> {
  const hasFiles = documents && documents.length > 0;
  const hasGithub = githubUrls && githubUrls.length > 0;

  if (hasFiles || hasGithub) {
    const formData = new FormData();
    formData.append('vision', vision);
    if (backend) formData.append('backend', backend);
    if (hasFiles) {
      documents!.forEach((file) => formData.append('documents', file));
    }
    if (hasGithub) {
      githubUrls!.forEach((url) => formData.append('github_urls', url));
    }
    const { data } = await api.post<{
      job_id: string; status: string; documents: number; github_repos: number;
    }>(
      '/api/jobs',
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    );
    return data;
  }
  const { data } = await api.post<{
    job_id: string; status: string; documents: number; github_repos: number;
  }>(
    '/api/jobs',
    { vision, backend }
  );
  return data;
}

/**
 * Create a migration job with a source code archive and MTA report files.
 *
 * - source_archive → extracted into workspace root (preserves directory tree)
 * - documents      → placed in workspace/docs/ (MTA reports)
 * - mode=migration → tells backend to skip the build pipeline
 */
export async function createMigrationJob(
  vision: string,
  sourceArchive: File | null,
  reportFiles: File[],
  githubUrls?: string[],
  backend?: string,
): Promise<{ job_id: string; status: string; documents: number; source_files: number; github_repos: number }> {
  const formData = new FormData();
  formData.append('vision', vision);
  formData.append('mode', 'migration');
  if (backend) formData.append('backend', backend);
  if (sourceArchive) {
    formData.append('source_archive', sourceArchive);
  }
  reportFiles.forEach((file) => formData.append('documents', file));
  if (githubUrls) {
    githubUrls.forEach((url) => formData.append('github_urls', url));
  }
  const { data } = await api.post<{
    job_id: string; status: string; documents: number; source_files: number; github_repos: number;
  }>(
    '/api/jobs',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return data;
}

/**
 * Create a refactor job with source code.
 */
export async function createRefactorJob(
  vision: string,
  sourceArchive: File | null,
  githubUrls?: string[],
  backend?: string,
): Promise<{ job_id: string; status: string; documents: number; source_files: number; github_repos: number }> {
  const formData = new FormData();
  formData.append('vision', vision);
  formData.append('mode', 'refactor');
  if (backend) formData.append('backend', backend);
  if (sourceArchive) {
    formData.append('source_archive', sourceArchive);
  }
  if (githubUrls) {
    githubUrls.forEach((url) => formData.append('github_urls', url));
  }
  const { data } = await api.post<{
    job_id: string; status: string; documents: number; source_files: number; github_repos: number;
  }>(
    '/api/jobs',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return data;
}

export interface JobDocument {
  id: string;
  job_id: string;
  filename: string;
  original_name: string;
  file_type: string;
  file_size: number;
  stored_path: string;
  uploaded_at: string;
}

export async function getJobDocuments(jobId: string): Promise<JobDocument[]> {
  const { data } = await api.get<{ documents: JobDocument[] }>(`/api/jobs/${jobId}/documents`);
  return data.documents;
}

export async function uploadJobDocuments(
  jobId: string,
  files: File[]
): Promise<{ uploaded: number; documents: JobDocument[] }> {
  const formData = new FormData();
  files.forEach((file) => formData.append('documents', file));
  const { data } = await api.post<{ uploaded: number; documents: JobDocument[] }>(
    `/api/jobs/${jobId}/documents`,
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } }
  );
  return data;
}

export async function deleteJobDocument(
  jobId: string,
  docId: string
): Promise<{ status: string }> {
  const { data } = await api.delete<{ status: string }>(
    `/api/jobs/${jobId}/documents/${docId}`
  );
  return data;
}

export async function cancelJob(jobId: string): Promise<{ status: string }> {
  const { data } = await api.post<{ status: string }>(`/api/jobs/${jobId}/cancel`);
  return data;
}

export async function restartJob(jobId: string): Promise<{ status: string; job_type: string; job_id: string }> {
  const { data } = await api.post<{ status: string; job_type: string; job_id: string }>(`/api/jobs/${jobId}/restart`);
  return data;
}

// ── Job Progress ────────────────────────────────────────────────────────────
export async function getJobProgress(jobId: string): Promise<JobProgress> {
  const { data } = await api.get<JobProgress>(`/api/jobs/${jobId}/progress`);
  return data;
}

// ── Job Tasks ───────────────────────────────────────────────────────────────
export async function getJobTasks(
  jobId: string
): Promise<{ total_tasks: number; tasks: Task[] }> {
  const { data } = await api.get<{ total_tasks: number; tasks: Task[] }>(
    `/api/jobs/${jobId}/tasks`
  );
  return data;
}

// ── Job Agents ──────────────────────────────────────────────────────────────
export async function getJobAgents(jobId: string): Promise<Agent[]> {
  const { data } = await api.get<{ agents: Agent[] }>(`/api/jobs/${jobId}/agents`);
  return data.agents;
}

// ── Job Files ───────────────────────────────────────────────────────────────
export async function getJobFiles(jobId: string): Promise<WorkspaceFile[]> {
  const { data } = await api.get<{ files: WorkspaceFile[] }>(`/api/jobs/${jobId}/files`);
  return data.files;
}

// ── Job Budget ──────────────────────────────────────────────────────────────
export async function getJobBudget(jobId: string): Promise<Record<string, unknown>> {
  const { data } = await api.get<Record<string, unknown>>(`/api/jobs/${jobId}/budget`);
  return data;
}

// ── Refinement ──────────────────────────────────────────────────────────────
export async function refineJob(
  jobId: string,
  prompt: string,
  filePath?: string
): Promise<{ status: string; message?: string; refinement_id?: string }> {
  const { data } = await api.post<{ status: string; message?: string; refinement_id?: string }>(
    `/api/jobs/${jobId}/refine`,
    { prompt, file_path: filePath }
  );
  return data;
}

export async function getRefinementHistory(jobId: string): Promise<Refinement[]> {
  const { data } = await api.get<{ refinements: Refinement[] }>(
    `/api/jobs/${jobId}/refinements`
  );
  return data.refinements;
}

/** Base URL for job preview (append file path). Use in iframe src with sandbox. */
export function getPreviewUrl(jobId: string, filePath: string): string {
  const base = import.meta.env.VITE_API_URL || '';
  const encoded = filePath.split('/').map(encodeURIComponent).join('/');
  return `${base}/api/jobs/${jobId}/preview/${encoded}`;
}

/** URL to download the job workspace as a ZIP file (for direct links). */
export function getJobDownloadUrl(jobId: string): string {
  const base = import.meta.env.VITE_API_URL || '';
  return `${base}/api/jobs/${jobId}/download`;
}

/** Fetch job workspace as ZIP and trigger browser download. Uses same origin/proxy as other API calls. */
export async function downloadJobWorkspace(jobId: string): Promise<void> {
  const { data } = await api.get<Blob>(`/api/jobs/${jobId}/download`, {
    responseType: 'blob',
  });
  const url = URL.createObjectURL(data);
  const a = document.createElement('a');
  a.href = url;
  a.download = `project-${jobId.slice(0, 8)}.zip`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── Workspace Files ─────────────────────────────────────────────────────────
export async function getWorkspaceFiles(jobId?: string): Promise<WorkspaceFile[]> {
  const params = jobId ? { job_id: jobId } : {};
  const { data } = await api.get<{ files: WorkspaceFile[]; workspace: string }>(
    '/api/workspace/files',
    { params }
  );
  return data.files;
}

export async function getFileContent(
  filePath: string,
  jobId?: string
): Promise<{ path: string; content: string }> {
  const params = jobId ? { job_id: jobId } : {};
  const { data } = await api.get<{ path: string; content: string }>(
    `/api/workspace/files/${filePath}`,
    { params }
  );
  return data;
}

// ── Migration ────────────────────────────────────────────────────────────────

export interface MigrationIssue {
  id: string;
  job_id: string;
  migration_id: string;
  title: string;
  severity: string;
  effort: string;
  files: string;
  description: string;
  migration_hint: string;
  status: string;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface MigrationSummary {
  total: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
  skipped: number;
}

export interface MigrationStatus {
  job_id: string;
  summary: MigrationSummary;
  issues: MigrationIssue[];
}

export async function startMigration(
  jobId: string,
  migrationGoal: string,
  migrationNotes?: string
): Promise<{ status: string; migration_id: string; message: string }> {
  const { data } = await api.post(`/api/jobs/${jobId}/migrate`, {
    migration_goal: migrationGoal,
    migration_notes: migrationNotes || undefined,
  });
  return data;
}

export async function startRefactor(
  jobId: string,
  targetStack: string,
  techPreferences: string
): Promise<{ status: string; message: string }> {
  const { data } = await api.post(`/api/jobs/${jobId}/refactor`, {
    target_stack: targetStack,
    tech_preferences: techPreferences
  });
  return data;
}

export async function getMigrationStatus(jobId: string): Promise<MigrationStatus> {
  const { data } = await api.get<MigrationStatus>(`/api/jobs/${jobId}/migration`);
  return data;
}

export async function getMigrationPlan(jobId: string): Promise<Record<string, unknown>> {
  const { data } = await api.get(`/api/jobs/${jobId}/migration/plan`);
  return data;
}

export interface MigrationFileChange {
  path: string;
  change_type: string;  // A=added, M=modified, D=deleted, R=renamed
  insertions: number;
  deletions: number;
}

export interface MigrationChanges {
  job_id: string;
  baseline_commit: string;
  head_commit: string;
  total_files: number;
  total_insertions: number;
  total_deletions: number;
  files: MigrationFileChange[];
}

export async function getMigrationChanges(jobId: string): Promise<MigrationChanges> {
  const { data } = await api.get<MigrationChanges>(`/api/jobs/${jobId}/migration/changes`);
  return data;
}

// ── Refactor ─────────────────────────────────────────────────────────────────

export interface RefactorTask {
  id: string;
  job_id: string;
  file_path: string;
  action: string;
  instruction: string;
  status: string;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface RefactorSummary {
  total: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
  skipped: number;
}

export interface RefactorStatus {
  job_id: string;
  summary: RefactorSummary;
  tasks: RefactorTask[];
}

export async function getRefactorStatus(jobId: string): Promise<RefactorStatus> {
  const { data } = await api.get<RefactorStatus>(`/api/jobs/${jobId}/refactor`);
  return data;
}

// ── Health ───────────────────────────────────────────────────────────────────
export async function getHealth(): Promise<HealthCheck> {
  const { data } = await api.get<HealthCheck>('/health');
  return data;
}

export async function getHealthReady(): Promise<HealthCheck> {
  const { data } = await api.get<HealthCheck>('/health/ready');
  return data;
}
