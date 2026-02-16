import { useState, useEffect } from 'react'
import AddTask from './components/AddTask'
import TaskFilters from './components/TaskFilters'
import TaskStats from './components/TaskStats'
import TaskItem from './components/TaskItem'
import { loadTasks, saveTasks } from './utils/localStorage'
import { filterTasks, sortTasks, getTaskStats } from './utils/taskUtils'
import './App.css'

function App() {
  const [tasks, setTasks] = useState([])
  const [filters, setFilters] = useState({
    status: 'all',
    category: 'all',
    priority: 'all',
    sortBy: 'createdAt'
  })

  useEffect(() => {
    const loadedTasks = loadTasks()
    setTasks(loadedTasks)
  }, [])

  useEffect(() => {
    saveTasks(tasks)
  }, [tasks])

  const handleAddTask = (task) => {
    setTasks(prev => [...prev, task])
  }

  const handleUpdateTask = (id, updates) => {
    setTasks(prev => prev.map(task =>
      task.id === id ? { ...task, ...updates } : task
    ))
  }

  const handleDeleteTask = (id) => {
    setTasks(prev => prev.filter(task => task.id !== id))
  }

  const handleToggleComplete = (id) => {
    setTasks(prev => prev.map(task =>
      task.id === id ? { ...task, completed: !task.completed } : task
    ))
  }

  const filteredTasks = filterTasks(tasks, filters)
  const sortedTasks = sortTasks(filteredTasks, filters.sortBy)
  const stats = getTaskStats(tasks)

  return (
    <div className="app">
      <div className="container">
        <header className="header">
          <h1>Todo App</h1>
          <p>Organize your tasks efficiently</p>
        </header>

        <TaskStats stats={stats} />

        <AddTask onAdd={handleAddTask} />

        <TaskFilters filters={filters} onFilterChange={setFilters} />

        <div className="taskList">
          {sortedTasks.length === 0 ? (
            <div className="emptyState">
              <p>No tasks found. Add your first task to get started!</p>
            </div>
          ) : (
            sortedTasks.map(task => (
              <TaskItem
                key={task.id}
                task={task}
                onUpdate={handleUpdateTask}
                onDelete={handleDeleteTask}
                onToggle={handleToggleComplete}
              />
            ))
          )}
        </div>
      </div>
    </div>
  )
}

export default App
