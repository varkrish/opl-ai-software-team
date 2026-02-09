import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Progress,
  Spinner,
} from '@patternfly/react-core';
import {
  CheckCircleIcon,
  ExclamationCircleIcon,
  ArrowRightIcon,
  CodeIcon,
  CubesIcon,
  OutlinedClockIcon,
} from '@patternfly/react-icons';
import { getJob, getJobFiles } from '../api/client';
import type { Job, WorkspaceFile, ProgressMessage } from '../types';

/* ── Phase metadata ───────────────────────────────────────────────────────── */
const PHASE_META: Record<string, { label: string; icon: string; color: string }> = {
  queued:           { label: 'Queued',             icon: '⏳', color: '#6A6E73' },
  fetching_context: { label: 'Fetching context',   icon: '📥', color: '#4A90E2' },
  initializing:     { label: 'Initializing',       icon: '⚙️', color: '#6A6E73' },
  meta:             { label: 'Meta Agent',          icon: '🧠', color: '#7B68EE' },
  product_owner:    { label: 'Product Owner',       icon: '📋', color: '#0066CC' },
  designer:         { label: 'Designer',            icon: '🎨', color: '#EE0000' },
  tech_architect:   { label: 'Tech Architect',      icon: '🏗️', color: '#F0AB00' },
  development:      { label: 'Development',         icon: '💻', color: '#3E8635' },
  frontend:         { label: 'Frontend',            icon: '🖥️', color: '#4A90E2' },
  completed:        { label: 'Completed',           icon: '✅', color: '#3E8635' },
  error:            { label: 'Error',               icon: '❌', color: '#C9190B' },
};

const PHASE_ORDER = [
  'queued', 'fetching_context', 'initializing', 'meta',
  'product_owner', 'designer', 'tech_architect', 'development', 'frontend', 'completed',
];

/* ── Internal file filter (same as fileTree.ts) ──────────────────────────── */
const HIDDEN = [
  /^tasks_.*\.db$/, /^state_.*\.json$/, /^crew_errors\.log$/,
  /^agent_prompts\.json$/, /^repomix-.*\.xml$/, /^\..*/,
  /^__pycache__$/, /^node_modules$/, /^venv$/, /^\.venv$/, /^docs$/,
];
function isVisible(path: string) {
  return !path.split('/').some((seg) => HIDDEN.some((r) => r.test(seg)));
}

interface Props {
  jobId: string;
  vision: string;
}

