import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTodos } from '../hooks/useTodos';

describe('useTodos', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('starts with empty todos', () => {
    const { result } = renderHook(() => useTodos());
    expect(result.current.todos).toEqual([]);
    expect(result.current.summary).toEqual({ total: 0, completed: 0, pending: 0 });
  });

  it('adds a todo', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({
        title: 'Test',
        category: 'work',
        priority: 'high',
        dueDate: null,
      });
    });

    expect(result.current.todos).toHaveLength(1);
    expect(result.current.todos[0].title).toBe('Test');
    expect(result.current.todos[0].category).toBe('work');
    expect(result.current.todos[0].priority).toBe('high');
    expect(result.current.todos[0].completed).toBe(false);
  });

  it('toggles completion', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({
        title: 'Toggle me',
        category: 'personal',
        priority: 'medium',
        dueDate: null,
      });
    });

    const id = result.current.todos[0].id;

    act(() => {
      result.current.toggleComplete(id);
    });

    expect(result.current.allTodos.find((t) => t.id === id).completed).toBe(true);
    expect(result.current.summary.completed).toBe(1);
  });

  it('updates a todo', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({
        title: 'Original',
        category: 'work',
        priority: 'low',
        dueDate: null,
      });
    });

    const id = result.current.todos[0].id;

    act(() => {
      result.current.updateTodo(id, { title: 'Updated', priority: 'high' });
    });

    expect(result.current.todos[0].title).toBe('Updated');
    expect(result.current.todos[0].priority).toBe('high');
  });

  it('deletes a todo', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({
        title: 'Delete me',
        category: 'shopping',
        priority: 'medium',
        dueDate: null,
      });
    });

    const id = result.current.todos[0].id;

    act(() => {
      result.current.deleteTodo(id);
    });

    expect(result.current.todos).toHaveLength(0);
    expect(result.current.summary.total).toBe(0);
  });

  it('filters by category', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({ title: 'Work task', category: 'work', priority: 'medium', dueDate: null });
      result.current.addTodo({ title: 'Personal task', category: 'personal', priority: 'medium', dueDate: null });
    });

    act(() => {
      result.current.setFilters({ category: 'work', priority: 'all', status: 'all' });
    });

    expect(result.current.todos).toHaveLength(1);
    expect(result.current.todos[0].title).toBe('Work task');
  });

  it('filters by priority', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({ title: 'High', category: 'work', priority: 'high', dueDate: null });
      result.current.addTodo({ title: 'Low', category: 'work', priority: 'low', dueDate: null });
    });

    act(() => {
      result.current.setFilters({ category: 'all', priority: 'high', status: 'all' });
    });

    expect(result.current.todos).toHaveLength(1);
    expect(result.current.todos[0].title).toBe('High');
  });

  it('filters by completion status', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({ title: 'Pending', category: 'work', priority: 'medium', dueDate: null });
      result.current.addTodo({ title: 'Done', category: 'work', priority: 'medium', dueDate: null });
    });

    const doneId = result.current.todos.find((t) => t.title === 'Done').id;

    act(() => {
      result.current.toggleComplete(doneId);
    });

    act(() => {
      result.current.setFilters({ category: 'all', priority: 'all', status: 'completed' });
    });

    expect(result.current.todos).toHaveLength(1);
    expect(result.current.todos[0].title).toBe('Done');
  });

  it('sorts by priority', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({ title: 'Low', category: 'work', priority: 'low', dueDate: null });
      result.current.addTodo({ title: 'High', category: 'work', priority: 'high', dueDate: null });
      result.current.addTodo({ title: 'Medium', category: 'work', priority: 'medium', dueDate: null });
    });

    act(() => {
      result.current.setSortBy('priority');
    });

    expect(result.current.todos.map((t) => t.title)).toEqual(['High', 'Medium', 'Low']);
  });

  it('sorts by due date', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({ title: 'Later', category: 'work', priority: 'medium', dueDate: '2026-06-01' });
      result.current.addTodo({ title: 'Sooner', category: 'work', priority: 'medium', dueDate: '2026-03-01' });
      result.current.addTodo({ title: 'No date', category: 'work', priority: 'medium', dueDate: null });
    });

    act(() => {
      result.current.setSortBy('dueDate');
    });

    expect(result.current.todos.map((t) => t.title)).toEqual(['Sooner', 'Later', 'No date']);
  });

  it('persists to localStorage', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({ title: 'Persisted', category: 'health', priority: 'low', dueDate: null });
    });

    const stored = JSON.parse(localStorage.getItem('todos'));
    expect(stored).toHaveLength(1);
    expect(stored[0].title).toBe('Persisted');
  });

  it('summary reflects correct counts', () => {
    const { result } = renderHook(() => useTodos());

    act(() => {
      result.current.addTodo({ title: 'One', category: 'work', priority: 'medium', dueDate: null });
      result.current.addTodo({ title: 'Two', category: 'work', priority: 'medium', dueDate: null });
      result.current.addTodo({ title: 'Three', category: 'work', priority: 'medium', dueDate: null });
    });

    const id = result.current.allTodos[0].id;
    act(() => {
      result.current.toggleComplete(id);
    });

    expect(result.current.summary).toEqual({ total: 3, completed: 1, pending: 2 });
  });
});
