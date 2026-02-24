import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import TodoSummary from '../components/TodoSummary';

describe('TodoSummary', () => {
  it('displays total, completed, and pending counts', () => {
    render(<TodoSummary summary={{ total: 10, completed: 3, pending: 7 }} />);
    expect(screen.getByText('10')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('displays labels for each count', () => {
    render(<TodoSummary summary={{ total: 5, completed: 2, pending: 3 }} />);
    expect(screen.getByText('total')).toBeInTheDocument();
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.getByText('pending')).toBeInTheDocument();
  });

  it('displays zero counts correctly', () => {
    render(<TodoSummary summary={{ total: 0, completed: 0, pending: 0 }} />);
    const zeros = screen.getAllByText('0');
    expect(zeros).toHaveLength(3);
  });
});
