import type { Task, KanbanColumn } from '../types';

/**
 * Group tasks into Kanban columns based on their status.
 * Maps backend statuses to four columns: Planned, In Progress, Completed, Failed.
 */
export function groupTasksIntoColumns(tasks: Task[]): KanbanColumn[] {
  const todo: Task[] = [];
  const inProgress: Task[] = [];
  const completed: Task[] = [];
  const failed: Task[] = [];

  for (const task of tasks) {
    switch (task.status) {
      case 'in_progress':
        inProgress.push(task);
        break;
      case 'completed':
      case 'skipped':
        completed.push(task);
        break;
      case 'failed':
        failed.push(task);
        break;
      case 'pending':
      case 'registered':
      case 'created':
      default:
        todo.push(task);
        break;
    }
  }

  const columns: KanbanColumn[] = [
    { id: 'todo', title: 'Planned', tasks: todo },
    { id: 'in-progress', title: 'In Progress', tasks: inProgress },
    { id: 'completed', title: 'Completed', tasks: completed },
  ];

  // Only show Failed column if there are failed tasks
  if (failed.length > 0) {
    columns.push({ id: 'failed', title: 'Failed', tasks: failed });
  }

  return columns;
}
