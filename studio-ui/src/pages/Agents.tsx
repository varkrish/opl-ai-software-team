import React, { useState, useCallback } from 'react';
import { Label, Spinner, Title, Select, SelectOption, MenuToggle, SelectList } from '@patternfly/react-core';
import {
  CubesIcon,
  PencilAltIcon,
  PaletteIcon,
  CogIcon,
  CodeIcon,
  DesktopIcon,
  OutlinedClockIcon,
} from '@patternfly/react-icons';
import { usePolling } from '../hooks/usePolling';
import { getJobs, getJobAgents } from '../api/client';
import type { Agent, JobSummary } from '../types';

/** Static agent definitions (fallback when no active job) */
const STATIC_AGENTS: Agent[] = [
  { name: 'Meta Agent', role: 'Orchestrator', model: 'deepseek-r1-distill-qwen-14b', status: 'idle', phase: 'meta', last_activity: null, last_activity_at: null },
  { name: 'Product Owner', role: 'Requirements', model: 'qwen3-14b', status: 'idle', phase: 'product_owner', last_activity: null, last_activity_at: null },
  { name: 'Designer', role: 'UX/UI', model: 'granite-3-2-8b-instruct', status: 'idle', phase: 'design', last_activity: null, last_activity_at: null },
  { name: 'Tech Architect', role: 'System Design', model: 'qwen3-14b', status: 'idle', phase: 'architecture', last_activity: null, last_activity_at: null },
  { name: 'Dev Crew', role: 'Implementation', model: 'qwen3-14b', status: 'idle', phase: 'development', last_activity: null, last_activity_at: null },
  { name: 'Frontend Crew', role: 'UI Implementation', model: 'granite-3-2-8b-instruct', status: 'idle', phase: 'frontend', last_activity: null, last_activity_at: null },
];

const agentIcons: Record<string, React.ReactNode> = {
  'Meta Agent': <CubesIcon />,
  'Product Owner': <PencilAltIcon />,
  'Designer': <PaletteIcon />,
  'Tech Architect': <CogIcon />,
  'Dev Crew': <CodeIcon />,
  'Frontend Crew': <DesktopIcon />,
};

const agentColors: Record<string, string> = {
  'Meta Agent': '#6753AC',
  'Product Owner': '#0066CC',
  'Designer': '#EC7A08',
  'Tech Architect': '#F0AB00',
  'Dev Crew': '#3E8635',
  'Frontend Crew': '#009596',
};

const statusLabelColor = (status: string): 'green' | 'blue' | 'grey' | 'orange' => {
  switch (status) {
    case 'working': return 'blue';
    case 'completed': return 'green';
    default: return 'grey';
  }
};

