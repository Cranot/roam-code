import { describe, it, expect } from 'vitest';
import {
  isOverdue,
  filterTasks,
  sortTasks,
  getTaskStats,
  generateId
} from '../taskUtils';

describe('taskUtils', () => {
  describe('isOverdue', () => {
    it('should return false for future dates', () => {
      const futureDate = new Date();
      futureDate.setDate(futureDate.getDate() + 1);
      expect(isOverdue(futureDate.toISOString().split('T')[0])).toBe(false);
    });

    it('should return true for past dates', () => {
      const pastDate = new Date();
      pastDate.setDate(pastDate.getDate() - 1);
      expect(isOverdue(pastDate.toISOString().split('T')[0])).toBe(true);
    });

    it('should return false for empty date', () => {
      expect(isOverdue('')).toBe(false);
    });
  });

  describe('filterTasks', () => {
    const tasks = [
      { id: '1', title: 'Task 1', completed: false, category: 'work', priority: 'high' },
      { id: '2', title: 'Task 2', completed: true, category: 'personal', priority: 'medium' },
      { id: '3', title: 'Task 3', completed: false, category: 'work', priority: 'low' }
    ];

    it('should filter by status', () => {
      const active = filterTasks(tasks, { status: 'active', category: 'all', priority: 'all' });
      expect(active).toHaveLength(2);
    });

    it('should filter by category', () => {
      const work = filterTasks(tasks, { status: 'all', category: 'work', priority: 'all' });
      expect(work).toHaveLength(2);
    });

    it('should filter by priority', () => {
      const high = filterTasks(tasks, { status: 'all', category: 'all', priority: 'high' });
      expect(high).toHaveLength(1);
    });
  });

  describe('generateId', () => {
    it('should generate unique IDs', () => {
      const id1 = generateId();
      const id2 = generateId();
      expect(id1).not.toBe(id2);
    });
  });
});
