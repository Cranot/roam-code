import { useEffect, useReducer } from 'react';
import { CATEGORY_OPTIONS, PRIORITY_OPTIONS } from '../constants';
import { loadTasks, saveTasks } from '../utils/storage';

function createTaskId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }

  return `task-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

function createTask(taskData) {
  const title = taskData.title.trim();
  if (!title) {
    return null;
  }

  const createdAt = new Date().toISOString();

  return {
    id: createTaskId(),
    title,
    description: taskData.description?.trim() ?? '',
    category: CATEGORY_OPTIONS.includes(taskData.category) ? taskData.category : 'personal',
    priority: PRIORITY_OPTIONS.includes(taskData.priority) ? taskData.priority : 'medium',
    dueDate: taskData.dueDate ?? '',
    completed: false,
    createdAt,
    updatedAt: createdAt,
  };
}

function tasksReducer(tasks, action) {
  switch (action.type) {
    case 'add': {
      const task = createTask(action.payload);
      if (!task) {
        return tasks;
      }
      return [task, ...tasks];
    }

    case 'update':
      return tasks.map((task) => {
        if (task.id !== action.payload.id) {
          return task;
        }

        const updates = action.payload.updates;
        const nextTitle = typeof updates.title === 'string' ? updates.title.trim() : task.title;
        const safeTitle = nextTitle || task.title;

        return {
          ...task,
          title: safeTitle,
          description: typeof updates.description === 'string' ? updates.description.trim() : task.description,
          category: CATEGORY_OPTIONS.includes(updates.category) ? updates.category : task.category,
          priority: PRIORITY_OPTIONS.includes(updates.priority) ? updates.priority : task.priority,
          dueDate: typeof updates.dueDate === 'string' ? updates.dueDate : task.dueDate,
          updatedAt: new Date().toISOString(),
        };
      });

    case 'toggle-complete':
      return tasks.map((task) =>
        task.id === action.payload
          ? {
              ...task,
              completed: !task.completed,
              updatedAt: new Date().toISOString(),
            }
          : task,
      );

    case 'delete':
      return tasks.filter((task) => task.id !== action.payload);

    default:
      return tasks;
  }
}

export function useTodos() {
  const [tasks, dispatch] = useReducer(tasksReducer, [], loadTasks);

  useEffect(() => {
    saveTasks(tasks);
  }, [tasks]);

  const addTask = (taskData) => {
    dispatch({ type: 'add', payload: taskData });
  };

  const updateTask = (id, updates) => {
    dispatch({ type: 'update', payload: { id, updates } });
  };

  const toggleTaskCompletion = (id) => {
    dispatch({ type: 'toggle-complete', payload: id });
  };

  const deleteTask = (id) => {
    dispatch({ type: 'delete', payload: id });
  };

  return {
    tasks,
    addTask,
    updateTask,
    toggleTaskCompletion,
    deleteTask,
  };
}
