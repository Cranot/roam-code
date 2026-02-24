import { PRIORITY_RANK } from '../constants';

export function getDateStamp(referenceDate = new Date()) {
  const date = new Date(referenceDate);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function isTaskOverdue(task, referenceDate = new Date()) {
  if (!task.dueDate || task.completed) {
    return false;
  }

  return task.dueDate < getDateStamp(referenceDate);
}

export function filterTasks(tasks, filters) {
  return tasks.filter((task) => {
    if (filters.category !== 'all' && task.category !== filters.category) {
      return false;
    }

    if (filters.priority !== 'all' && task.priority !== filters.priority) {
      return false;
    }

    if (filters.status === 'completed' && !task.completed) {
      return false;
    }

    if (filters.status === 'pending' && task.completed) {
      return false;
    }

    return true;
  });
}

function dueDateTimestamp(task) {
  if (!task.dueDate) {
    return Number.POSITIVE_INFINITY;
  }
  return new Date(`${task.dueDate}T00:00:00`).getTime();
}

export function sortTasks(tasks, sortBy) {
  const sortedTasks = [...tasks];

  sortedTasks.sort((leftTask, rightTask) => {
    if (sortBy === 'priority') {
      return PRIORITY_RANK[leftTask.priority] - PRIORITY_RANK[rightTask.priority];
    }

    if (sortBy === 'dueDate') {
      return dueDateTimestamp(leftTask) - dueDateTimestamp(rightTask);
    }

    return new Date(rightTask.createdAt).getTime() - new Date(leftTask.createdAt).getTime();
  });

  return sortedTasks;
}

export function applyFiltersAndSort(tasks, filters) {
  const filteredTasks = filterTasks(tasks, filters);
  return sortTasks(filteredTasks, filters.sortBy);
}
