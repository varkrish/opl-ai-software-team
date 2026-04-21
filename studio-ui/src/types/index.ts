/** Job status values from the Flask backend */
export type JobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'partially_completed'
  | 'failed'
  | 'cancelled'
  | 'quota_exhausted'
  | 'refinement_failed'
  | 'validation_failed';

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

/** External metadata attached to a job (e.g. Jira origin) */
export interface JobMetadata {
  jira_issue_key?: string;
  jira_base_url?: string;
  jira_issue_url?: string;
  [key: string]: unknown;
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
  metadata?: JobMetadata;
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
  metadata?: JobMetadata;
}

/** Paginated jobs list response from GET /api/jobs?page=&page_size= */
export interface JobsPageResponse {
  jobs: JobSummary[];
  total: number;
  page: number;
  page_size: number;
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

/** A single validation issue from GET /api/jobs/<id>/validation */
export interface ValidationIssue {
  id: string;
  job_id: string;
  check_name: string;
  severity: 'error' | 'warning';
  file_path: string | null;
  line_number: number | null;
  description: string;
  fix_strategy: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed';
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

/** Summary counts from GET /api/jobs/<id>/validation */
export interface ValidationSummary {
  total: number;
  fixed: number;
  failed: number;
  pending: number;
}

/** Full validation report response from GET /api/jobs/<id>/validation */
export interface ValidationReport {
  issues: ValidationIssue[];
  summary: ValidationSummary;
  overall: 'PASS' | 'ISSUES_FOUND';
}

/** Skill metadata from GET /api/skills */
export interface SkillInfo {
  name: string;
  description: string;
  tags: string[];
  file_count: number;
}

/** Semantic search result from POST /api/skills/query */
export interface SkillSearchResult {
  skill_name: string;
  content: string;
  tags: string[];
}

