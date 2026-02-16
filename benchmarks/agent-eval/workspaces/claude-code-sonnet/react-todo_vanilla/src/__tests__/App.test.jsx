import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'

vi.mock('../utils/localStorage', () => ({
  loadTasks: vi.fn(() => []),
  saveTasks: vi.fn(),
}))

describe('App', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the app title', () => {
    render(<App />)
    expect(screen.getByText('Todo App')).toBeInTheDocument()
  })

  it('shows empty state when no tasks', () => {
    render(<App />)
    expect(screen.getByText(/No tasks found/i)).toBeInTheDocument()
  })

  it('displays task stats', () => {
    render(<App />)
    expect(screen.getAllByText('Total')[0]).toBeInTheDocument()
    expect(screen.getAllByText('Completed')[0]).toBeInTheDocument()
    expect(screen.getAllByText('Pending')[0]).toBeInTheDocument()
  })

  it('shows add task button', () => {
    render(<App />)
    expect(screen.getByText('+ Add New Task')).toBeInTheDocument()
  })

  it('expands task form when add button is clicked', async () => {
    render(<App />)
    const addButton = screen.getByText('+ Add New Task')
    fireEvent.click(addButton)

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Task title/i)).toBeInTheDocument()
    })
  })

  it('adds a new task', async () => {
    const user = userEvent.setup()
    render(<App />)

    const addButton = screen.getByText('+ Add New Task')
    fireEvent.click(addButton)

    const titleInput = await screen.findByPlaceholderText(/Task title/i)
    await user.type(titleInput, 'New Test Task')

    const submitButton = screen.getByText('Add Task')
    fireEvent.click(submitButton)

    await waitFor(() => {
      expect(screen.getByText('New Test Task')).toBeInTheDocument()
    })
  })

  it('displays filters', () => {
    render(<App />)
    expect(screen.getByText('Status:')).toBeInTheDocument()
    expect(screen.getByText('Category:')).toBeInTheDocument()
    expect(screen.getByText('Priority:')).toBeInTheDocument()
    expect(screen.getByText('Sort by:')).toBeInTheDocument()
  })
})
