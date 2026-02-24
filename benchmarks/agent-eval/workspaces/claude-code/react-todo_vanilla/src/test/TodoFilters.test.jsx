import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TodoFilters from '../components/TodoFilters';

const defaultFilters = { category: 'all', priority: 'all', status: 'all' };

describe('TodoFilters', () => {
  it('renders all filter dropdowns', () => {
    render(
      <TodoFilters
        filters={defaultFilters}
        onFilterChange={vi.fn()}
        sortBy="createdAt"
        onSortChange={vi.fn()}
      />
    );
    expect(screen.getByLabelText('Category')).toBeInTheDocument();
    expect(screen.getByLabelText('Priority')).toBeInTheDocument();
    expect(screen.getByLabelText('Status')).toBeInTheDocument();
    expect(screen.getByLabelText('Sort by')).toBeInTheDocument();
  });

  it('calls onFilterChange when category changes', async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(
      <TodoFilters
        filters={defaultFilters}
        onFilterChange={onFilterChange}
        sortBy="createdAt"
        onSortChange={vi.fn()}
      />
    );

    await user.selectOptions(screen.getByLabelText('Category'), 'work');
    expect(onFilterChange).toHaveBeenCalledWith({ ...defaultFilters, category: 'work' });
  });

  it('calls onFilterChange when priority changes', async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(
      <TodoFilters
        filters={defaultFilters}
        onFilterChange={onFilterChange}
        sortBy="createdAt"
        onSortChange={vi.fn()}
      />
    );

    await user.selectOptions(screen.getByLabelText('Priority'), 'high');
    expect(onFilterChange).toHaveBeenCalledWith({ ...defaultFilters, priority: 'high' });
  });

  it('calls onFilterChange when status changes', async () => {
    const user = userEvent.setup();
    const onFilterChange = vi.fn();
    render(
      <TodoFilters
        filters={defaultFilters}
        onFilterChange={onFilterChange}
        sortBy="createdAt"
        onSortChange={vi.fn()}
      />
    );

    await user.selectOptions(screen.getByLabelText('Status'), 'completed');
    expect(onFilterChange).toHaveBeenCalledWith({ ...defaultFilters, status: 'completed' });
  });

  it('calls onSortChange when sort changes', async () => {
    const user = userEvent.setup();
    const onSortChange = vi.fn();
    render(
      <TodoFilters
        filters={defaultFilters}
        onFilterChange={vi.fn()}
        sortBy="createdAt"
        onSortChange={onSortChange}
      />
    );

    await user.selectOptions(screen.getByLabelText('Sort by'), 'priority');
    expect(onSortChange).toHaveBeenCalledWith('priority');
  });
});
