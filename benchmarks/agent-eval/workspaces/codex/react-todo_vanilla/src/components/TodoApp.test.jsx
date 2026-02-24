import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { STORAGE_KEY } from '../constants';
import TodoApp from './TodoApp';

describe('TodoApp', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('adds and completes tasks while updating summary', async () => {
    const user = userEvent.setup();
    render(<TodoApp />);

    await user.type(screen.getByLabelText(/task title/i), 'Buy groceries{enter}');

    expect(screen.getByText('Buy groceries')).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === 'Total: 1')).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === 'Pending: 1')).toBeInTheDocument();

    await user.click(screen.getByRole('checkbox', { name: /mark buy groceries as complete/i }));

    expect(screen.getByText((_, element) => element?.textContent === 'Completed: 1')).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === 'Pending: 0')).toBeInTheDocument();
  });

  it('cancels edit when escape is pressed', async () => {
    const user = userEvent.setup();
    render(<TodoApp />);

    await user.type(screen.getByLabelText(/task title/i), 'Doctor appointment{enter}');
    await user.click(screen.getByRole('button', { name: /edit doctor appointment/i }));

    const editTitleInput = screen.getByLabelText(/edit task title/i);
    await user.clear(editTitleInput);
    await user.type(editTitleInput, 'Changed title');
    await user.keyboard('{Escape}');

    expect(screen.queryByLabelText(/edit task title/i)).not.toBeInTheDocument();
    expect(screen.getByText('Doctor appointment')).toBeInTheDocument();
  });

  it('persists tasks into localStorage', async () => {
    const user = userEvent.setup();
    const storageSpy = vi.spyOn(Storage.prototype, 'setItem');
    render(<TodoApp />);

    await user.type(screen.getByLabelText(/task title/i), 'Persist me{enter}');

    expect(storageSpy).toHaveBeenCalledWith(STORAGE_KEY, expect.any(String));
    storageSpy.mockRestore();
  });
});
