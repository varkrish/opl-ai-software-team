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

// ── Health ───────────────────────────────────────────────────────────────────
export async function getHealth(): Promise<HealthCheck> {
  const { data } = await api.get<HealthCheck>('/health');
  return data;
}

export async function getHealthReady(): Promise<HealthCheck> {
  const { data } = await api.get<HealthCheck>('/health/ready');
  return data;
}
