/** Job status values from the Flask backend */
export type JobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'quota_exhausted'
  | 'refinement_failed';

/** Refinement record from GET /api/jobs/<id>/refinements */
export interface Refinement {
  id: string;
  job_id: string;
  prompt: string;
  file_path: string | null;
  status: 'running' | 'completed' | 'failed';
  created_at: string;
  completed_at: string | null;
  error: string | null;
}

/** A progress message emitted during job execution */
export interface ProgressMessage {
  timestamp: string;
  phase: string;
  message: string;
}

/** Full job record from GET /api/jobs/<id> */
export interface Job {
  id: string;
  vision: string;
  status: JobStatus;
  progress: number;
  current_phase: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  workspace_path: string;
  results: Record<string, unknown> | null;
  error: string | null;
  last_message: ProgressMessage[];
}

/** Summary job record from GET /api/jobs list */
export interface JobSummary {
  id: string;
  vision: string;
  status: JobStatus;
  progress: number;
  current_phase: string;
  created_at: string;
  completed_at: string | null;
}

/** Phase-level task record from GET /api/jobs/<id>/tasks */
export interface Task {
  task_id: string;
  phase: string;
  task_type: string;
  agent: string;
  description: string;
  status: string;
  subtasks_total: number;
  subtasks_completed: number;
  subtasks_in_progress: number;
  progress: number;
}

/** System statistics from GET /api/stats */
export interface Stats {
  total_jobs: number;
  completed: number;
  running: number;
  failed: number;
  quota_exhausted: number;
  queued: number;
}

/** Workspace file entry */
export interface WorkspaceFile {
  path: string;
  size: number;
  modified: string;
}

/** Health check response */
export interface HealthCheck {
  status: string;
  timestamp: string;
  service?: string;
  version?: string;
  checks?: Record<string, { status: string; message: string }>;
}

/** Agent definition for GET /api/jobs/<id>/agents */
export interface Agent {
  name: string;
  role: string;
  model: string;
  status: 'idle' | 'working' | 'completed';
  phase: string;
  last_activity: string | null;
  last_activity_at: string | null;
}

/** Job progress response */
export interface JobProgress {
  status: JobStatus;
  progress: number;
  current_phase: string;
  last_message: ProgressMessage[];
}

/** File tree node for the Files page */
export interface FileTreeNode {
  name: string;
  path: string;
  type: 'file' | 'folder';
  size?: number;
  children?: FileTreeNode[];
}

/** Kanban column for the Tasks page */
export interface KanbanColumn {
  id: string;
  title: string;
  tasks: Task[];
}

/** Agentic backend option for the Landing selector */
export interface BackendOption {
  name: string;
  display_name: string;
  available: boolean;
}

