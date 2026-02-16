import { PRIORITY_WEIGHTS } from '../types';

export const isOverdue = (dueDate) => {
  if (!dueDate) return false;
  const due = new Date(dueDate);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return due < today;
};

export const filterTasks = (tasks, { status, category, priority }) => {
  return tasks.filter(task => {
    if (status === 'active' && task.completed) return false;
    if (status === 'completed' && !task.completed) return false;
    if (category !== 'all' && task.category !== category) return false;
    if (priority !== 'all' && task.priority !== priority) return false;
    return true;
  });
};

export const sortTasks = (tasks, sortBy) => {
  const sorted = [...tasks];
  switch (sortBy) {
    case 'dueDate':
      return sorted.sort((a, b) => {
        if (!a.dueDate && !b.dueDate) return 0;
        if (!a.dueDate) return 1;
        if (!b.dueDate) return -1;
        return new Date(a.dueDate) - new Date(b.dueDate);
      });
    case 'priority':
      return sorted.sort((a, b) => PRIORITY_WEIGHTS[b.priority] - PRIORITY_WEIGHTS[a.priority]);
    case 'createdAt':
      return sorted.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
    default:
      return sorted;
  }
};

export const getTaskStats = (tasks) => {
  const total = tasks.length;
  const completed = tasks.filter(t => t.completed).length;
  const pending = total - completed;
  const overdue = tasks.filter(t => !t.completed && isOverdue(t.dueDate)).length;
  return { total, completed, pending, overdue };
};

export const generateId = () => {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
};
