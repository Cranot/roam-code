import { describe, it, expect, beforeEach, vi } from 'vitest'
import { loadTasks, saveTasks, clearTasks } from '../utils/localStorage'

describe('localStorage', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  describe('loadTasks', () => {
    it('returns empty array when no tasks stored', () => {
      localStorage.getItem.mockReturnValue(null)
      expect(loadTasks()).toEqual([])
    })

    it('returns parsed tasks from localStorage', () => {
      const tasks = [{ id: '1', title: 'Test Task', completed: false }]
      localStorage.getItem.mockReturnValue(JSON.stringify(tasks))
      expect(loadTasks()).toEqual(tasks)
    })

    it('returns empty array on parse error', () => {
      localStorage.getItem.mockReturnValue('invalid json')
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
      expect(loadTasks()).toEqual([])
      expect(consoleError).toHaveBeenCalled()
      consoleError.mockRestore()
    })
  })

  describe('saveTasks', () => {
    it('saves tasks to localStorage', () => {
      const tasks = [{ id: '1', title: 'Test Task', completed: false }]
      saveTasks(tasks)
      expect(localStorage.setItem).toHaveBeenCalledWith(
        'react-todo-app-tasks',
        JSON.stringify(tasks)
      )
    })

    it('handles save errors gracefully', () => {
      localStorage.setItem.mockImplementation(() => {
        throw new Error('Storage full')
      })
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
      saveTasks([])
      expect(consoleError).toHaveBeenCalled()
      consoleError.mockRestore()
    })
  })

  describe('clearTasks', () => {
    it('removes tasks from localStorage', () => {
      clearTasks()
      expect(localStorage.removeItem).toHaveBeenCalledWith('react-todo-app-tasks')
    })

    it('handles clear errors gracefully', () => {
      localStorage.removeItem.mockImplementation(() => {
        throw new Error('Clear failed')
      })
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
      clearTasks()
      expect(consoleError).toHaveBeenCalled()
      consoleError.mockRestore()
    })
  })
})
