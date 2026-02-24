import { CATEGORY_OPTIONS, PRIORITY_OPTIONS, STORAGE_KEY } from '../constants';

function isValidDateStamp(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function sanitizeTask(rawTask) {
  if (!rawTask || typeof rawTask !== 'object') {
    return null;
  }

  const title = typeof rawTask.title === 'string' ? rawTask.title.trim() : '';
  if (!title) {
    return null;
  }

  const category = CATEGORY_OPTIONS.includes(rawTask.category) ? rawTask.category : 'personal';
  const priority = PRIORITY_OPTIONS.includes(rawTask.priority) ? rawTask.priority : 'medium';
  const dueDate = isValidDateStamp(rawTask.dueDate) ? rawTask.dueDate : '';
  const createdAt = typeof rawTask.createdAt === 'string' ? rawTask.createdAt : new Date().toISOString();
  const updatedAt = typeof rawTask.updatedAt === 'string' ? rawTask.updatedAt : createdAt;

  return {
    id: typeof rawTask.id === 'string' ? rawTask.id : `task-${createdAt}`,
    title,
    description: typeof rawTask.description === 'string' ? rawTask.description.trim() : '',
    category,
    priority,
    dueDate,
    completed: Boolean(rawTask.completed),
    createdAt,
    updatedAt,
  };
}

export function loadTasks() {
  try {
    const storedValue = window.localStorage.getItem(STORAGE_KEY);
    if (!storedValue) {
      return [];
    }

    const parsedValue = JSON.parse(storedValue);
    if (!Array.isArray(parsedValue)) {
      return [];
    }

    return parsedValue.map(sanitizeTask).filter(Boolean);
  } catch {
    return [];
  }
}

export function saveTasks(tasks) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
  } catch {
    // Ignore localStorage failures to avoid breaking interaction.
  }
}
