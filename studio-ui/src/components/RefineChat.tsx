/**
 * RefineChat — Floating AI refinement button + slide-out chat panel.
 *
 * Features:
 * - "Ask Red Hat"-style floating pill button (sparkle icon, gradient glow)
 * - Slide-out chat panel from the right (like Cursor / Copilot chat)
 * - Shows original job vision as system welcome message
 * - Auto-loads all past refinement history as chat messages
 * - Rich system feedback during refinements (progress steps)
 */
import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  TextArea,
  Label,
  Spinner,
  Alert,
} from '@patternfly/react-core';
import { TimesIcon } from '@patternfly/react-icons';
import {
  refineJob,
  getJobProgress,
  getRefinementHistory,
} from '../api/client';
import type { Refinement } from '../types';
import axios from 'axios';

/* ─── Props ──────────────────────────────────────────────────────────── */

interface RefineChatProps {
  selectedJobId: string | null;
  selectedFile: { path: string; content: string } | null;
  /** The original vision/prompt used to create the selected job */
  jobVision: string | null;
  /** Called after a successful refinement so Files can reload tree / content */
  onRefineComplete?: () => void;
}

/* ─── Constants ──────────────────────────────────────────────────────── */

const POLL_MS = 2000;
const TIMEOUT_MS = 300_000; // 5 min

/* ─── Chat message type ──────────────────────────────────────────────── */

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  text: string;
  status?: 'running' | 'completed' | 'failed' | 'info';
  timestamp: Date;
}

/* ─── Sparkle SVG icon ────────────────────────────────────────────────── */

