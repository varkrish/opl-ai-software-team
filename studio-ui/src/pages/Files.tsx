import React, { useState, useCallback } from 'react';
import {
  Alert,
  Button,
  Card,
  CardTitle,
  CardBody,
  CardHeader,
  Title,
  TreeView,
  TreeViewDataItem,
  EmptyState,
  EmptyStateIcon,
  EmptyStateBody,
  Spinner,
  Split,
  SplitItem,
  Select,
  SelectOption,
  MenuToggle,
  MenuToggleElement,
  SelectList,
  Flex,
  FlexItem,
  Label,
} from '@patternfly/react-core';
import {
  FolderIcon,
  FolderOpenIcon,
  FileIcon,
  FileCodeIcon,
  CubeIcon,
  DownloadIcon,
} from '@patternfly/react-icons';
import { useSearchParams } from 'react-router-dom';
import Editor, { type Monaco } from '@monaco-editor/react';
import { usePolling } from '../hooks/usePolling';
import {
  getJobs,
  getWorkspaceFiles,
  getFileContent,
  getPreviewUrl,
  downloadJobWorkspace,
} from '../api/client';
import { buildFileTree } from '../utils/fileTree';
import type { JobSummary, FileTreeNode } from '../types';
import RefineChat from '../components/RefineChat';

/** Red Hat brand colors for Monaco theme */
const REDHAT = {
  red: '#CC0000',
  redDark: '#A30000',
  text: '#151515',
  textMuted: '#6A6E73',
  border: '#D2D2D2',
  bg: '#FFFFFF',
  bgSubtle: '#F5F5F5',
  accent: '#0066CC',
};

/** Map file extension to Monaco language id */
function getLanguage(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, string> = {
    js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
    py: 'python', json: 'json', md: 'markdown', html: 'html', htm: 'html',
    css: 'css', scss: 'scss', yaml: 'yaml', yml: 'yaml', xml: 'xml',
    sh: 'shell', bash: 'shell', env: 'plaintext',
  };
  return map[ext] || 'plaintext';
}

function defineRedHatTheme(monaco: Monaco): void {
  monaco.editor.defineTheme('redhat-light', {
    base: 'vs',
    inherit: true,
    rules: [
      { token: 'keyword', foreground: REDHAT.redDark, fontStyle: 'bold' },
      { token: 'string', foreground: '#006600' },
      { token: 'comment', foreground: REDHAT.textMuted, fontStyle: 'italic' },
      { token: 'number', foreground: REDHAT.accent },
    ],
    colors: {
      'editor.background': REDHAT.bg,
      'editor.foreground': REDHAT.text,
      'editorLineNumber.foreground': REDHAT.textMuted,
      'editorLineNumber.activeForeground': REDHAT.red,
      'editor.selectionBackground': '#E8F4FF',
      'editorCursor.foreground': REDHAT.red,
    },
  });
}

/** Convert our FileTreeNode[] to PatternFly TreeViewDataItem[] */
function toTreeViewData(nodes: FileTreeNode[]): TreeViewDataItem[] {
  return nodes.map((node) => ({
    id: node.path,
    name: node.name,
    icon: node.type === 'folder' ? <FolderIcon /> : getFileIcon(node.name),
    expandedIcon: node.type === 'folder' ? <FolderOpenIcon /> : undefined,
    children: node.children ? toTreeViewData(node.children) : undefined,
    defaultExpanded: false,
  }));
}

function getFileIcon(name: string): React.ReactNode {
  if (name.endsWith('.py') || name.endsWith('.ts') || name.endsWith('.tsx') || name.endsWith('.js') || name.endsWith('.jsx')) {
    return <FileCodeIcon />;
  }
  return <FileIcon />;
}

/* Refine polling constants moved into RefineChat component */

