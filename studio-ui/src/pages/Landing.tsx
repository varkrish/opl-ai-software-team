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
  /* IDLE MODE: centered input (original landing page)                     */
  /* ═══════════════════════════════════════════════════════════════════════ */
  return (
    <div style={{
      minHeight: '100vh',
      background: 'linear-gradient(180deg, #F0F0F0 0%, #FAFAFA 100%)',
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Hero */}
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '3rem 2rem',
      }}>
        <div style={{ maxWidth: '900px', width: '100%' }}>
          {/* Heading */}
          <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
            <h1 style={{
              fontSize: '3rem', fontWeight: 700,
              fontFamily: '"Red Hat Display", sans-serif',
              color: '#151515', marginBottom: '0.75rem', lineHeight: 1.1,
            }}>
              Describe it.{' '}
              <span style={{
                background: 'linear-gradient(135deg, #EE0000 0%, #CC0000 100%)',
                WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
                backgroundClip: 'text',
              }}>
                We'll build it.
              </span>
            </h1>

            <p style={{
              fontSize: '1.125rem', color: '#72767B', maxWidth: '560px',
              margin: '0 auto', lineHeight: 1.5, fontWeight: 400,
            }}>
              AI-powered software development. From idea to production-ready code.
            </p>
          </div>

          {/* Error */}
          {error && (
            <Alert variant="danger" title={error} style={{ marginBottom: '1.5rem' }} isInline
              actionClose={<Button variant="plain" onClick={() => setError(null)}>×</Button>} />
          )}

          {/* Main Input Card */}
          <div style={{
            background: 'white', borderRadius: '20px', padding: '2rem',
            boxShadow: '0 4px 24px rgba(0,0,0,0.06)', border: '1px solid #E7E7E7',
          }}>
            <TextArea
              value={vision}
              onChange={(_e, v) => setVision(v)}
              placeholder="Describe your project vision in plain English..."
              style={{
                minHeight: '180px', fontSize: '1.0625rem',
                fontFamily: '"Red Hat Text", sans-serif',
                border: 'none', padding: '0', resize: 'vertical',
                lineHeight: 1.6, color: '#151515',
              }}
              aria-label="Project description"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleCreateProject();
              }}
            />

            {/* Backend selector - right below text area */}
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              marginTop: '1.5rem', paddingTop: '1.5rem',
              borderTop: '1px solid #F0F0F0',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span style={{ fontSize: '0.875rem', color: '#72767B', fontWeight: 500 }}>
                  Agentic Framework:
                </span>
                <Select
                  toggle={(toggleRef) => (
                    <MenuToggle
                      ref={toggleRef}
                      onClick={() => setBackendSelectOpen(!backendSelectOpen)}
                      isExpanded={backendSelectOpen}
                      style={{
                        fontSize: '0.875rem',
                        padding: '0.375rem 0.875rem',
                        minWidth: '160px',
                        border: '1px solid #D2D2D2',
                        borderRadius: '8px',
                      }}
                    >
                      {backends.find((b) => b.name === selectedBackend)?.display_name || 'OPL AI Team'}
                    </MenuToggle>
                  )}
                  onSelect={(_event, selection) => {
                    setSelectedBackend(selection as string);
                    setBackendSelectOpen(false);
                  }}
                  selected={selectedBackend}
                  isOpen={backendSelectOpen}
                  onOpenChange={(isOpen) => setBackendSelectOpen(isOpen)}
                  aria-label="Select agentic system"
                >
                  <SelectList>
                    {backends.map((backend) => (
                      <SelectOption
                        key={backend.name}
                        value={backend.name}
                        isDisabled={!backend.available}
                      >
                        {backend.display_name}
                        {!backend.available && (
                          <span style={{ color: '#8A8D90', fontSize: '0.75rem', marginLeft: '0.5rem' }}>
                            (not installed)
                          </span>
                        )}
                      </SelectOption>
                    ))}
                  </SelectList>
                </Select>
              </div>

              <Button variant="primary" size="lg" onClick={handleCreateProject}
                isLoading={creating} isDisabled={!vision.trim() || creating}
                style={{
                  backgroundColor: '#EE0000', border: 'none',
                  fontWeight: 600, padding: '0.625rem 2rem',
                  fontSize: '0.9375rem', borderRadius: '10px',
                  color: 'white',
                }}
                icon={creating ? <Spinner size="sm" /> : <RocketIcon />} iconPosition="end">
                {creating ? 'Creating...' : 'Start Building'}
              </Button>
            </div>
          </div>

          {/* Quick links and context hint */}
          <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            marginTop: '1rem', padding: '0 0.25rem',
          }}>
            <span style={{ fontSize: '0.8125rem', color: '#8A8D90' }}>
              {contextCount > 0
                ? `${contextCount} reference${contextCount > 1 ? 's' : ''} attached`
                : 'Tip: ⌘+Enter to submit'}
            </span>
            <Button variant="link" onClick={() => navigate('/dashboard')}
              style={{ color: '#72767B', fontSize: '0.875rem', padding: '0.375rem 0.75rem' }}
              icon={<ArrowRightIcon />} iconPosition="end">
              View Past Projects
            </Button>
          </div>

          {/* Collapsible Context Section */}
          <details style={{
            background: 'white', borderRadius: '12px',
            boxShadow: '0 2px 12px rgba(0,0,0,0.04)',
            border: '1px solid #E7E7E7', marginTop: '1.5rem',
          }}>
            <summary style={{
              padding: '1rem 1.25rem', cursor: 'pointer',
              fontSize: '0.875rem', fontWeight: 600, color: '#151515',
              listStyle: 'none', display: 'flex', alignItems: 'center',
              justifyContent: 'space-between',
            }}>
              <span>+ Add Reference Context (optional)</span>
              {contextCount > 0 && (
                <span style={{
                  background: '#EE0000', color: 'white',
                  borderRadius: '12px', padding: '0.125rem 0.5rem',
                  fontSize: '0.75rem', fontWeight: 600,
                }}>{contextCount}</span>
              )}
            </summary>

            <div style={{ padding: '0 1.25rem 1.25rem' }}>

              {/* GitHub URL input */}
              <div style={{
                display: 'flex', gap: '0.5rem', alignItems: 'flex-start',
                marginBottom: '0.75rem',
              }}>
                <div style={{ flex: 1 }}>
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: '0.5rem',
                    background: '#FAFAFA', border: '1px solid #D2D2D2',
                    borderRadius: '8px', padding: '0.125rem 0.75rem',
                  }}>
                    <GithubIcon style={{ color: '#151515', flexShrink: 0, fontSize: '0.875rem' }} />
                    <TextInput
                      value={githubInput}
                      onChange={(_e, v) => { setGithubInput(v); setGithubError(null); }}
                      placeholder="https://github.com/user/repo"
                      aria-label="GitHub repository URL"
                      style={{
                        border: 'none', background: 'transparent',
                        fontSize: '0.875rem', padding: '0.5rem 0',
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') { e.preventDefault(); addGithubUrl(); }
                      }}
                    />
                  </div>
                  {githubError && (
                    <span style={{ fontSize: '0.75rem', color: '#C9190B', marginTop: '0.25rem', display: 'block' }}>
                      {githubError}
                    </span>
                  )}
                </div>
                <Button variant="secondary" onClick={addGithubUrl}
                  isDisabled={!githubInput.trim()} style={{ whiteSpace: 'nowrap' }}
                  icon={<PlusCircleIcon />}>
                  Add
                </Button>
              </div>

              {/* GitHub URLs list */}
              {githubUrls.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.875rem' }}>
                  {githubUrls.map((url) => (
                    <div key={url} style={{
                      display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
                      background: '#F0F7FF', border: '1px solid #BEE1F4',
                      borderRadius: '8px', padding: '0.375rem 0.75rem',
                      fontSize: '0.8125rem', color: '#151515',
                    }}>
                      <GithubIcon style={{ color: '#0066CC', fontSize: '0.875rem' }} />
                      <span style={{ maxWidth: '280px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {extractRepoName(url)}
                      </span>
                      <button onClick={() => removeGithubUrl(url)}
                        style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px', display: 'flex', color: '#6A6E73' }}
                        aria-label={`Remove ${url}`}>
                        <TimesIcon />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* File upload */}
              <div
                onDragEnter={handleDrag} onDragLeave={handleDrag}
                onDragOver={handleDrag} onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                style={{
                  padding: '1rem',
                  border: `2px dashed ${dragActive ? '#EE0000' : '#D2D2D2'}`,
                  borderRadius: '10px',
                  background: dragActive ? 'rgba(238,0,0,0.02)' : '#FAFAFA',
                  cursor: 'pointer', transition: 'all 0.2s', textAlign: 'center',
                }}
              >
                <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }}
                  accept={Array.from(ALLOWED_EXT).map((e) => `.${e}`).join(',')}
                  onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ''; }} />
                <UploadIcon style={{ marginRight: '0.5rem', color: '#72767B' }} />
                <span style={{ fontSize: '0.875rem', color: '#72767B' }}>
                  Drag files or click to upload
                </span>
                <span style={{ display: 'block', fontSize: '0.75rem', color: '#A0A0A0', marginTop: '0.25rem' }}>
                  Docs, specs, code files — up to 10 MB each
                </span>
              </div>

              {/* Attached files */}
              {files.length > 0 && (
                <div style={{ marginTop: '0.75rem', display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                  {files.map((f) => (
                    <div key={f.name} style={{
                      display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
                      background: '#F5F5F5', borderRadius: '8px',
                      padding: '0.375rem 0.75rem', fontSize: '0.8125rem', color: '#151515',
                      border: '1px solid #E7E7E7',
                    }}>
                      {getFileIcon(f.name)}
                      <span style={{ maxWidth: '180px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {f.name}
                      </span>
                      <span style={{ fontSize: '0.6875rem', color: '#8A8D90' }}>{formatSize(f.size)}</span>
                      <button onClick={(e) => { e.stopPropagation(); removeFile(f.name); }}
                        style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px', display: 'flex', color: '#6A6E73' }}
                        aria-label={`Remove ${f.name}`}>
                        <TimesIcon />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </details>
        </div>
      </div>

      {/* Features */}
      <div style={{ background: 'white', borderTop: '1px solid #E0E0E0', padding: '3rem 2rem' }}>
        <div style={{ maxWidth: '1200px', margin: '0 auto' }}>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '2rem',
          }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{
                width: '64px', height: '64px',
                background: 'linear-gradient(135deg, #4A90E2 0%, #357ABD 100%)',
                borderRadius: '16px', display: 'flex', alignItems: 'center',
                justifyContent: 'center', margin: '0 auto 1rem',
              }}><CubesIcon color="white" /></div>
              <h3 style={{ fontSize: '1.125rem', fontWeight: 600, color: '#151515', marginBottom: '0.5rem', fontFamily: '"Red Hat Display", sans-serif' }}>
                Multi-Agent Crew
              </h3>
              <p style={{ fontSize: '0.875rem', color: '#6A6E73', lineHeight: 1.6 }}>
                6 specialized AI agents work together — from planning to deployment
              </p>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{
                width: '64px', height: '64px',
                background: 'linear-gradient(135deg, #7B68EE 0%, #6A5ACD 100%)',
                borderRadius: '16px', display: 'flex', alignItems: 'center',
                justifyContent: 'center', margin: '0 auto 1rem',
              }}><CodeIcon color="white" /></div>
              <h3 style={{ fontSize: '1.125rem', fontWeight: 600, color: '#151515', marginBottom: '0.5rem', fontFamily: '"Red Hat Display", sans-serif' }}>
                Production Ready
              </h3>
              <p style={{ fontSize: '0.875rem', color: '#6A6E73', lineHeight: 1.6 }}>
                Generate clean, tested, documented code with best practices
              </p>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{
                width: '64px', height: '64px',
                background: 'linear-gradient(135deg, #50C878 0%, #3E8635 100%)',
                borderRadius: '16px', display: 'flex', alignItems: 'center',
                justifyContent: 'center', margin: '0 auto 1rem',
              }}><RocketIcon color="white" /></div>
              <h3 style={{ fontSize: '1.125rem', fontWeight: 600, color: '#151515', marginBottom: '0.5rem', fontFamily: '"Red Hat Display", sans-serif' }}>
                Lightning Fast
              </h3>
              <p style={{ fontSize: '0.875rem', color: '#6A6E73', lineHeight: 1.6 }}>
                From idea to working prototype in minutes, not hours
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Landing;
