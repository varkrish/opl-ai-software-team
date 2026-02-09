import type { WorkspaceFile, FileTreeNode } from '../types';

/**
 * Internal/system files created by backend agents that should be hidden from the UI.
 * These are infrastructure files not relevant to the generated project code.
 */
const INTERNAL_FILE_PATTERNS = [
  /^tasks_.*\.db$/,           // SQLite task database
  /^state_.*\.json$/,         // Job state tracking
  /^crew_errors\.log$/,       // Error logs
  /^agent_prompts\.json$/,    // Internal prompt data
  /^repomix-.*\.xml$/,        // Repomix packed repo outputs
  /^\..*$/,                   // Hidden files (dotfiles)
  /^__pycache__$/,            // Python cache
  /^\.pytest_cache$/,         // Pytest cache
  /^\.mypy_cache$/,           // MyPy cache
  /^node_modules$/,           // Node modules (if any)
  /^\.venv$/,                 // Python venv
  /^venv$/,                   // Python venv
];

/**
 * Check if a file/folder should be hidden from the UI.
 */
function shouldHide(name: string): boolean {
  return INTERNAL_FILE_PATTERNS.some((pattern) => pattern.test(name));
}

/**
 * Convert a flat list of workspace files into a nested tree structure,
 * filtering out internal/system files.
 * Each file path like "src/agents/meta.py" becomes nested folders + file leaf.
 */
export function buildFileTree(files: WorkspaceFile[]): FileTreeNode[] {
  // First filter out files with internal names at any path segment
  const filteredFiles = files.filter((file) => {
    const parts = file.path.split('/');
    return !parts.some(shouldHide);
  });

  const root: FileTreeNode[] = [];

  for (const file of filteredFiles) {
    const parts = file.path.split('/');
    let current = root;

    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isFile = i === parts.length - 1;
      const pathSoFar = parts.slice(0, i + 1).join('/');

      let existing = current.find((n) => n.name === part);

      if (!existing) {
        existing = {
          name: part,
          path: pathSoFar,
          type: isFile ? 'file' : 'folder',
          ...(isFile ? { size: file.size } : { children: [] }),
        };
        current.push(existing);
      }

      if (!isFile && existing.children) {
        current = existing.children;
      }
    }
  }

  // Sort: folders first, then files, alphabetically within each group
  sortTree(root);
  return root;
}

function sortTree(nodes: FileTreeNode[]): void {
  nodes.sort((a, b) => {
    if (a.type !== b.type) return a.type === 'folder' ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  for (const node of nodes) {
    if (node.children) {
      sortTree(node.children);
    }
  }
}
