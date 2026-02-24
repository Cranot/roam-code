import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TodoItem from '../components/TodoItem';

const baseTodo = {
  id: '1',
  title: 'Test task',
  category: 'work',
  priority: 'medium',
  dueDate: null,
  completed: false,
  createdAt: '2026-01-01T00:00:00.000Z',
};

describe('TodoItem', () => {
  it('renders the todo title', () => {
    render(
      <TodoItem todo={baseTodo} onToggle={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />
    );
    expect(screen.getByText('Test task')).toBeInTheDocument();
  });

  it('renders category and priority badges', () => {
    render(
      <TodoItem todo={baseTodo} onToggle={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />
    );
    expect(screen.getByText('work')).toBeInTheDocument();
    expect(screen.getByText('medium')).toBeInTheDocument();
  });

  it('calls onToggle when checkbox is clicked', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(
      <TodoItem todo={baseTodo} onToggle={onToggle} onUpdate={vi.fn()} onDelete={vi.fn()} />
    );

    await user.click(screen.getByRole('checkbox'));
    expect(onToggle).toHaveBeenCalledWith('1');
  });

  it('calls onDelete when delete button is clicked', async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(
      <TodoItem todo={baseTodo} onToggle={vi.fn()} onUpdate={vi.fn()} onDelete={onDelete} />
    );

    await user.click(screen.getByRole('button', { name: 'Delete task' }));
    expect(onDelete).toHaveBeenCalledWith('1');
  });

  it('enters edit mode when Edit button is clicked', async () => {
    const user = userEvent.setup();
    render(
      <TodoItem todo={baseTodo} onToggle={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />
    );

    await user.click(screen.getByRole('button', { name: 'Edit task' }));
    expect(screen.getByLabelText('Edit task title')).toHaveValue('Test task');
  });

  it('saves edits when Save button is clicked', async () => {
    const user = userEvent.setup();
    const onUpdate = vi.fn();
    render(
      <TodoItem todo={baseTodo} onToggle={vi.fn()} onUpdate={onUpdate} onDelete={vi.fn()} />
    );

    await user.click(screen.getByRole('button', { name: 'Edit task' }));
    const input = screen.getByLabelText('Edit task title');
    await user.clear(input);
    await user.type(input, 'Updated task');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(onUpdate).toHaveBeenCalledWith('1', expect.objectContaining({ title: 'Updated task' }));
  });

  it('cancels edit on Escape key', async () => {
    const user = userEvent.setup();
    const onUpdate = vi.fn();
    render(
      <TodoItem todo={baseTodo} onToggle={vi.fn()} onUpdate={onUpdate} onDelete={vi.fn()} />
    );

    await user.click(screen.getByRole('button', { name: 'Edit task' }));
    await user.keyboard('{Escape}');

    expect(screen.getByText('Test task')).toBeInTheDocument();
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it('saves edit on Enter key', async () => {
    const user = userEvent.setup();
    const onUpdate = vi.fn();
    render(
      <TodoItem todo={baseTodo} onToggle={vi.fn()} onUpdate={onUpdate} onDelete={vi.fn()} />
    );

    await user.click(screen.getByRole('button', { name: 'Edit task' }));
    const input = screen.getByLabelText('Edit task title');
    await user.clear(input);
    await user.type(input, 'Enter saved{Enter}');

    expect(onUpdate).toHaveBeenCalledWith('1', expect.objectContaining({ title: 'Enter saved' }));
  });

  it('shows due date when provided', () => {
    const todoWithDate = { ...baseTodo, dueDate: '2026-03-15' };
    render(
      <TodoItem todo={todoWithDate} onToggle={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />
    );
    expect(screen.getByText(/Mar 15, 2026/)).toBeInTheDocument();
  });

  it('shows overdue indicator for past dates on incomplete tasks', () => {
    const overdueTodo = { ...baseTodo, dueDate: '2020-01-01' };
    render(
      <TodoItem todo={overdueTodo} onToggle={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />
    );
    expect(screen.getByText(/Overdue/)).toBeInTheDocument();
  });

  it('does not show overdue for completed tasks', () => {
    const completedOverdue = { ...baseTodo, dueDate: '2020-01-01', completed: true };
    render(
      <TodoItem
        todo={completedOverdue}
        onToggle={vi.fn()}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
      />
    );
    expect(screen.queryByText(/Overdue/)).not.toBeInTheDocument();
  });

  it('applies completed styling when task is completed', () => {
    const completedTodo = { ...baseTodo, completed: true };
    render(
      <TodoItem todo={completedTodo} onToggle={vi.fn()} onUpdate={vi.fn()} onDelete={vi.fn()} />
    );
    expect(screen.getByRole('checkbox')).toBeChecked();
  });
});
