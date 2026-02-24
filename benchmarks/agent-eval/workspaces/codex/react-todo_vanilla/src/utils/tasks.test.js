import { describe, expect, it } from 'vitest';
import { applyFiltersAndSort, isTaskOverdue, sortTasks } from './tasks';

const TASKS = [
  {
    id: '1',
    title: 'Work task',
    description: '',
    category: 'work',
    priority: 'high',
    dueDate: '2026-02-14',
    completed: false,
    createdAt: '2026-02-10T09:00:00.000Z',
    updatedAt: '2026-02-10T09:00:00.000Z',
  },
  {
    id: '2',
    title: 'Personal task',
    description: '',
    category: 'personal',
    priority: 'medium',
    dueDate: '',
    completed: false,
    createdAt: '2026-02-12T09:00:00.000Z',
    updatedAt: '2026-02-12T09:00:00.000Z',
  },
  {
    id: '3',
    title: 'Shopping task',
    description: '',
    category: 'shopping',
    priority: 'low',
    dueDate: '2026-02-20',
    completed: true,
    createdAt: '2026-02-11T09:00:00.000Z',
    updatedAt: '2026-02-11T09:00:00.000Z',
  },
];

describe('task utilities', () => {
  it('filters by category, priority, and status', () => {
    const filtered = applyFiltersAndSort(TASKS, {
      category: 'work',
      priority: 'high',
      status: 'pending',
      sortBy: 'creationDate',
    });

    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe('1');
  });

  it('sorts by due date while keeping tasks without due date last', () => {
    const sorted = sortTasks(TASKS, 'dueDate');
    expect(sorted.map((task) => task.id)).toEqual(['1', '3', '2']);
  });

  it('sorts by priority from high to low', () => {
    const sorted = sortTasks(TASKS, 'priority');
    expect(sorted.map((task) => task.priority)).toEqual(['high', 'medium', 'low']);
  });

  it('identifies overdue tasks for incomplete tasks only', () => {
    expect(isTaskOverdue(TASKS[0], new Date('2026-02-16T08:00:00.000Z'))).toBe(true);
    expect(isTaskOverdue(TASKS[2], new Date('2026-02-16T08:00:00.000Z'))).toBe(false);
  });
});
