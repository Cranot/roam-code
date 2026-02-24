export const CATEGORY_OPTIONS = ['work', 'personal', 'shopping', 'health'];
export const PRIORITY_OPTIONS = ['high', 'medium', 'low'];

export const STATUS_OPTIONS = ['all', 'completed', 'pending'];
export const SORT_OPTIONS = ['creationDate', 'dueDate', 'priority'];

export const PRIORITY_RANK = {
  high: 0,
  medium: 1,
  low: 2,
};

export const DEFAULT_FILTERS = {
  category: 'all',
  priority: 'all',
  status: 'all',
  sortBy: 'creationDate',
};

export const STORAGE_KEY = 'todo_app_tasks_v1';
