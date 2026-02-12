import React, { useState, useRef, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  TextArea,
  TextInput,
  Alert,
  Spinner,
  Label,
  MenuToggle,
  Select,
  SelectList,
  SelectOption,
} from '@patternfly/react-core';
import {
  RocketIcon,
  CodeIcon,
  CubesIcon,
  LightbulbIcon,
  ArrowRightIcon,
  UploadIcon,
  TimesIcon,
  FileIcon,
  FileCodeIcon,
  FileAltIcon,
  GithubIcon,
  PlusCircleIcon,
} from '@patternfly/react-icons';
import { createJob, getBackends } from '../api/client';
import type { BackendOption } from '../types';
import BuildProgress from '../components/BuildProgress';

/* ── Constants ────────────────────────────────────────────────────────────── */
const ALLOWED_EXT = new Set([
  'txt','md','pdf','json','yaml','yml','csv','xml',
  'py','js','ts','java','go','rs','rb','sh',
  'html','css','sql','proto','graphql',
  'png','jpg','jpeg','svg',
  'doc','docx','pptx','xlsx',
]);

const GITHUB_URL_RE = /^https?:\/\/github\.com\/[\w.\-]+\/[\w.\-]+(\/.*)?$/;

function getFileIcon(name: string) {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  if (['py','js','ts','java','go','rs','rb','sh','sql','html','css'].includes(ext))
    return <FileCodeIcon style={{ color: '#4A90E2' }} />;
  if (['md','txt','pdf','doc','docx','pptx','xlsx'].includes(ext))
    return <FileAltIcon style={{ color: '#7B68EE' }} />;
  return <FileIcon style={{ color: '#6A6E73' }} />;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* ── Component ────────────────────────────────────────────────────────────── */
const Landing: React.FC = () => {
  const navigate = useNavigate();

  // Form state
  const [vision, setVision] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [githubUrls, setGithubUrls] = useState<string[]>([]);
  const [githubInput, setGithubInput] = useState('');
  const [githubError, setGithubError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Backend selection
  const [backends, setBackends] = useState<BackendOption[]>([]);
  const [selectedBackend, setSelectedBackend] = useState('opl-ai-team');
  const [backendSelectOpen, setBackendSelectOpen] = useState(false);

  // Build state — when set, we switch to split view
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [submittedVision, setSubmittedVision] = useState('');

  // Load available backends on mount
  useEffect(() => {
    getBackends()
      .then(setBackends)
      .catch((err) => {
        console.error('Failed to load backends:', err);
        // Fallback to OPL only
        setBackends([{ name: 'opl-ai-team', display_name: 'OPL AI Team', available: true }]);
      });
  }, []);

  /* ── File handling ──────────────────────────────────────────────────────── */
  const addFiles = useCallback((incoming: FileList | File[]) => {
    const arr = Array.from(incoming).filter((f) => {
      const ext = f.name.split('.').pop()?.toLowerCase() || '';
      return ALLOWED_EXT.has(ext) && f.size <= 10 * 1024 * 1024;
    });
    setFiles((prev) => {
      const names = new Set(prev.map((p) => p.name));
      const deduped = arr.filter((f) => !names.has(f.name));
      return [...prev, ...deduped].slice(0, 20);
    });
  }, []);

  const removeFile = (name: string) => setFiles((prev) => prev.filter((f) => f.name !== name));

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(e.type === 'dragenter' || e.type === 'dragover');
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  }, [addFiles]);

  /* ── GitHub URL handling ────────────────────────────────────────────────── */
  const addGithubUrl = () => {
    const url = githubInput.trim();
    if (!url) return;
    if (!GITHUB_URL_RE.test(url)) { setGithubError('Enter a valid GitHub URL'); return; }
    if (githubUrls.includes(url)) { setGithubError('Already added'); return; }
    if (githubUrls.length >= 5) { setGithubError('Max 5 repos'); return; }
    setGithubUrls((prev) => [...prev, url]);
    setGithubInput('');
    setGithubError(null);
  };

  const removeGithubUrl = (url: string) => setGithubUrls((prev) => prev.filter((u) => u !== url));

  const extractRepoName = (url: string) => {
    const parts = url.replace(/\/+$/, '').split('/');
    return parts.length >= 2 ? `${parts[parts.length - 2]}/${parts[parts.length - 1].split('/')[0]}` : url;
  };

  /* ── Submit ─────────────────────────────────────────────────────────────── */
  const handleCreateProject = async () => {
    if (!vision.trim()) { setError('Please describe your project vision'); return; }
    setCreating(true);
    setError(null);
    try {
      const result = await createJob(
        vision,
        files.length > 0 ? files : undefined,
        githubUrls.length > 0 ? githubUrls : undefined,
        selectedBackend,
      );
      setSubmittedVision(vision);
      setActiveJobId(result.job_id);
    } catch (err) {
      setError('Failed to create project. Please try again.');
      console.error('Error creating job:', err);
    } finally {
      setCreating(false);
    }
  };

  const handleNewProject = () => {
    setActiveJobId(null);
    setSubmittedVision('');
    setVision('');
    setFiles([]);
    setGithubUrls([]);
  };

  const examplePrompts = [
    'Build a REST API for a task management system',
    'Create a React dashboard with real-time charts',
    'Develop a CLI tool for data processing',
    'Build a microservice with WebSocket support',
  ];

  const contextCount = files.length + githubUrls.length;
  const isBuildMode = activeJobId !== null;

  /* ═══════════════════════════════════════════════════════════════════════ */
  /* BUILD MODE: split panel — chat left, progress right                   */
  /* ═══════════════════════════════════════════════════════════════════════ */
  if (isBuildMode) {
    return (
      <div style={{
        minHeight: '100vh', background: '#F5F5F5',
        display: 'flex', flexDirection: 'column',
      }}>
        {/* Top bar */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.75rem 1.5rem', background: 'white',
          borderBottom: '1px solid #E0E0E0',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <img src="/redhat-logo.svg" alt="Red Hat" style={{ height: '18px' }}
              onError={(e) => { e.currentTarget.style.display = 'none'; }} />
            <span style={{
              fontSize: '0.875rem', fontWeight: 600, color: '#151515',
              fontFamily: '"Red Hat Display", sans-serif',
            }}>
              AI Development Studio
            </span>
          </div>
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <Button variant="link" size="sm" onClick={handleNewProject}
              style={{ fontSize: '0.8125rem' }}>
              + New Project
            </Button>
            <Button variant="link" size="sm" onClick={() => navigate('/dashboard')}
              icon={<ArrowRightIcon />} iconPosition="end"
              style={{ fontSize: '0.8125rem', color: '#6A6E73' }}>
              Dashboard
            </Button>
          </div>
        </div>

        {/* Split panels */}
        <div style={{
          flex: 1, display: 'flex', overflow: 'hidden',
        }}>
          {/* ── LEFT PANEL: Chat / prompt ────────────────────────────────── */}
          <div style={{
            width: '420px', minWidth: '360px',
            display: 'flex', flexDirection: 'column',
            background: 'white', borderRight: '1px solid #E0E0E0',
          }}>
            {/* Submitted prompt */}
            <div style={{
              flex: 1, overflowY: 'auto', padding: '1.5rem',
            }}>
              {/* User message */}
              <div style={{ marginBottom: '1.25rem' }}>
                <div style={{
                  display: 'flex', alignItems: 'center', gap: '0.5rem',
                  marginBottom: '0.5rem',
                }}>
                  <div style={{
                    width: '28px', height: '28px', borderRadius: '50%',
                    background: 'linear-gradient(135deg, #4A90E2 0%, #357ABD 100%)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: 'white', fontSize: '0.75rem', fontWeight: 700,
                  }}>
                    U
                  </div>
                  <span style={{ fontSize: '0.8125rem', fontWeight: 600, color: '#151515' }}>You</span>
                </div>
                <div style={{
                  background: '#F0F7FF', borderRadius: '12px',
                  padding: '1rem', fontSize: '0.875rem',
                  color: '#151515', lineHeight: 1.6,
                  whiteSpace: 'pre-wrap',
                  borderTopLeftRadius: '4px',
                }}>
                  {submittedVision}
                </div>

                {/* Context attachments */}
                {(files.length > 0 || githubUrls.length > 0) && (
                  <div style={{
                    display: 'flex', flexWrap: 'wrap', gap: '0.375rem',
                    marginTop: '0.5rem',
                  }}>
                    {githubUrls.map((url) => (
                      <span key={url} style={{
                        display: 'inline-flex', alignItems: 'center', gap: '0.25rem',
                        background: '#F0F7FF', border: '1px solid #BEE1F4',
                        borderRadius: '6px', padding: '0.2rem 0.5rem',
                        fontSize: '0.6875rem', color: '#0066CC',
                      }}>
                        <GithubIcon style={{ fontSize: '10px' }} /> {extractRepoName(url)}
                      </span>
                    ))}
                    {files.map((f) => (
                      <span key={f.name} style={{
                        display: 'inline-flex', alignItems: 'center', gap: '0.25rem',
                        background: '#F0F0F0', borderRadius: '6px',
                        padding: '0.2rem 0.5rem', fontSize: '0.6875rem', color: '#151515',
                      }}>
                        <FileIcon style={{ fontSize: '10px' }} /> {f.name}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* AI response */}
              <div>
                <div style={{
                  display: 'flex', alignItems: 'center', gap: '0.5rem',
                  marginBottom: '0.5rem',
                }}>
                  <div style={{
                    width: '28px', height: '28px', borderRadius: '50%',
                    background: 'linear-gradient(135deg, #EE0000 0%, #B00 100%)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: 'white', fontSize: '0.65rem', fontWeight: 700,
                  }}>
                    AI
                  </div>
                  <span style={{ fontSize: '0.8125rem', fontWeight: 600, color: '#151515' }}>AI Crew</span>
                </div>
                <div style={{
                  background: '#FAFAFA', borderRadius: '12px',
                  padding: '1rem', fontSize: '0.875rem',
                  color: '#151515', lineHeight: 1.6,
                  borderTopLeftRadius: '4px',
                }}>
                  Got it! I'm assembling the crew and starting to build your project.
                  You can see the real-time progress on the right panel.

                  <div style={{
                    marginTop: '0.75rem', padding: '0.75rem',
                    background: 'white', borderRadius: '8px',
                    border: '1px solid #E0E0E0', fontSize: '0.8125rem',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <CubesIcon style={{ color: '#4A90E2' }} />
                      <span style={{ fontWeight: 600 }}>6 AI Agents</span>
                      <span style={{ color: '#6A6E73' }}>are working on your project</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Bottom: new prompt input (future follow-up) */}
            <div style={{
              padding: '1rem 1.5rem', borderTop: '1px solid #F0F0F0',
              background: '#FAFAFA',
            }}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: '0.5rem',
                background: 'white', border: '1px solid #D2D2D2',
                borderRadius: '10px', padding: '0.5rem 0.75rem',
                opacity: 0.5, cursor: 'not-allowed',
              }}>
                <input
                  disabled
                  placeholder="Follow-up messages coming soon..."
                  style={{
                    flex: 1, border: 'none', background: 'transparent',
                    fontSize: '0.8125rem', outline: 'none', cursor: 'not-allowed',
                    fontFamily: '"Red Hat Text", sans-serif',
                  }}
                />
              </div>
            </div>
          </div>

          {/* ── RIGHT PANEL: Build Progress ──────────────────────────────── */}
          <div style={{
            flex: 1, overflowY: 'auto', padding: '1.5rem 2rem',
          }}>
            <BuildProgress jobId={activeJobId!} vision={submittedVision} />
          </div>
        </div>
      </div>
    );
  }

  /* ═══════════════════════════════════════════════════════════════════════ */
  /* IDLE MODE: side-by-side — left: prompt input, right: features (100vh) */
  /* ═══════════════════════════════════════════════════════════════════════ */
  return (
    <div style={{
      height: '100vh', overflow: 'hidden',
      display: 'flex',
    }}>
      {/* Animations for right pane */}
      <style>{`
        @keyframes landingFadeInRight {
          from { opacity: 0; transform: translateX(16px); }
          to { opacity: 1; transform: translateX(0); }
        }
        @keyframes landingFadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes landingIconPulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.05); }
        }
        .landing-right-panel { animation: landingFadeInRight 0.5s ease-out forwards; }
        .landing-right-row { opacity: 0; animation: landingFadeInRight 0.4s ease-out forwards; }
        .landing-left-hero { animation: landingFadeIn 0.4s ease-out; }
        .landing-right-icon:hover { transform: scale(1.08); }
      `}</style>

      {/* ── LEFT: white panel — prompt input ───────────────────────────────── */}
      <div style={{
        flex: '1 1 75%', display: 'flex', flexDirection: 'column',
        background: '#FAFAFA', overflow: 'auto',
      }}>
        {/* Top bar */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.75rem 1.5rem', borderBottom: '1px solid #E7E7E7',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <img src="/redhat-logo.svg" alt="Red Hat" style={{ height: '20px' }}
              onError={(e) => { e.currentTarget.style.display = 'none'; }} />
            <span style={{ fontSize: '0.9375rem', fontWeight: 700, color: '#151515', fontFamily: '"Red Hat Display", sans-serif' }}>
              AI Crew
            </span>
          </div>
          <Button variant="link" onClick={() => navigate('/dashboard')}
            style={{ color: '#72767B', fontSize: '0.8125rem' }}
            icon={<ArrowRightIcon />} iconPosition="end">
            Dashboard
          </Button>
        </div>

        {/* Main content — vertically centered */}
        <div className="landing-left-hero" style={{
          flex: 1, display: 'flex', flexDirection: 'column',
          justifyContent: 'center', padding: '2rem 2.5rem',
          maxWidth: '640px', width: '100%', margin: '0 auto',
        }}>
          <h1 style={{
            fontSize: '2.25rem', fontWeight: 700,
            fontFamily: '"Red Hat Display", sans-serif',
            color: '#151515', marginBottom: '0.5rem', lineHeight: 1.15,
          }}>
            Describe it.{' '}
            <span style={{
              background: 'linear-gradient(135deg, #EE0000, #CC0000)',
              WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}>
              We build it.
            </span>
          </h1>
          <p style={{ fontSize: '0.9375rem', color: '#72767B', marginBottom: '1.25rem', lineHeight: 1.5 }}>
            From idea to production-ready code, powered by 6 AI agents.
          </p>

          {error && (
            <Alert variant="danger" title={error} style={{ marginBottom: '1rem' }} isInline isPlain
              actionClose={<Button variant="plain" onClick={() => setError(null)}>×</Button>} />
          )}

          {/* Input card */}
          <div style={{
            background: 'white', borderRadius: '16px', padding: '1.25rem',
            boxShadow: '0 2px 16px rgba(0,0,0,0.06)', border: '1px solid #E7E7E7',
          }}>
            <TextArea
              value={vision}
              onChange={(_e, v) => setVision(v)}
              placeholder="Describe your project vision..."
              style={{
                minHeight: '120px', fontSize: '0.9375rem',
                fontFamily: '"Red Hat Text", sans-serif',
                border: 'none', padding: '0', resize: 'none',
                lineHeight: 1.6, color: '#151515',
              }}
              aria-label="Project description"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleCreateProject();
              }}
            />

            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              marginTop: '0.75rem', paddingTop: '0.75rem',
              borderTop: '1px solid #F0F0F0',
            }}>
              <Select
                toggle={(toggleRef) => (
                  <MenuToggle ref={toggleRef}
                    onClick={() => setBackendSelectOpen(!backendSelectOpen)}
                    isExpanded={backendSelectOpen}
                    style={{ fontSize: '0.8125rem', padding: '0.3rem 0.75rem', minWidth: '150px', border: '1px solid #D2D2D2', borderRadius: '8px' }}
                  >
                    {backends.find((b) => b.name === selectedBackend)?.display_name || 'OPL AI Team'}
                  </MenuToggle>
                )}
                onSelect={(_e, s) => { setSelectedBackend(s as string); setBackendSelectOpen(false); }}
                selected={selectedBackend}
                isOpen={backendSelectOpen}
                onOpenChange={setBackendSelectOpen}
                aria-label="Select agentic system"
              >
                <SelectList>
                  {backends.map((b) => (
                    <SelectOption key={b.name} value={b.name} isDisabled={!b.available}>
                      {b.display_name}{!b.available && <span style={{ color: '#8A8D90', fontSize: '0.7rem', marginLeft: '0.4rem' }}>(N/A)</span>}
                    </SelectOption>
                  ))}
                </SelectList>
              </Select>
              <Button variant="primary" onClick={handleCreateProject}
                isLoading={creating} isDisabled={!vision.trim() || creating}
                style={{
                  backgroundColor: '#EE0000', border: 'none', fontWeight: 600,
                  padding: '0.5rem 1.75rem', fontSize: '0.875rem', borderRadius: '10px', color: 'white',
                }}
                icon={creating ? <Spinner size="sm" /> : <RocketIcon />} iconPosition="end">
                {creating ? 'Creating...' : 'Start Building'}
              </Button>
            </div>
          </div>

          {/* Example prompt pills */}
          <div style={{ marginTop: '0.875rem', display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
            {examplePrompts.map((p) => (
              <button key={p} onClick={() => setVision(p)} style={{
                background: 'rgba(238,0,0,0.04)', border: '1px solid rgba(238,0,0,0.12)',
                borderRadius: '999px', padding: '0.3rem 0.75rem',
                fontSize: '0.75rem', color: '#CC0000', cursor: 'pointer',
                fontFamily: '"Red Hat Text", sans-serif', transition: 'all 0.15s', lineHeight: 1.4,
              }}
              onMouseOver={(e) => { e.currentTarget.style.background = 'rgba(238,0,0,0.10)'; }}
              onMouseOut={(e) => { e.currentTarget.style.background = 'rgba(238,0,0,0.04)'; }}
              >
                {p}
              </button>
            ))}
          </div>

          {/* Context attachments — compact */}
          <details style={{
            background: 'white', borderRadius: '10px', border: '1px solid #E7E7E7',
            marginTop: '1rem',
          }}>
            <summary style={{
              padding: '0.625rem 1rem', cursor: 'pointer',
              fontSize: '0.8125rem', fontWeight: 600, color: '#151515',
              listStyle: 'none', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <span>+ Add Context (GitHub repos, files)</span>
              {contextCount > 0 && (
                <span style={{ background: '#EE0000', color: 'white', borderRadius: '12px', padding: '0.1rem 0.45rem', fontSize: '0.7rem', fontWeight: 600 }}>{contextCount}</span>
              )}
            </summary>
            <div style={{ padding: '0 1rem 1rem' }}>
              <div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.5rem' }}>
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '0.4rem', background: '#FAFAFA', border: '1px solid #D2D2D2', borderRadius: '8px', padding: '0.1rem 0.6rem' }}>
                  <GithubIcon style={{ color: '#151515', fontSize: '0.8rem', flexShrink: 0 }} />
                  <TextInput value={githubInput} onChange={(_e, v) => { setGithubInput(v); setGithubError(null); }}
                    placeholder="https://github.com/user/repo" aria-label="GitHub URL"
                    style={{ border: 'none', background: 'transparent', fontSize: '0.8125rem', padding: '0.35rem 0' }}
                    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addGithubUrl(); } }} />
                </div>
                <Button variant="secondary" size="sm" onClick={addGithubUrl} isDisabled={!githubInput.trim()} icon={<PlusCircleIcon />}>Add</Button>
              </div>
              {githubError && <span style={{ fontSize: '0.7rem', color: '#C9190B', display: 'block', marginBottom: '0.4rem' }}>{githubError}</span>}
              {githubUrls.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem', marginBottom: '0.5rem' }}>
                  {githubUrls.map((url) => (
                    <span key={url} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', background: '#F0F7FF', border: '1px solid #BEE1F4', borderRadius: '6px', padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}>
                      <GithubIcon style={{ fontSize: '0.7rem', color: '#0066CC' }} />{extractRepoName(url)}
                      <button onClick={() => removeGithubUrl(url)} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '1px', display: 'flex', color: '#6A6E73' }}><TimesIcon style={{ fontSize: '0.65rem' }} /></button>
                    </span>
                  ))}
                </div>
              )}
              <div onDragEnter={handleDrag} onDragLeave={handleDrag} onDragOver={handleDrag} onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                style={{ padding: '0.6rem', border: `2px dashed ${dragActive ? '#EE0000' : '#D2D2D2'}`, borderRadius: '8px', background: dragActive ? 'rgba(238,0,0,0.02)' : '#FAFAFA', cursor: 'pointer', textAlign: 'center' }}>
                <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }}
                  accept={Array.from(ALLOWED_EXT).map((e) => `.${e}`).join(',')}
                  onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ''; }} />
                <UploadIcon style={{ marginRight: '0.4rem', color: '#72767B', fontSize: '0.8rem' }} />
                <span style={{ fontSize: '0.8125rem', color: '#72767B' }}>Drop files or click</span>
              </div>
              {files.length > 0 && (
                <div style={{ marginTop: '0.5rem', display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
                  {files.map((f) => (
                    <span key={f.name} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', background: '#F5F5F5', borderRadius: '6px', padding: '0.2rem 0.5rem', fontSize: '0.75rem', border: '1px solid #E7E7E7' }}>
                      {getFileIcon(f.name)}{f.name} <span style={{ color: '#8A8D90', fontSize: '0.65rem' }}>{formatSize(f.size)}</span>
                      <button onClick={(e) => { e.stopPropagation(); removeFile(f.name); }} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '1px', display: 'flex', color: '#6A6E73' }}><TimesIcon style={{ fontSize: '0.65rem' }} /></button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </details>

          <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: '#8A8D90' }}>
            {contextCount > 0 ? `${contextCount} reference${contextCount > 1 ? 's' : ''} attached  ·  ` : ''}
            ⌘+Enter to submit
          </div>
        </div>
      </div>

      {/* ── RIGHT: dark panel — features (25%) with animations ──────────────── */}
      <div
        className="landing-right-panel"
        style={{
          flex: '0 0 25%', minWidth: '240px', maxWidth: '320px',
          background: 'linear-gradient(160deg, #1A1A1A 0%, #0C0C0C 100%)',
          display: 'flex', flexDirection: 'column', justifyContent: 'center',
          padding: '1.5rem 1.25rem',
          borderLeft: '3px solid #EE0000',
          overflow: 'auto',
        }}
      >
        <h2 style={{
          fontSize: '1.25rem', fontWeight: 700, color: '#FFFFFF',
          fontFamily: '"Red Hat Display", sans-serif',
          marginBottom: '0.25rem', letterSpacing: '-0.02em',
        }}>
          Ship <span style={{ color: '#EE0000' }}>faster</span> with AI
        </h2>
        <p style={{ fontSize: '0.8125rem', color: 'rgba(255,255,255,0.45)', marginBottom: '1rem', lineHeight: 1.45 }}>
          A full dev crew — architecture to tests — no boilerplate.
        </p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
          {[
            { icon: <CubesIcon />,     gradient: 'linear-gradient(135deg, #4A90E2, #357ABD)', title: 'Multi-Agent Crew',   desc: '6 AI agents: PM, Architect, Dev, QA, Frontend, DevOps.' },
            { icon: <CodeIcon />,       gradient: 'linear-gradient(135deg, #A855F7, #7C3AED)', title: 'Production Ready',   desc: 'TDD, code review, security checks — tested & deployable.' },
            { icon: <RocketIcon />,     gradient: 'linear-gradient(135deg, #EE0000, #CC0000)', title: 'Lightning Fast',     desc: 'Idea to prototype in minutes with real-time tracking.' },
            { icon: <LightbulbIcon />,  gradient: 'linear-gradient(135deg, #F59E0B, #D97706)', title: 'Prompt-to-Refine',   desc: 'Describe edits in English — add, delete, restructure.' },
            { icon: <GithubIcon />,     gradient: 'linear-gradient(135deg, #6EE7B7, #10B981)', title: 'Context-Aware',      desc: 'Attach repos & docs — no hallucinated APIs.' },
            { icon: <ArrowRightIcon />, gradient: 'linear-gradient(135deg, #38BDF8, #0EA5E9)', title: 'Pluggable LLMs',     desc: 'Red Hat MaaS, OpenRouter, Ollama — swap from the UI.' },
          ].map((c, i) => (
            <div
              key={c.title}
              className="landing-right-row"
              style={{
                display: 'flex', alignItems: 'flex-start', gap: '0.6rem',
                padding: '0.6rem 0.75rem', borderRadius: '10px',
                background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
                transition: 'transform 0.2s ease, background 0.2s, border-color 0.2s',
                animationDelay: `${0.1 + i * 0.06}s`,
              } as React.CSSProperties}
              onMouseOver={(e) => {
                e.currentTarget.style.background = 'rgba(255,255,255,0.07)';
                e.currentTarget.style.borderColor = 'rgba(238,0,0,0.3)';
                e.currentTarget.style.transform = 'translateX(4px)';
              }}
              onMouseOut={(e) => {
                e.currentTarget.style.background = 'rgba(255,255,255,0.03)';
                e.currentTarget.style.borderColor = 'rgba(255,255,255,0.06)';
                e.currentTarget.style.transform = 'translateX(0)';
              }}
            >
              <div
                className="landing-right-icon"
                style={{
                  width: '32px', height: '32px', borderRadius: '8px',
                  background: c.gradient, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: 'white', fontSize: '0.9rem', flexShrink: 0,
                  transition: 'transform 0.25s ease',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.transform = 'scale(1.08)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.transform = 'scale(1)'; }}
              >
                {c.icon}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: '0.8125rem', fontWeight: 700, color: '#FFFFFF', fontFamily: '"Red Hat Display", sans-serif', marginBottom: '0.1rem' }}>{c.title}</div>
                <div style={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.45)', lineHeight: 1.4 }}>{c.desc}</div>
              </div>
            </div>
          ))}
        </div>

        <p style={{
          marginTop: '1rem', fontSize: '0.7rem',
          color: 'rgba(255,255,255,0.2)',
          fontFamily: '"Red Hat Text", sans-serif',
        }}>
          Built with LlamaIndex &middot; Powered by Red Hat &middot; Open Source
        </p>
      </div>
    </div>
  );
};

export default Landing;
