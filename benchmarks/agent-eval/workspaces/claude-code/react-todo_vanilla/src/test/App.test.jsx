import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

describe('App', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders the app title', () => {
    render(<App />);
    expect(screen.getByText('Todo App')).toBeInTheDocument();
  });

  it('shows zero counts initially', () => {
    render(<App />);
    const zeros = screen.getAllByText('0');
    expect(zeros.length).toBeGreaterThanOrEqual(3);
  });

  it('shows empty state message', () => {
    render(<App />);
    expect(screen.getByText(/No tasks found/)).toBeInTheDocument();
  });

  it('can add and display a task', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByPlaceholderText('What needs to be done?'), 'New task');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    expect(screen.getByText('New task')).toBeInTheDocument();
    // Summary should show "1 total" — find the "total" text's sibling strong
    const totalLabel = screen.getByText('total');
    expect(totalLabel.closest('span').querySelector('strong').textContent).toBe('1');
  });

  it('can complete and delete a task', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByPlaceholderText('What needs to be done?'), 'Task to complete');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await user.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('checkbox')).toBeChecked();

    await user.click(screen.getByRole('button', { name: 'Delete task' }));
    expect(screen.queryByText('Task to complete')).not.toBeInTheDocument();
  });

  it('can filter tasks by status', async () => {
    const user = userEvent.setup();
    render(<App />);

    // Add two tasks
    await user.type(screen.getByPlaceholderText('What needs to be done?'), 'Task A');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await user.type(screen.getByPlaceholderText('What needs to be done?'), 'Task B');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    // Complete Task A (sorted by createdAt desc, so Task B is first, Task A is second)
    const checkboxA = screen.getByLabelText(/Mark "Task A"/);
    await user.click(checkboxA);

    // Filter to pending only
    await user.selectOptions(screen.getByLabelText('Status'), 'pending');

    expect(screen.queryByText('Task A')).not.toBeInTheDocument();
    expect(screen.getByText('Task B')).toBeInTheDocument();
  });
});