const Agents: React.FC = () => {
  const [agents, setAgents] = useState<Agent[]>(STATIC_AGENTS);
  const [loading, setLoading] = useState(true);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [jobSelectOpen, setJobSelectOpen] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const fetchedJobs = await getJobs();
      setJobs(fetchedJobs);
      
      // Auto-select job if none selected or if selected job no longer exists
      if (!selectedJobId || !fetchedJobs.find(j => j.id === selectedJobId)) {
        // Pick the most relevant job: running first, then most recent
        const activeJob = fetchedJobs.find((j: JobSummary) => j.status === 'running')
          || fetchedJobs.find((j: JobSummary) => j.status === 'completed')
          || fetchedJobs[0];
        
        if (activeJob) {
          setSelectedJobId(activeJob.id);
        }
      }
      
      // Fetch agents for selected job
      if (selectedJobId) {
        try {
          const liveAgents = await getJobAgents(selectedJobId);
          setAgents(liveAgents);
        } catch {
          setAgents(STATIC_AGENTS);
        }
      } else {
        setAgents(STATIC_AGENTS);
      }
    } catch {
      // API not available
    } finally {
      setLoading(false);
    }
  }, [selectedJobId]);

  usePolling(loadData, 3000);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '4rem' }}>
        <Spinner aria-label="Loading agents" />
      </div>
    );
  }

  return (
    <>
      <div style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
          <Title headingLevel="h1" size="2xl" style={{ fontFamily: '"Red Hat Display", sans-serif' }}>
            Crew Roster
          </Title>
          
          {/* Job Selector */}
          {jobs.length > 0 && (
            <Select
              isOpen={jobSelectOpen}
              selected={selectedJobId}
              onSelect={(_event, value) => {
                setSelectedJobId(value as string);
                setJobSelectOpen(false);
              }}
              onOpenChange={(isOpen) => setJobSelectOpen(isOpen)}
              toggle={(toggleRef) => (
                <MenuToggle
                  ref={toggleRef}
                  onClick={() => setJobSelectOpen(!jobSelectOpen)}
                  isExpanded={jobSelectOpen}
                  style={{ minWidth: '250px' }}
                >
                  {selectedJobId 
                    ? (() => {
                        const j = jobs.find(job => job.id === selectedJobId);
                        return j ? `${j.vision.substring(0, 30)}${j.vision.length > 30 ? '...' : ''}` : 'Select Job';
                      })()
                    : 'Select Job'}
                </MenuToggle>
              )}
            >
              <SelectList>
                {jobs.map((job) => {
                  const statusIcon = job.status === 'running' ? '🟢' : 
                                   job.status === 'completed' ? '✅' : 
                                   job.status === 'failed' ? '❌' : '⏸️';
                  return (
                    <SelectOption key={job.id} value={job.id}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span>{statusIcon}</span>
                        <span style={{ flex: 1 }}>{job.vision.substring(0, 40)}{job.vision.length > 40 ? '...' : ''}</span>
                      </div>
                    </SelectOption>
                  );
                })}
              </SelectList>
            </Select>
          )}
        </div>
        <p style={{ color: '#6A6E73', marginTop: '0.25rem' }}>
          Manage and monitor your specialized AI agents.
          {jobs.length > 1 && ` (${jobs.length} total jobs)`}
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem' }}>
        {agents.map((agent) => {
          const color = agentColors[agent.name] || '#6A6E73';
          const dotColor = agent.status === 'working' ? '#F0AB00' : agent.status === 'completed' ? '#3E8635' : '#6A6E73';

          return (
            <div
              key={agent.name}
              style={{
                borderLeft: `4px solid ${color}`,
                borderRadius: 8,
                backgroundColor: '#fff',
                boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              {/* ── Header row: icon + name/role + status ── */}
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '1rem 1rem 0.5rem' }}>
                <div
                  style={{
                    flexShrink: 0,
                    width: 40,
                    height: 40,
                    borderRadius: 8,
                    backgroundColor: `${color}20`,
                    color,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '1.25rem',
                  }}
                >
                  {agentIcons[agent.name]}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem', lineHeight: 1.3 }}>{agent.name}</div>
                  <div style={{ fontSize: '0.75rem', color: '#6A6E73' }}>{agent.role}</div>
                </div>
                <Label
                  color={statusLabelColor(agent.status)}
                  isCompact
                  style={{ flexShrink: 0, textTransform: 'uppercase', fontSize: '0.6rem', letterSpacing: '0.05em' }}
                >
                  {agent.status}
                </Label>
              </div>

              {/* ── Activity box ── */}
              <div style={{ padding: '0 1rem', flex: 1 }}>
                <div
                  style={{
                    backgroundColor: '#F5F5F5',
                    borderRadius: 6,
                    padding: '0.75rem',
                    fontSize: '0.75rem',
                    fontFamily: '"JetBrains Mono", monospace',
                    marginTop: '0.25rem',
                  }}
                >
                  <div style={{ color: '#6A6E73', marginBottom: '0.25rem', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <OutlinedClockIcon style={{ fontSize: '0.75rem' }} /> Latest Activity
                  </div>
                  <span style={{ color: '#151515' }}>
                    {agent.last_activity || 'Waiting for instructions...'}
                  </span>
                </div>
              </div>

              {/* ── Footer: model indicator ── */}
              <div
                style={{
                  borderTop: '1px solid #E8E8E8',
                  padding: '0.5rem 1rem',
                  marginTop: '0.75rem',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <span
                  style={{
                    display: 'inline-block',
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    backgroundColor: dotColor,
                  }}
                />
                <span style={{ fontSize: '0.75rem', color: '#6A6E73' }}>{agent.model}</span>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
};

export default Agents;
