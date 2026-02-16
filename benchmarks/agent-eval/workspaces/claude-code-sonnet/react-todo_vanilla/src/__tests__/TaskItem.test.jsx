import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TaskItem from '../components/TaskItem'

describe('TaskItem', () => {
  const mockTask = {
    id: '1',
    title: 'Test Task',
    description: 'Test description',
    category: 'work',
    priority: 'high',
    dueDate: '2026-02-20',
    completed: false,
    createdAt: '2026-02-15T10:00:00Z'
  }

  const mockHandlers = {
    onUpdate: vi.fn(),
    onDelete: vi.fn(),
    onToggle: vi.fn(),
  }

  it('renders task title', () => {
    render(<TaskItem task={mockTask} {...mockHandlers} />)
    expect(screen.getByText('Test Task')).toBeInTheDocument()
  })

  it('displays task metadata', () => {
    render(<TaskItem task={mockTask} {...mockHandlers} />)
    expect(screen.getByText('work')).toBeInTheDocument()
    expect(screen.getByText('high')).toBeInTheDocument()
  })

  it('shows due date', () => {
    render(<TaskItem task={mockTask} {...mockHandlers} />)
    expect(screen.getByText(/2026/)).toBeInTheDocument()
  })

  it('calls onToggle when checkbox is clicked', () => {
    render(<TaskItem task={mockTask} {...mockHandlers} />)
    const checkbox = screen.getByRole('checkbox')
    fireEvent.click(checkbox)
    expect(mockHandlers.onToggle).toHaveBeenCalledWith('1')
  })

  it('calls onDelete when delete button is clicked', () => {
    render(<TaskItem task={mockTask} {...mockHandlers} />)
    const deleteButton = screen.getByText('Delete')
    fireEvent.click(deleteButton)
    expect(mockHandlers.onDelete).toHaveBeenCalledWith('1')
  })

  it('enters edit mode when edit button is clicked', () => {
    render(<TaskItem task={mockTask} {...mockHandlers} />)
    const editButton = screen.getByText('Edit')
    fireEvent.click(editButton)
    expect(screen.getByDisplayValue('Test Task')).toBeInTheDocument()
    expect(screen.getByText('Save')).toBeInTheDocument()
    expect(screen.getByText('Cancel')).toBeInTheDocument()
  })

  it('saves changes when save button is clicked', async () => {
    const user = userEvent.setup()
    render(<TaskItem task={mockTask} {...mockHandlers} />)

    const editButton = screen.getByText('Edit')
    fireEvent.click(editButton)

    const titleInput = screen.getByDisplayValue('Test Task')
    await user.clear(titleInput)
    await user.type(titleInput, 'Updated Task')

    const saveButton = screen.getByText('Save')
    fireEvent.click(saveButton)

    expect(mockHandlers.onUpdate).toHaveBeenCalledWith('1', expect.objectContaining({
      title: 'Updated Task'
    }))
  })

  it('cancels edit when cancel button is clicked', () => {
    render(<TaskItem task={mockTask} {...mockHandlers} />)

    const editButton = screen.getByText('Edit')
    fireEvent.click(editButton)

    const cancelButton = screen.getByText('Cancel')
    fireEvent.click(cancelButton)

    expect(screen.queryByText('Save')).not.toBeInTheDocument()
    expect(screen.getByText('Edit')).toBeInTheDocument()
  })

  it('cancels edit on Escape key', () => {
    render(<TaskItem task={mockTask} {...mockHandlers} />)

    const editButton = screen.getByText('Edit')
    fireEvent.click(editButton)

    const titleInput = screen.getByDisplayValue('Test Task')
    fireEvent.keyDown(titleInput, { key: 'Escape' })

    expect(screen.queryByText('Save')).not.toBeInTheDocument()
    expect(screen.getByText('Edit')).toBeInTheDocument()
  })

  it('shows completed style for completed tasks', () => {
    const completedTask = { ...mockTask, completed: true }
    const { container } = render(<TaskItem task={completedTask} {...mockHandlers} />)
    const taskItem = container.firstChild
    expect(taskItem.className).toContain('completed')
  })

  it('shows overdue style for overdue tasks', () => {
    const yesterday = new Date()
    yesterday.setDate(yesterday.getDate() - 1)
    const overdueTask = {
      ...mockTask,
      dueDate: yesterday.toISOString().split('T')[0],
      completed: false
    }
    const { container } = render(<TaskItem task={overdueTask} {...mockHandlers} />)
    const taskItem = container.firstChild
    expect(taskItem.className).toContain('overdue')
  })
})