const BuildProgress: React.FC<Props> = ({ jobId, vision }) => {
  const navigate = useNavigate();
  const [job, setJob] = useState<Job | null>(null);
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [seenMessages, setSeenMessages] = useState<ProgressMessage[]>([]);
  const logEndRef = useRef<HTMLDivElement>(null);
  const prevMsgCount = useRef(0);

  const isTerminal = job?.status === 'completed' || job?.status === 'failed' || job?.status === 'cancelled';

  /* ── Polling ───────────────────────────────────────────────────────────── */
  const poll = useCallback(async () => {
    try {
      const [j, f] = await Promise.all([
        getJob(jobId),
        getJobFiles(jobId).catch(() => [] as WorkspaceFile[]),
      ]);
      setJob(j);
      setFiles(f.filter((fi) => isVisible(fi.path)));

      // Append new messages
      if (j.last_message && j.last_message.length > prevMsgCount.current) {
        setSeenMessages((prev) => {
          const newMsgs = j.last_message.slice(prevMsgCount.current);
          prevMsgCount.current = j.last_message.length;
          return [...prev, ...newMsgs];
        });
      }
    } catch {
      /* ignore transient errors */
    }
  }, [jobId]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 2500);
    return () => clearInterval(id);
  }, [poll]);

  // Auto-scroll log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [seenMessages]);

  /* ── Phase timeline ────────────────────────────────────────────────────── */
  const currentPhaseIdx = PHASE_ORDER.indexOf(job?.current_phase || 'queued');

  const renderTimeline = () => (
    <div style={{ marginBottom: '1.5rem' }}>
      {PHASE_ORDER.filter((p) => p !== 'queued' && p !== 'fetching_context').map((phase, i) => {
        const meta = PHASE_META[phase] || { label: phase, icon: '🔹', color: '#6A6E73' };
        const phaseIdx = PHASE_ORDER.indexOf(phase);
        const isActive = phaseIdx === currentPhaseIdx;
        const isDone = phaseIdx < currentPhaseIdx || job?.status === 'completed';
        const isPending = phaseIdx > currentPhaseIdx && job?.status !== 'completed';

        return (
          <div key={phase} style={{
            display: 'flex', alignItems: 'center', gap: '0.75rem',
            padding: '0.5rem 0',
            opacity: isPending ? 0.35 : 1,
          }}>
            {/* Status dot */}
            <div style={{
              width: '28px', height: '28px', borderRadius: '50%',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '0.75rem', flexShrink: 0,
              background: isDone ? '#3E8635' : isActive ? meta.color : '#E0E0E0',
              color: isDone || isActive ? 'white' : '#6A6E73',
              transition: 'all 0.3s',
            }}>
              {isDone ? <CheckCircleIcon style={{ fontSize: '14px' }} /> :
               isActive ? <Spinner size="sm" style={{ '--pf-v5-c-spinner--Color': 'white' } as React.CSSProperties} /> :
               <span style={{ fontSize: '0.7rem' }}>{i + 1}</span>}
            </div>

            {/* Label */}
            <div style={{ flex: 1 }}>
              <div style={{
                fontSize: '0.8125rem',
                fontWeight: isActive ? 700 : isDone ? 500 : 400,
                color: isActive ? meta.color : isDone ? '#151515' : '#6A6E73',
                fontFamily: '"Red Hat Text", sans-serif',
              }}>
                {meta.icon} {meta.label}
              </div>
            </div>

            {isActive && (
              <span style={{
                fontSize: '0.6875rem', color: meta.color,
                background: `${meta.color}15`, padding: '2px 8px',
                borderRadius: '10px', fontWeight: 600,
              }}>
                active
              </span>
            )}
          </div>
        );
      })}
    </div>
  );

  /* ── Activity log ──────────────────────────────────────────────────────── */
  const renderLog = () => (
    <div style={{
      background: '#1E1E1E', borderRadius: '10px', padding: '1rem',
      fontFamily: '"Red Hat Mono", monospace', fontSize: '0.75rem',
      color: '#D4D4D4', maxHeight: '220px', overflowY: 'auto',
      lineHeight: 1.6,
    }}>
      <div style={{ color: '#6A9955', marginBottom: '0.5rem' }}>
        {'// '}AI Crew Activity
      </div>
      {seenMessages.length === 0 && (
        <div style={{ color: '#6A6E73' }}>Waiting for activity...</div>
      )}
      {seenMessages.map((msg, i) => {
        const phase = PHASE_META[msg.phase];
        return (
          <div key={i} style={{ marginBottom: '0.25rem' }}>
            <span style={{ color: '#569CD6' }}>
              [{new Date(msg.timestamp).toLocaleTimeString()}]
            </span>{' '}
            <span style={{ color: phase?.color || '#D4D4D4' }}>
              {phase?.icon || '>'} {msg.phase}
            </span>{' '}
            <span style={{ color: '#CE9178' }}>{msg.message}</span>
          </div>
        );
      })}

      {/* Live indicator */}
      {!isTerminal && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.5rem' }}>
          <span style={{
            width: '6px', height: '6px', borderRadius: '50%', background: '#3E8635',
            animation: 'pulse 1.5s infinite',
          }} />
          <span style={{ color: '#6A9955' }}>
            {job?.current_phase ? `${PHASE_META[job.current_phase]?.label || job.current_phase} in progress...` : 'Starting...'}
          </span>
        </div>
      )}
      <div ref={logEndRef} />
    </div>
  );

  /* ── Generated files ───────────────────────────────────────────────────── */
  const renderFiles = () => {
    if (files.length === 0) return null;
    return (
      <div style={{ marginTop: '1rem' }}>
        <div style={{
          fontSize: '0.75rem', fontWeight: 600, color: '#6A6E73',
          textTransform: 'uppercase', letterSpacing: '0.05em',
          marginBottom: '0.5rem',
        }}>
          Generated Files ({files.length})
        </div>
        <div style={{
          maxHeight: '160px', overflowY: 'auto', background: '#FAFAFA',
          borderRadius: '8px', padding: '0.5rem',
        }}>
          {files.map((f) => (
            <div key={f.path} style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              padding: '0.25rem 0.5rem', fontSize: '0.75rem',
              fontFamily: '"Red Hat Mono", monospace', color: '#151515',
            }}>
              <CodeIcon style={{ fontSize: '12px', color: '#4A90E2', flexShrink: 0 }} />
              <span style={{
                flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {f.path}
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  };

  /* ── Terminal states ───────────────────────────────────────────────────── */
  const renderCompleted = () => (
    <div style={{
      textAlign: 'center', padding: '1.5rem',
      background: '#F3FAF3', borderRadius: '12px',
      border: '1px solid #C8E6C9', marginTop: '1rem',
    }}>
      <CheckCircleIcon style={{ fontSize: '2.5rem', color: '#3E8635', marginBottom: '0.75rem' }} />
      <div style={{
        fontSize: '1.125rem', fontWeight: 700, color: '#151515',
        fontFamily: '"Red Hat Display", sans-serif', marginBottom: '0.5rem',
      }}>
        Project Built Successfully
      </div>
      <p style={{ fontSize: '0.875rem', color: '#6A6E73', marginBottom: '1rem' }}>
        {files.length} files generated. View details in the dashboard.
      </p>
      <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
        <Button variant="primary" onClick={() => navigate('/dashboard')}
          style={{ backgroundColor: '#3E8635', border: 'none' }}
          icon={<CubesIcon />}>
          Open Dashboard
        </Button>
        <Button variant="secondary" onClick={() => navigate('/files')}
          icon={<CodeIcon />}>
          View Files
        </Button>
      </div>
    </div>
  );

  const renderFailed = () => (
    <div style={{
      textAlign: 'center', padding: '1.5rem',
      background: '#FFF5F5', borderRadius: '12px',
      border: '1px solid #FECDD3', marginTop: '1rem',
    }}>
      <ExclamationCircleIcon style={{ fontSize: '2.5rem', color: '#C9190B', marginBottom: '0.75rem' }} />
      <div style={{
        fontSize: '1.125rem', fontWeight: 700, color: '#151515',
        fontFamily: '"Red Hat Display", sans-serif', marginBottom: '0.5rem',
      }}>
        Build Failed
      </div>
      <p style={{ fontSize: '0.8125rem', color: '#6A6E73', marginBottom: '0.5rem' }}>
        {job?.error || 'An unexpected error occurred'}
      </p>
      <Button variant="secondary" onClick={() => navigate('/dashboard')}>
        View Details
      </Button>
    </div>
  );

  /* ── Main render ───────────────────────────────────────────────────────── */
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      fontFamily: '"Red Hat Text", sans-serif',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: '1.25rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {!isTerminal && (
            <div style={{
              width: '10px', height: '10px', borderRadius: '50%',
              background: '#3E8635', boxShadow: '0 0 8px rgba(62, 134, 53, 0.5)',
              animation: 'pulse 1.5s infinite',
            }} />
          )}
          <span style={{ fontSize: '1rem', fontWeight: 700, color: '#151515' }}>
            {isTerminal
              ? (job?.status === 'completed' ? 'Build Complete' : 'Build Stopped')
              : 'Building...'}
          </span>
        </div>
        <Button variant="link" size="sm"
          onClick={() => navigate('/dashboard')}
          icon={<ArrowRightIcon />} iconPosition="end"
          style={{ fontSize: '0.8125rem', color: '#6A6E73' }}>
          Dashboard
        </Button>
      </div>

      {/* Overall progress */}
      <Progress
        value={job?.progress || 0}
        title=""
        size="sm"
        style={{ marginBottom: '1.25rem' }}
        variant={job?.status === 'failed' ? 'danger' : job?.status === 'completed' ? 'success' : undefined}
      />

      {/* Phase timeline */}
      {renderTimeline()}

      {/* Activity log */}
      {renderLog()}

      {/* Files */}
      {renderFiles()}

      {/* Terminal states */}
      {job?.status === 'completed' && renderCompleted()}
      {(job?.status === 'failed' || job?.status === 'cancelled') && renderFailed()}

      {/* Pulse animation */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
};

export default BuildProgress;