const SparkleIcon: React.FC<{ size?: number; color?: string }> = ({ size = 20, color = '#fff' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path
      d="M12 2L13.09 8.26L18 6L14.74 10.91L21 12L14.74 13.09L18 18L13.09 15.74L12 22L10.91 15.74L6 18L9.26 13.09L3 12L9.26 10.91L6 6L10.91 8.26L12 2Z"
      fill={color}
    />
  </svg>
);

/* ─── Animated typing dots ────────────────────────────────────────────── */

const TypingDots: React.FC = () => (
  <span style={{ display: 'inline-flex', gap: 3, alignItems: 'center' }}>
    {[0, 1, 2].map((i) => (
      <span
        key={i}
        style={{
          width: 5,
          height: 5,
          borderRadius: '50%',
          backgroundColor: '#6A6E73',
          animation: `typingDot 1.2s ease-in-out ${i * 0.2}s infinite`,
        }}
      />
    ))}
    <style>{`
      @keyframes typingDot {
        0%, 60%, 100% { opacity: 0.3; transform: scale(0.8); }
        30% { opacity: 1; transform: scale(1.2); }
      }
    `}</style>
  </span>
);

/* ─── System message bubble ───────────────────────────────────────────── */

const SystemBubble: React.FC<{ children: React.ReactNode; variant?: 'info' | 'vision' }> = ({ children, variant = 'info' }) => (
  <div
    style={{
      display: 'flex',
      justifyContent: 'center',
      margin: '4px 0',
    }}
  >
    <div
      style={{
        maxWidth: '95%',
        padding: variant === 'vision' ? '14px 18px' : '8px 14px',
        borderRadius: 12,
        backgroundColor: variant === 'vision' ? '#F0F4FF' : '#F5F5F5',
        border: variant === 'vision' ? '1px solid #D6E4FF' : '1px solid #E8E8E8',
        fontSize: variant === 'vision' ? '0.85rem' : '0.78rem',
        lineHeight: 1.5,
        color: variant === 'vision' ? '#151515' : '#6A6E73',
        fontFamily: '"Red Hat Text", sans-serif',
        textAlign: 'center',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}
    >
      {children}
    </div>
  </div>
);

/* ─── Component ─────────────────────────────────────────────────────── */

const RefineChat: React.FC<RefineChatProps> = ({
  selectedJobId,
  selectedFile,
  jobVision,
  onRefineComplete,
}) => {
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isRefining, setIsRefining] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  /* ── Build initial messages: vision + history ─────────────────────── */
  useEffect(() => {
    if (!selectedJobId) {
      setMessages([]);
      setHistoryLoaded(false);
      return;
    }

    (async () => {
      const msgs: ChatMessage[] = [];

      // 1. Original vision as system welcome
      if (jobVision) {
        msgs.push({
          id: 'sys-vision',
          role: 'system',
          text: jobVision,
          status: 'info',
          timestamp: new Date(),
        });
      }

      // 2. Load refinement history
      try {
        const history = await getRefinementHistory(selectedJobId);

        if (history.length > 0) {
          msgs.push({
            id: 'sys-history-divider',
            role: 'system',
            text: `${history.length} previous refinement${history.length > 1 ? 's' : ''} loaded`,
            status: 'info',
            timestamp: new Date(),
          });

          // Show history oldest → newest
          history.reverse().forEach((r: Refinement) => {
            // User prompt
            msgs.push({
              id: `${r.id}-user`,
              role: 'user',
              text: r.prompt,
              timestamp: new Date(r.created_at),
            });

            // AI response with rich feedback
            let responseText: string;
            let status: 'completed' | 'failed' | 'running';
            if (r.status === 'completed') {
              responseText = r.file_path
                ? `Done! I've updated **${r.file_path}** based on your instructions. The changes have been applied to the workspace.`
                : 'Done! I\'ve applied the refinements across the project. The workspace files have been updated.';
              status = 'completed';
            } else if (r.status === 'failed') {
              responseText = `I wasn't able to complete this refinement${r.error ? ': ' + r.error : '. Please try again with a different prompt.'}`;
              status = 'failed';
            } else {
              responseText = 'Refinement is still in progress...';
              status = 'running';
            }

            msgs.push({
              id: `${r.id}-ai`,
              role: 'assistant',
              text: responseText,
              status,
              timestamp: new Date(r.completed_at || r.created_at),
            });
          });
        } else {
          // No history — show a helpful getting-started message
          msgs.push({
            id: 'sys-welcome',
            role: 'system',
            text: 'No refinements yet. Describe a change below and I\'ll update the code for you.',
            status: 'info',
            timestamp: new Date(),
          });
        }
      } catch {
        msgs.push({
          id: 'sys-welcome',
          role: 'system',
          text: 'Ready to refine. Describe what you\'d like to change below.',
          status: 'info',
          timestamp: new Date(),
        });
      }

      setMessages(msgs);
      setHistoryLoaded(true);
    })();
  }, [selectedJobId, jobVision]);

  /* ── Auto-scroll to bottom ──────────────────────────────────────── */
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  /* ── Focus input when panel opens ───────────────────────────────── */
  useEffect(() => {
    if (open && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 200);
    }
  }, [open]);

  /* ── Close on Escape ────────────────────────────────────────────── */
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  /* ── Add a system feedback message ──────────────────────────────── */
  const addSystemMsg = useCallback((text: string) => {
    setMessages((prev) => [
      ...prev,
      {
        id: `sys-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        role: 'system',
        text,
        status: 'info',
        timestamp: new Date(),
      },
    ]);
  }, []);

  /* ── Send refinement ────────────────────────────────────────────── */
  const handleSend = useCallback(async () => {
    if (!selectedJobId || !prompt.trim() || isRefining) return;

    const trimmedPrompt = prompt.trim();
    const scopePath = selectedFile?.path;

    // User message
    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      text: trimmedPrompt,
      timestamp: new Date(),
    };

    // Scope system message
    const scopeInfo: ChatMessage = {
      id: `sys-scope-${Date.now()}`,
      role: 'system',
      text: scopePath
        ? `Targeting: ${scopePath}`
        : 'Targeting: entire project',
      status: 'info',
      timestamp: new Date(),
    };

    // AI working message
    const aiMsgId = `a-${Date.now()}`;
    const aiMsg: ChatMessage = {
      id: aiMsgId,
      role: 'assistant',
      text: 'Analyzing your request and preparing changes...',
      status: 'running',
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMsg, scopeInfo, aiMsg]);
    setPrompt('');
    setError(null);
    setIsRefining(true);

    try {
      await refineJob(selectedJobId, trimmedPrompt, scopePath);

      // Progress feedback messages
      const progressSteps = [
        { delay: 3000, text: 'Reading project context and tech stack...' },
        { delay: 8000, text: 'Generating code changes...' },
        { delay: 15000, text: 'Writing updated files to workspace...' },
      ];

      let stepIdx = 0;
      const deadline = Date.now() + TIMEOUT_MS;

      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const progress = await getJobProgress(selectedJobId);

        // Show next progress step if enough time has passed
        if (stepIdx < progressSteps.length) {
          const step = progressSteps[stepIdx];
          if (Date.now() - aiMsg.timestamp.getTime() > step.delay) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === aiMsgId ? { ...m, text: step.text } : m
              )
            );
            stepIdx++;
          }
        }

        if (progress.current_phase !== 'refining') {
          // Check if it failed
          if (progress.current_phase === 'refinement_failed') {
            // last_message is an array of {timestamp, phase, message} objects
            const msgs = Array.isArray(progress.last_message) ? progress.last_message : [];
            const lastEntry = msgs.length > 0 ? msgs[msgs.length - 1] : null;
            const failMsg = (lastEntry && typeof lastEntry === 'object' && lastEntry.message)
              ? lastEntry.message
              : 'The AI agent completed but did not modify any files. Try being more specific.';
            setMessages((prev) =>
              prev.map((m) =>
                m.id === aiMsgId
                  ? { ...m, text: failMsg, status: 'failed' as const, timestamp: new Date() }
                  : m
              )
            );
            const isServiceError = /503|502|429|temporarily unavailable|rate limit|quota|timed out/i.test(failMsg);
            if (isServiceError) {
              addSystemMsg('You can try again in a few minutes once the service is back.');
            } else {
              addSystemMsg('You can try rephrasing your request or selecting a different file scope.');
            }
            setError(failMsg);
            setIsRefining(false);
            return;
          }
          break;
        }
      }

      // Success - only reached when phase is not 'refinement_failed'
      const successText = scopePath
        ? `Done! I've updated **${scopePath}**. The file viewer will refresh to show the changes.`
        : 'Done! The refinement has been applied across the project. Files will refresh automatically.';

      setMessages((prev) =>
        prev.map((m) =>
          m.id === aiMsgId
            ? { ...m, text: successText, status: 'completed' as const, timestamp: new Date() }
            : m
        )
      );

      // System feedback
      addSystemMsg('Tip: Select a specific file in the tree to scope your next refinement to that file only.');

      onRefineComplete?.();
    } catch (err: unknown) {
      const is409 = axios.isAxiosError(err) && err.response?.status === 409;
      const errText = is409
        ? 'A refinement is already in progress. Please wait for it to finish before starting another.'
        : err instanceof Error
        ? err.message
        : 'Something went wrong during refinement.';

      setMessages((prev) =>
        prev.map((m) =>
          m.id === aiMsgId
            ? { ...m, text: errText, status: 'failed' as const, timestamp: new Date() }
            : m
        )
      );

      if (!is409) {
        addSystemMsg('You can try rephrasing your request or selecting a different file scope.');
      }

      setError(errText);
    } finally {
      setIsRefining(false);
    }
  }, [selectedJobId, prompt, isRefining, selectedFile, onRefineComplete, addSystemMsg]);

  /* ── Handle Enter to send (Shift+Enter for newline) ─────────────── */
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  /* ── Scope label ────────────────────────────────────────────────── */
  const scopeLabel = selectedFile ? selectedFile.path : 'whole project';

  /* ── Render a single message ────────────────────────────────────── */
  const renderMessage = (msg: ChatMessage) => {
    if (msg.role === 'system') {
      // Special rendering for the vision message
      if (msg.id === 'sys-vision') {
        return (
          <SystemBubble key={msg.id} variant="vision">
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center', marginBottom: 6 }}>
              <SparkleIcon size={14} color="#0066CC" />
              <span style={{ fontWeight: 600, color: '#0066CC', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Original Vision
              </span>
            </div>
            <div style={{ fontStyle: 'italic' }}>"{msg.text}"</div>
          </SystemBubble>
        );
      }
      // Generic system messages (dividers, tips, scope info)
      return <SystemBubble key={msg.id}>{msg.text}</SystemBubble>;
    }

    // User & assistant messages
    return (
      <div
        key={msg.id}
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
        }}
      >
        {/* Role label */}
        <span
          style={{
            fontSize: '0.7rem',
            fontWeight: 600,
            color: msg.role === 'user' ? '#6A6E73' : '#0066CC',
            marginBottom: 3,
            fontFamily: '"Red Hat Text", sans-serif',
            textTransform: 'uppercase',
            letterSpacing: '0.03em',
          }}
        >
          {msg.role === 'user' ? 'You' : 'AI'}
        </span>
        <div
          style={{
            maxWidth: '88%',
            padding: '12px 16px',
            borderRadius: msg.role === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
            backgroundColor:
              msg.role === 'user'
                ? undefined
                : msg.status === 'failed'
                ? '#FFF5F5'
                : msg.status === 'running'
                ? '#F0F4FF'
                : msg.status === 'completed'
                ? '#F0FFF0'
                : '#F5F5F5',
            background:
              msg.role === 'user'
                ? 'linear-gradient(135deg, #1a1a2e, #16213e)'
                : undefined,
            color: msg.role === 'user' ? '#fff' : '#151515',
            fontSize: '0.875rem',
            lineHeight: 1.55,
            fontFamily: '"Red Hat Text", sans-serif',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
          }}
        >
          {msg.role === 'assistant' && msg.status === 'running' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <Spinner size="sm" />
              <Label isCompact color="blue">refining</Label>
            </div>
          )}
          {msg.role === 'assistant' && msg.status === 'completed' && (
            <div style={{ marginBottom: 6 }}>
              <Label isCompact color="green">completed</Label>
            </div>
          )}
          {msg.role === 'assistant' && msg.status === 'failed' && (
            <div style={{ marginBottom: 6 }}>
              <Label isCompact color="red">failed</Label>
            </div>
          )}
          {msg.text}
          {msg.role === 'assistant' && msg.status === 'running' && (
            <div style={{ marginTop: 8 }}>
              <TypingDots />
            </div>
          )}
        </div>
        <span
          style={{
            fontSize: '0.68rem',
            color: '#6A6E73',
            marginTop: 3,
            fontFamily: '"Red Hat Mono", monospace',
          }}
        >
          {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>
    );
  };

  /* ── Render ──────────────────────────────────────────────────────── */
  return (
    <>
      {/* ── Floating button ─────────────────────────────────────────── */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label="Refine with AI"
          style={{
            position: 'fixed',
            bottom: 32,
            right: 32,
            zIndex: 9998,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '14px 28px 14px 22px',
            border: 'none',
            borderRadius: 999,
            cursor: 'pointer',
            fontFamily: '"Red Hat Display", "Red Hat Text", sans-serif',
            fontSize: '1rem',
            fontWeight: 600,
            color: '#fff',
            background: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)',
            boxShadow: `
              0 0 0 2px transparent,
              0 0 0 3px rgba(0,0,0,0.05),
              0 8px 32px rgba(0, 0, 0, 0.35),
              inset 0 1px 0 rgba(255,255,255,0.05)
            `,
            backgroundClip: 'padding-box',
            outline: '2.5px solid transparent',
            outlineOffset: '2px',
            transition: 'transform 0.2s ease, box-shadow 0.2s ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'translateY(-2px) scale(1.03)';
            e.currentTarget.style.boxShadow = `
              0 0 24px rgba(204, 0, 0, 0.35),
              0 0 48px rgba(128, 0, 128, 0.2),
              0 12px 40px rgba(0, 0, 0, 0.4),
              inset 0 1px 0 rgba(255,255,255,0.08)
            `;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'translateY(0) scale(1)';
            e.currentTarget.style.boxShadow = `
              0 0 0 2px transparent,
              0 0 0 3px rgba(0,0,0,0.05),
              0 8px 32px rgba(0, 0, 0, 0.35),
              inset 0 1px 0 rgba(255,255,255,0.05)
            `;
          }}
        >
          {/* Gradient border ring */}
          <span
            aria-hidden
            style={{
              position: 'absolute',
              inset: -3,
              borderRadius: 999,
              padding: 2.5,
              background: 'linear-gradient(135deg, #CC0000, #A30000, #7B2D8B, #4A1A6B)',
              WebkitMask: 'linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)',
              WebkitMaskComposite: 'xor',
              maskComposite: 'exclude',
              pointerEvents: 'none',
            }}
          />
          <SparkleIcon size={22} color="#fff" />
          <span>Refine</span>
        </button>
      )}

      {/* ── Backdrop ────────────────────────────────────────────────── */}
      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 9998,
            backgroundColor: 'rgba(0, 0, 0, 0.25)',
            backdropFilter: 'blur(2px)',
            transition: 'opacity 0.2s ease',
          }}
        />
      )}

      {/* ── Slide-out panel ─────────────────────────────────────────── */}
      <div
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          bottom: 0,
          width: 440,
          maxWidth: '100vw',
          zIndex: 9999,
          transform: open ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: '#FAFAFA',
          boxShadow: open ? '-8px 0 40px rgba(0, 0, 0, 0.15)' : 'none',
        }}
      >
        {/* ── Header ──────────────────────────────────────────────── */}
        <div
          style={{
            padding: '20px 24px 16px',
            background: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0d1117 100%)',
            borderBottom: '3px solid transparent',
            backgroundClip: 'padding-box',
            position: 'relative',
          }}
        >
          {/* Gradient accent line at bottom */}
          <div
            style={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              height: 3,
              background: 'linear-gradient(90deg, #CC0000, #A30000, #7B2D8B, #4A1A6B)',
            }}
          />
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <SparkleIcon size={24} color="#fff" />
              <span
                style={{
                  color: '#fff',
                  fontFamily: '"Red Hat Display", sans-serif',
                  fontSize: '1.125rem',
                  fontWeight: 700,
                  letterSpacing: '-0.01em',
                }}
              >
                Refine with AI
              </span>
            </div>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close refine panel"
              style={{
                background: 'rgba(255,255,255,0.1)',
                border: 'none',
                borderRadius: 8,
                padding: 6,
                cursor: 'pointer',
                color: 'rgba(255,255,255,0.7)',
                display: 'flex',
                transition: 'background 0.15s, color 0.15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'rgba(255,255,255,0.2)';
                e.currentTarget.style.color = '#fff';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'rgba(255,255,255,0.1)';
                e.currentTarget.style.color = 'rgba(255,255,255,0.7)';
              }}
            >
              <TimesIcon />
            </button>
          </div>
          {/* Scope chip */}
          {selectedJobId && (
            <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span
                style={{
                  fontSize: '0.75rem',
                  color: 'rgba(255,255,255,0.5)',
                  fontFamily: '"Red Hat Mono", monospace',
                  background: 'rgba(255,255,255,0.08)',
                  padding: '3px 10px',
                  borderRadius: 12,
                }}
              >
                {scopeLabel}
              </span>
            </div>
          )}
        </div>

        {/* ── Chat messages area ──────────────────────────────────── */}
        <div
          ref={scrollRef}
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '16px 20px',
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
          }}
        >
          {!selectedJobId ? (
            <div
              style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                textAlign: 'center',
                padding: '2rem',
              }}
            >
              <div>
                <SparkleIcon size={40} color="#D2D2D2" />
                <p
                  style={{
                    marginTop: 16,
                    color: '#6A6E73',
                    fontSize: '0.9rem',
                    lineHeight: 1.6,
                    fontFamily: '"Red Hat Text", sans-serif',
                  }}
                >
                  Select a job from the dropdown above to start refining your generated code.
                </p>
              </div>
            </div>
          ) : !historyLoaded ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{ textAlign: 'center' }}>
                <Spinner size="lg" />
                <p style={{ marginTop: 12, color: '#6A6E73', fontSize: '0.85rem' }}>Loading conversation...</p>
              </div>
            </div>
          ) : (
            messages.map(renderMessage)
          )}
        </div>

        {/* ── Input area ──────────────────────────────────────────── */}
        {selectedJobId && (
          <div
            style={{
              padding: '16px 20px 20px',
              borderTop: '1px solid #E8E8E8',
              backgroundColor: '#fff',
            }}
          >
            {error && (
              <Alert
                variant="danger"
                title={error}
                isInline
                isPlain
                style={{ marginBottom: 8, fontSize: '0.8rem' }}
                actionClose={
                  <button
                    onClick={() => setError(null)}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#6A6E73' }}
                  >
                    <TimesIcon />
                  </button>
                }
              />
            )}
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
              <div style={{ flex: 1, position: 'relative' }}>
                <TextArea
                  ref={inputRef}
                  value={prompt}
                  onChange={(_e, v) => setPrompt(v)}
                  onKeyDown={handleKeyDown}
                  placeholder="Describe the change you want..."
                  rows={2}
                  isDisabled={isRefining}
                  style={{
                    borderRadius: 12,
                    fontSize: '0.875rem',
                    fontFamily: '"Red Hat Text", sans-serif',
                    resize: 'none',
                  }}
                />
              </div>
              <button
                onClick={handleSend}
                disabled={!prompt.trim() || isRefining}
                aria-label="Send refinement"
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: 12,
                  border: 'none',
                  cursor: !prompt.trim() || isRefining ? 'not-allowed' : 'pointer',
                  opacity: !prompt.trim() || isRefining ? 0.4 : 1,
                  background: 'linear-gradient(135deg, #CC0000, #A30000)',
                  color: '#fff',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                  transition: 'opacity 0.15s, transform 0.15s',
                }}
                onMouseEnter={(e) => {
                  if (prompt.trim() && !isRefining) e.currentTarget.style.transform = 'scale(1.08)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = 'scale(1)';
                }}
              >
                {isRefining ? (
                  <Spinner size="sm" />
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
                  </svg>
                )}
              </button>
            </div>
            <div style={{ marginTop: 6, fontSize: '0.7rem', color: '#A3A3A3', fontFamily: '"Red Hat Text", sans-serif' }}>
              Enter to send &middot; Shift+Enter for new line &middot; Esc to close
            </div>
          </div>
        )}
      </div>
    </>
  );
};

export default RefineChat;