const Files: React.FC = () => {
  const [searchParams] = useSearchParams();
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [treeData, setTreeData] = useState<TreeViewDataItem[]>([]);
  const [selectedFile, setSelectedFile] = useState<{ path: string; content: string } | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);
  const [loading, setLoading] = useState(true);
  const [isSelectOpen, setIsSelectOpen] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  /* Refine state now lives in RefineChat component */

  /** Load jobs list and file tree. Pass jobId to immediately load files for that project (e.g. after dropdown change). */
  const loadData = useCallback(async (overrideJobId?: string) => {
    try {
      const j = await getJobs();
      setJobs(j);

      let jobId: string | null = overrideJobId ?? selectedJobId;

      if (overrideJobId === undefined) {
        // No override: apply URL and auto-selection rules
        const urlJobId = searchParams.get('job');
        if (urlJobId && urlJobId !== selectedJobId) {
          jobId = urlJobId;
          setSelectedJobId(jobId);
        } else if (!jobId || !j.find((job) => job.id === jobId)) {
          const running = j.find((job) => job.status === 'running');
          jobId = running?.id ?? j[0]?.id ?? null;
          setSelectedJobId(jobId);
        }
      }

      if (jobId) {
        const files = await getWorkspaceFiles(jobId);
        const tree = buildFileTree(files);
        setTreeData(toTreeViewData(tree));
      } else {
        setTreeData([]);
      }
    } catch {
      // API not available
    } finally {
      setLoading(false);
    }
  }, [selectedJobId, searchParams]);

  usePolling(loadData, 5000);

  /** Called by RefineChat after a successful refinement to reload tree + file */
  const handleRefineComplete = useCallback(async () => {
    await loadData();
    if (selectedFile && selectedJobId) {
      try {
        const result = await getFileContent(selectedFile.path, selectedJobId);
        setSelectedFile(result);
      } catch { /* ignore */ }
    }
  }, [loadData, selectedFile, selectedJobId]);

  const handleFileSelect = async (_event: React.MouseEvent, item: TreeViewDataItem) => {
    // Only handle file clicks (items without children)
    if (item.children) return;

    const filePath = item.id as string;
    setLoadingFile(true);
    try {
      const result = await getFileContent(filePath, selectedJobId || undefined);
      setSelectedFile(result);
    } catch (err) {
      console.error('Error loading file:', err);
      setSelectedFile({ path: filePath, content: 'Error loading file content.' });
    } finally {
      setLoadingFile(false);
    }
  };

  const handleJobSelect = (_event: React.MouseEvent | undefined, value: string | number | undefined) => {
    const newJobId = value as string;
    setSelectedJobId(newJobId);
    setSelectedFile(null);
    setIsSelectOpen(false);
    // Reload file tree immediately for the selected project
    loadData(newJobId);
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '4rem' }}>
        <Spinner aria-label="Loading files" />
      </div>
    );
  }

  return (
    <>
      {downloadError && (
        <Alert
          variant="danger"
          title={downloadError}
          isInline
          style={{ marginBottom: '1rem' }}
          actionClose={<Button variant="plain" onClick={() => setDownloadError(null)} aria-label="Close">×</Button>}
        />
      )}
      <Split hasGutter style={{ marginBottom: '1.5rem' }}>
        <SplitItem isFilled>
          <Title headingLevel="h1" size="2xl" style={{ fontFamily: '"Red Hat Display", sans-serif' }}>
            Files
          </Title>
          <p style={{ color: '#6A6E73', marginTop: '0.25rem' }}>Browse workspace files generated by jobs.</p>
        </SplitItem>
        <SplitItem>
          <Flex alignItems={{ default: 'alignItemsCenter' }} gap={{ default: 'gapSm' }}>
            {jobs.length > 0 && (
              <Select
                isOpen={isSelectOpen}
                selected={selectedJobId || undefined}
                onSelect={handleJobSelect}
                onOpenChange={setIsSelectOpen}
                toggle={(toggleRef: React.Ref<MenuToggleElement>) => (
                  <MenuToggle
                    ref={toggleRef}
                    data-testid="files-project-select-toggle"
                    onClick={() => setIsSelectOpen(!isSelectOpen)}
                    isExpanded={isSelectOpen}
                    style={{ minWidth: 200 }}
                  >
                    {jobs.find((j) => j.id === selectedJobId)?.vision.substring(0, 30) || 'Select Job'}
                  </MenuToggle>
                )}
              >
                <SelectList>
                  {jobs.map((job) => (
                    <SelectOption key={job.id} value={job.id}>
                      <Flex justifyContent={{ default: 'justifyContentSpaceBetween' }}>
                        <FlexItem>{job.vision.substring(0, 40)}{job.vision.length > 40 ? '...' : ''}</FlexItem>
                        <FlexItem>
                          <Label isCompact color={
                            job.status === 'running' ? 'blue' :
                            job.status === 'completed' ? 'green' :
                            job.status === 'failed' || job.status === 'quota_exhausted' ? 'red' :
                            job.status === 'cancelled' ? 'orange' :
                            'grey'
                          }>
                            {job.status === 'quota_exhausted' ? 'quota' : job.status}
                          </Label>
                        </FlexItem>
                      </Flex>
                    </SelectOption>
                  ))}
                </SelectList>
              </Select>
            )}
            {selectedJobId && (
              <Button
                variant="secondary"
                icon={<DownloadIcon />}
                iconPosition="end"
                data-testid="files-download-project"
                isDisabled={downloading}
                onClick={async () => {
                  setDownloadError(null);
                  setDownloading(true);
                  try {
                    await downloadJobWorkspace(selectedJobId);
                  } catch (e: unknown) {
                    const msg = e && typeof e === 'object' && 'response' in e
                      ? (e as { response?: { status?: number; data?: unknown } }).response?.status === 404
                        ? 'Project or workspace not found.'
                        : 'Download failed. Try again.'
                      : 'Download failed. Try again.';
                    setDownloadError(msg);
                  } finally {
                    setDownloading(false);
                  }
                }}
              >
                {downloading ? 'Downloading...' : 'Download project'}
              </Button>
            )}
          </Flex>
        </SplitItem>
      </Split>

      <div style={{ display: 'flex', gap: '1.5rem', height: 'calc(100vh - 14rem)' }}>
        {/* File Tree Panel */}
        <Card style={{ width: 320, flexShrink: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <CardHeader style={{ borderBottom: '1px solid #D2D2D2' }}>
            <CardTitle style={{ fontSize: '0.875rem', display: 'flex', alignItems: 'center', gap: 8 }}>
              <FolderIcon /> Project Explorer
            </CardTitle>
          </CardHeader>
          <CardBody style={{ overflow: 'auto', flex: 1, padding: '0.5rem' }}>
            {treeData.length > 0 ? (
              <TreeView data={treeData} onSelect={handleFileSelect} />
            ) : (
              <p style={{ color: '#6A6E73', textAlign: 'center', padding: '2rem', fontSize: '0.875rem' }}>
                No files in workspace
              </p>
            )}
          </CardBody>
        </Card>

        {/* File Content Panel */}
        <Card style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {loadingFile ? (
            <CardBody style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Spinner aria-label="Loading file" />
            </CardBody>
          ) : selectedFile ? (
            <>
              <CardHeader style={{ borderBottom: `1px solid ${REDHAT.border}` }}>
                <CardTitle style={{ fontSize: '0.875rem', fontFamily: '"Red Hat Mono", "JetBrains Mono", monospace', color: REDHAT.text }}>
                  {selectedFile.path}
                </CardTitle>
              </CardHeader>
              <CardBody style={{ overflow: 'hidden', flex: 1, padding: 0, backgroundColor: REDHAT.bg, display: 'flex', flexDirection: 'column' }}>
                <div
                  style={{
                    flex: 1,
                    minHeight: 240,
                    borderTop: `1px solid ${REDHAT.border}`,
                    boxSizing: 'border-box',
                  }}
                >
                  <Editor
                    height={selectedFile.path.toLowerCase().endsWith('.html') || selectedFile.path.toLowerCase().endsWith('.htm') ? '50%' : '100%'}
                    language={getLanguage(selectedFile.path)}
                    value={selectedFile.content}
                    theme="redhat-light"
                    loading={null}
                    options={{
                      readOnly: true,
                      minimap: { enabled: false },
                      scrollBeyondLastLine: false,
                      fontSize: 13,
                      fontFamily: '"Red Hat Mono", "JetBrains Mono", "Menlo", monospace',
                      lineNumbers: 'on',
                      renderLineHighlight: 'line',
                      padding: { top: 12, bottom: 12 },
                      overviewRulerBorder: false,
                      hideCursorInOverviewRuler: true,
                      matchBrackets: 'always',
                      cursorBlinking: 'smooth',
                    }}
                    beforeMount={defineRedHatTheme}
                  />
                </div>
                {(selectedFile.path.toLowerCase().endsWith('.html') || selectedFile.path.toLowerCase().endsWith('.htm')) && selectedJobId && (
                  <div style={{ borderTop: `1px solid ${REDHAT.border}`, flex: '1 1 280px', minHeight: 280 }}>
                    <div style={{ padding: '8px 12px', fontSize: '0.875rem', color: REDHAT.textMuted }}>Preview</div>
                    <iframe
                      title="HTML preview"
                      src={getPreviewUrl(selectedJobId, selectedFile.path)}
                      sandbox="allow-scripts"
                      style={{ width: '100%', height: 'calc(100% - 32px)', border: 'none', display: 'block' }}
                    />
                  </div>
                )}
              </CardBody>
            </>
          ) : (
            <CardBody style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <EmptyState>
                <EmptyStateIcon icon={CubeIcon} />
                <Title headingLevel="h4" size="lg">
                  Select a file to view
                </Title>
                <EmptyStateBody>
                  Choose a file from the project explorer to view its contents.
                </EmptyStateBody>
              </EmptyState>
            </CardBody>
          )}
        </Card>
      </div>

      {/* Floating refine button + slide-out chat panel */}
      <RefineChat
        selectedJobId={selectedJobId}
        selectedFile={selectedFile}
        jobVision={jobs.find((j) => j.id === selectedJobId)?.vision || null}
        onRefineComplete={handleRefineComplete}
      />
    </>
  );
};

export default Files;
