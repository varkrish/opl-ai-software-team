import React, { useState, useCallback, useEffect } from 'react';
import {
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
import { FolderIcon, FolderOpenIcon, FileIcon, FileCodeIcon, CubeIcon } from '@patternfly/react-icons';
import { useSearchParams } from 'react-router-dom';
import Editor, { type Monaco } from '@monaco-editor/react';
import { usePolling } from '../hooks/usePolling';
import { getJobs, getWorkspaceFiles, getFileContent } from '../api/client';
import { buildFileTree } from '../utils/fileTree';
import type { JobSummary, FileTreeNode } from '../types';

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

const Files: React.FC = () => {
  const [searchParams] = useSearchParams();
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [treeData, setTreeData] = useState<TreeViewDataItem[]>([]);
  const [selectedFile, setSelectedFile] = useState<{ path: string; content: string } | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);
  const [loading, setLoading] = useState(true);
  const [isSelectOpen, setIsSelectOpen] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const j = await getJobs();
      setJobs(j);

      // Priority order for job selection:
      // 1. URL parameter (if present and not yet applied)
      // 2. Current selection (if valid)
      // 3. First running job
      // 4. Most recent job
      
      let jobId = selectedJobId;
      
      // Check if URL param should override current selection
      const urlJobId = searchParams.get('job');
      if (urlJobId && urlJobId !== selectedJobId) {
        // URL param exists and differs from current selection - use it
        jobId = urlJobId;
        setSelectedJobId(jobId);
      } else if (!jobId || !j.find((job) => job.id === jobId)) {
        // No valid selection - auto-select
        const running = j.find((job) => job.status === 'running');
        jobId = running?.id || j[0]?.id || null;
        setSelectedJobId(jobId);
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
    setSelectedJobId(value as string);
    setSelectedFile(null);
    setIsSelectOpen(false);
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
      <Split hasGutter style={{ marginBottom: '1.5rem' }}>
        <SplitItem isFilled>
          <Title headingLevel="h1" size="2xl" style={{ fontFamily: '"Red Hat Display", sans-serif' }}>
            Files
          </Title>
          <p style={{ color: '#6A6E73', marginTop: '0.25rem' }}>Browse workspace files generated by jobs.</p>
        </SplitItem>
        <SplitItem>
          {jobs.length > 0 && (
            <Select
              isOpen={isSelectOpen}
              selected={selectedJobId || undefined}
              onSelect={handleJobSelect}
              onOpenChange={setIsSelectOpen}
              toggle={(toggleRef: React.Ref<MenuToggleElement>) => (
                <MenuToggle
                  ref={toggleRef}
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
              <CardBody style={{ overflow: 'hidden', flex: 1, padding: 0, backgroundColor: REDHAT.bg }}>
                <div
                  style={{
                    height: '100%',
                    minHeight: 360,
                    borderTop: `1px solid ${REDHAT.border}`,
                    boxSizing: 'border-box',
                  }}
                >
                  <Editor
                    height="100%"
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
    </>
  );
};

export default Files;
