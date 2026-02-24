import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TodoForm from '../components/TodoForm';

describe('TodoForm', () => {
  it('renders the form with all inputs', () => {
    render(<TodoForm onAdd={vi.fn()} />);
    expect(screen.getByPlaceholderText('What needs to be done?')).toBeInTheDocument();
    expect(screen.getByLabelText('Category')).toBeInTheDocument();
    expect(screen.getByLabelText('Priority')).toBeInTheDocument();
    expect(screen.getByLabelText('Due date')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add' })).toBeInTheDocument();
  });

  it('disables the Add button when input is empty', () => {
    render(<TodoForm onAdd={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled();
  });

  it('enables the Add button when input has text', async () => {
    const user = userEvent.setup();
    render(<TodoForm onAdd={vi.fn()} />);
    await user.type(screen.getByPlaceholderText('What needs to be done?'), 'Test task');
    expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled();
  });

  it('calls onAdd with correct data when form is submitted', async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<TodoForm onAdd={onAdd} />);

    await user.type(screen.getByPlaceholderText('What needs to be done?'), 'Buy groceries');
    await user.selectOptions(screen.getByLabelText('Category'), 'shopping');
    await user.selectOptions(screen.getByLabelText('Priority'), 'high');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    expect(onAdd).toHaveBeenCalledWith({
      title: 'Buy groceries',
      category: 'shopping',
      priority: 'high',
      dueDate: null,
    });
  });

  it('clears the title input after submission', async () => {
    const user = userEvent.setup();
    render(<TodoForm onAdd={vi.fn()} />);

    const input = screen.getByPlaceholderText('What needs to be done?');
    await user.type(input, 'Test task');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    expect(input).toHaveValue('');
  });

  it('submits on Enter key press', async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<TodoForm onAdd={onAdd} />);

    await user.type(screen.getByPlaceholderText('What needs to be done?'), 'Enter task{Enter}');

    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Enter task' })
    );
  });

  it('does not submit when title is only whitespace', async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<TodoForm onAdd={onAdd} />);

    await user.type(screen.getByPlaceholderText('What needs to be done?'), '   {Enter}');
    expect(onAdd).not.toHaveBeenCalled();
  });

  it('includes due date when provided', async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<TodoForm onAdd={onAdd} />);

    await user.type(screen.getByPlaceholderText('What needs to be done?'), 'Task with date');
    await user.type(screen.getByLabelText('Due date'), '2026-03-15');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({ dueDate: '2026-03-15' })
    );
  });
});
