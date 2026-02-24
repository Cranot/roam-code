import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import TodoList from '../components/TodoList';

const sampleTodos = [
  {
    id: '1',
    title: 'First task',
    category: 'work',
    priority: 'high',
    dueDate: null,
    completed: false,
    createdAt: '2026-01-01T00:00:00.000Z',
  },
  {
    id: '2',
    title: 'Second task',
    category: 'personal',
    priority: 'low',
    dueDate: '2026-06-01',
    completed: true,
    createdAt: '2026-01-02T00:00:00.000Z',
  },
];

describe('TodoList', () => {
  it('renders all todos', () => {
    render(
      <TodoList
        todos={sampleTodos}
        onToggle={vi.fn()}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
      />
    );
    expect(screen.getByText('First task')).toBeInTheDocument();
    expect(screen.getByText('Second task')).toBeInTheDocument();
  });

  it('shows empty message when no todos', () => {
    render(
      <TodoList todos={[]} onToggle={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />
    );
    expect(screen.getByText(/No tasks found/)).toBeInTheDocument();
  });
});
