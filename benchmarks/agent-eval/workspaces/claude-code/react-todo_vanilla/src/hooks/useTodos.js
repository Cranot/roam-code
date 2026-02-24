import { useCallback, useMemo } from 'react';
import { useLocalStorage } from './useLocalStorage';

export const CATEGORIES = ['work', 'personal', 'shopping', 'health'];
export const PRIORITIES = ['high', 'medium', 'low'];
export const SORT_OPTIONS = [
  { value: 'dueDate', label: 'Due Date' },
  { value: 'priority', label: 'Priority' },
  { value: 'createdAt', label: 'Creation Date' },
];

const PRIORITY_ORDER = { high: 0, medium: 1, low: 2 };

export function useTodos() {
  const [todos, setTodos] = useLocalStorage('todos', []);
  const [filters, setFilters] = useLocalStorage('todo-filters', {
    category: 'all',
    priority: 'all',
    status: 'all',
  });
  const [sortBy, setSortBy] = useLocalStorage('todo-sort', 'createdAt');

  const addTodo = useCallback((todo) => {
    setTodos((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        title: todo.title,
        category: todo.category,
        priority: todo.priority,
        dueDate: todo.dueDate || null,
        completed: false,
        createdAt: new Date().toISOString(),
      },
    ]);
  }, [setTodos]);

  const updateTodo = useCallback((id, updates) => {
    setTodos((prev) =>
      prev.map((t) => (t.id === id ? { ...t, ...updates } : t))
    );
  }, [setTodos]);

  const deleteTodo = useCallback((id) => {
    setTodos((prev) => prev.filter((t) => t.id !== id));
  }, [setTodos]);

  const toggleComplete = useCallback((id) => {
    setTodos((prev) =>
      prev.map((t) => (t.id === id ? { ...t, completed: !t.completed } : t))
    );
  }, [setTodos]);

  const filteredAndSorted = useMemo(() => {
    let result = [...todos];

    if (filters.category !== 'all') {
      result = result.filter((t) => t.category === filters.category);
    }
    if (filters.priority !== 'all') {
      result = result.filter((t) => t.priority === filters.priority);
    }
    if (filters.status === 'completed') {
      result = result.filter((t) => t.completed);
    } else if (filters.status === 'pending') {
      result = result.filter((t) => !t.completed);
    }

    result.sort((a, b) => {
      if (sortBy === 'dueDate') {
        if (!a.dueDate && !b.dueDate) return 0;
        if (!a.dueDate) return 1;
        if (!b.dueDate) return -1;
        return new Date(a.dueDate) - new Date(b.dueDate);
      }
      if (sortBy === 'priority') {
        return PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority];
      }
      return new Date(b.createdAt) - new Date(a.createdAt);
    });

    return result;
  }, [todos, filters, sortBy]);

  const summary = useMemo(() => {
    const total = todos.length;
    const completed = todos.filter((t) => t.completed).length;
    return { total, completed, pending: total - completed };
  }, [todos]);

  return {
    todos: filteredAndSorted,
    allTodos: todos,
    filters,
    setFilters,
    sortBy,
    setSortBy,
    addTodo,
    updateTodo,
    deleteTodo,
    toggleComplete,
    summary,
  };
}
