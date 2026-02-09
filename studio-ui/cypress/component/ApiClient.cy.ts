/// <reference types="cypress" />

/**
 * TDD component tests for the API client utility functions.
 * Tests pure logic: fileTree builder and taskGrouping.
 */

import { buildFileTree } from '../../src/utils/fileTree';
import { groupTasksIntoColumns } from '../../src/utils/taskGrouping';
import type { WorkspaceFile, Task } from '../../src/types';

describe('buildFileTree', () => {
  it('should return empty array for no files', () => {
    const result = buildFileTree([]);
    expect(result).to.deep.equal([]);
  });

  it('should create a flat file at the root level', () => {
    const files: WorkspaceFile[] = [
      { path: 'README.md', size: 256, modified: '2025-01-01' },
    ];
    const result = buildFileTree(files);
    expect(result).to.have.length(1);
    expect(result[0].name).to.equal('README.md');
    expect(result[0].type).to.equal('file');
  });

  it('should create nested folders for paths with separators', () => {
    const files: WorkspaceFile[] = [
      { path: 'src/main.py', size: 1024, modified: '2025-01-01' },
    ];
    const result = buildFileTree(files);
    expect(result).to.have.length(1);
    expect(result[0].name).to.equal('src');
    expect(result[0].type).to.equal('folder');
    expect(result[0].children).to.have.length(1);
    expect(result[0].children![0].name).to.equal('main.py');
    expect(result[0].children![0].type).to.equal('file');
  });

  it('should group files under the same folder', () => {
    const files: WorkspaceFile[] = [
      { path: 'src/main.py', size: 1024, modified: '2025-01-01' },
      { path: 'src/config.py', size: 512, modified: '2025-01-01' },
    ];
    const result = buildFileTree(files);
    expect(result).to.have.length(1);
    expect(result[0].children).to.have.length(2);
  });

  it('should sort folders before files', () => {
    const files: WorkspaceFile[] = [
      { path: 'README.md', size: 256, modified: '2025-01-01' },
      { path: 'src/main.py', size: 1024, modified: '2025-01-01' },
    ];
    const result = buildFileTree(files);
    expect(result[0].type).to.equal('folder');
    expect(result[1].type).to.equal('file');
  });
});

describe('groupTasksIntoColumns', () => {
  it('should return 3 columns', () => {
    const result = groupTasksIntoColumns([]);
    expect(result).to.have.length(3);
    expect(result[0].id).to.equal('todo');
    expect(result[1].id).to.equal('in-progress');
    expect(result[2].id).to.equal('completed');
  });

  it('should group registered tasks as To Do', () => {
    const tasks: Task[] = [
      { task_id: 't1', phase: 'meta', task_type: 'init', description: 'Init', status: 'registered' },
    ];
    const result = groupTasksIntoColumns(tasks);
    expect(result[0].tasks).to.have.length(1);
    expect(result[0].tasks[0].task_id).to.equal('t1');
  });

  it('should group in_progress tasks correctly', () => {
    const tasks: Task[] = [
      { task_id: 't1', phase: 'arch', task_type: 'schema', description: 'Schema', status: 'in_progress' },
    ];
    const result = groupTasksIntoColumns(tasks);
    expect(result[1].tasks).to.have.length(1);
  });

  it('should group completed and skipped tasks in Completed', () => {
    const tasks: Task[] = [
      { task_id: 't1', phase: 'meta', task_type: 'init', description: 'Init', status: 'completed' },
      { task_id: 't2', phase: 'po', task_type: 'validate', description: 'Validate', status: 'skipped' },
    ];
    const result = groupTasksIntoColumns(tasks);
    expect(result[2].tasks).to.have.length(2);
  });

  it('should group failed tasks as To Do', () => {
    const tasks: Task[] = [
      { task_id: 't1', phase: 'dev', task_type: 'impl', description: 'Implement', status: 'failed' },
    ];
    const result = groupTasksIntoColumns(tasks);
    expect(result[0].tasks).to.have.length(1);
  });
});
