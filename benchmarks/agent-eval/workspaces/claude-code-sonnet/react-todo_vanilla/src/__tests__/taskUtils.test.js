import { describe, it, expect } from 'vitest'
import { isOverdue, filterTasks, sortTasks, getTaskStats } from '../utils/taskUtils'

describe('taskUtils', () => {
  describe('isOverdue', () => {
    it('returns false for null dueDate', () => {
      expect(isOverdue(null)).toBe(false)
    })

    it('returns false for future dates', () => {
      const tomorrow = new Date()
      tomorrow.setDate(tomorrow.getDate() + 1)
      expect(isOverdue(tomorrow.toISOString().split('T')[0])).toBe(false)
    })

    it('returns true for past dates', () => {
      const yesterday = new Date()
      yesterday.setDate(yesterday.getDate() - 1)
      expect(isOverdue(yesterday.toISOString().split('T')[0])).toBe(true)
    })

    it('returns false for today', () => {
      const today = new Date().toISOString().split('T')[0]
      expect(isOverdue(today)).toBe(false)
    })
  })

  describe('filterTasks', () => {
    const tasks = [
      { id: '1', title: 'Task 1', category: 'work', priority: 'high', completed: false },
      { id: '2', title: 'Task 2', category: 'personal', priority: 'low', completed: true },
      { id: '3', title: 'Task 3', category: 'work', priority: 'medium', completed: false },
    ]

    it('returns all tasks when no filters applied', () => {
      const result = filterTasks(tasks, { status: 'all', category: 'all', priority: 'all' })
      expect(result).toHaveLength(3)
    })

    it('filters by status - active', () => {
      const result = filterTasks(tasks, { status: 'active', category: 'all', priority: 'all' })
      expect(result).toHaveLength(2)
      expect(result.every(t => !t.completed)).toBe(true)
    })

    it('filters by status - completed', () => {
      const result = filterTasks(tasks, { status: 'completed', category: 'all', priority: 'all' })
      expect(result).toHaveLength(1)
      expect(result[0].completed).toBe(true)
    })

    it('filters by category', () => {
      const result = filterTasks(tasks, { status: 'all', category: 'work', priority: 'all' })
      expect(result).toHaveLength(2)
      expect(result.every(t => t.category === 'work')).toBe(true)
    })

    it('filters by priority', () => {
      const result = filterTasks(tasks, { status: 'all', category: 'all', priority: 'high' })
      expect(result).toHaveLength(1)
      expect(result[0].priority).toBe('high')
    })

    it('applies multiple filters', () => {
      const result = filterTasks(tasks, { status: 'active', category: 'work', priority: 'all' })
      expect(result).toHaveLength(2)
      expect(result.every(t => t.category === 'work' && !t.completed)).toBe(true)
    })
  })

  describe('sortTasks', () => {
    const tasks = [
      {
        id: '1',
        title: 'Task 1',
        priority: 'low',
        dueDate: '2026-02-20',
        createdAt: '2026-02-15T10:00:00Z'
      },
      {
        id: '2',
        title: 'Task 2',
        priority: 'high',
        dueDate: '2026-02-18',
        createdAt: '2026-02-16T10:00:00Z'
      },
      {
        id: '3',
        title: 'Task 3',
        priority: 'medium',
        dueDate: null,
        createdAt: '2026-02-14T10:00:00Z'
      },
    ]

    it('sorts by due date', () => {
      const result = sortTasks(tasks, 'dueDate')
      expect(result[0].id).toBe('2')
      expect(result[1].id).toBe('1')
      expect(result[2].id).toBe('3') // null dates come last
    })

    it('sorts by priority', () => {
      const result = sortTasks(tasks, 'priority')
      expect(result[0].priority).toBe('high')
      expect(result[1].priority).toBe('medium')
      expect(result[2].priority).toBe('low')
    })

    it('sorts by created date', () => {
      const result = sortTasks(tasks, 'createdAt')
      expect(result[0].id).toBe('2')
      expect(result[1].id).toBe('1')
      expect(result[2].id).toBe('3')
    })
  })

  describe('getTaskStats', () => {
    it('returns correct stats', () => {
      const yesterday = new Date()
      yesterday.setDate(yesterday.getDate() - 1)

      const tasks = [
        { id: '1', completed: false, dueDate: null },
        { id: '2', completed: true, dueDate: null },
        { id: '3', completed: false, dueDate: yesterday.toISOString().split('T')[0] },
        { id: '4', completed: false, dueDate: null },
      ]

      const stats = getTaskStats(tasks)
      expect(stats.total).toBe(4)
      expect(stats.completed).toBe(1)
      expect(stats.pending).toBe(3)
      expect(stats.overdue).toBe(1)
    })

    it('returns zero stats for empty array', () => {
      const stats = getTaskStats([])
      expect(stats.total).toBe(0)
      expect(stats.completed).toBe(0)
      expect(stats.pending).toBe(0)
      expect(stats.overdue).toBe(0)
    })
  })
})
