import { useState } from 'react'
import { isOverdue } from '../utils/taskUtils'
import { CATEGORIES, PRIORITIES } from '../types'
import styles from './TaskItem.module.css'

const TaskItem = ({ task, onUpdate, onDelete, onToggle }) => {
  const [isEditing, setIsEditing] = useState(false)
  const [editTitle, setEditTitle] = useState(task.title)
  const [editCategory, setEditCategory] = useState(task.category)
  const [editPriority, setEditPriority] = useState(task.priority)
  const [editDueDate, setEditDueDate] = useState(task.dueDate || '')

  const handleSave = () => {
    if (!editTitle.trim()) return

    onUpdate(task.id, {
      title: editTitle.trim(),
      category: editCategory,
      priority: editPriority,
      dueDate: editDueDate || null,
    })
    setIsEditing(false)
  }

  const handleCancel = () => {
    setEditTitle(task.title)
    setEditCategory(task.category)
    setEditPriority(task.priority)
    setEditDueDate(task.dueDate || '')
    setIsEditing(false)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      handleSave()
    } else if (e.key === 'Escape') {
      handleCancel()
    }
  }

  const overdue = !task.completed && isOverdue(task.dueDate)

  if (isEditing) {
    return (
      <div className={styles.taskItem}>
        <div className={styles.editForm}>
          <input
            type="text"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onKeyDown={handleKeyDown}
            className={styles.editInput}
            autoFocus
          />
          <div className={styles.editControls}>
            <select
              value={editCategory}
              onChange={(e) => setEditCategory(e.target.value)}
              className={styles.editSelect}
            >
              {CATEGORIES.map(cat => (
                <option key={cat} value={cat}>
                  {cat.charAt(0).toUpperCase() + cat.slice(1)}
                </option>
              ))}
            </select>

            <select
              value={editPriority}
              onChange={(e) => setEditPriority(e.target.value)}
              className={styles.editSelect}
            >
              {PRIORITIES.map(pri => (
                <option key={pri} value={pri}>
                  {pri.charAt(0).toUpperCase() + pri.slice(1)}
                </option>
              ))}
            </select>

            <input
              type="date"
              value={editDueDate}
              onChange={(e) => setEditDueDate(e.target.value)}
              className={styles.editDate}
            />

            <button onClick={handleSave} className={styles.saveButton}>
              Save
            </button>
            <button onClick={handleCancel} className={styles.cancelButton}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={`${styles.taskItem} ${task.completed ? styles.completed : ''} ${overdue ? styles.overdue : ''}`}>
      <input
        type="checkbox"
        checked={task.completed}
        onChange={() => onToggle(task.id)}
        className={styles.checkbox}
      />

      <div className={styles.content}>
        <div className={styles.title}>{task.title}</div>
        <div className={styles.meta}>
          <span className={styles.category}>{task.category}</span>
          <span className={`${styles.priority} ${styles[task.priority]}`}>
            {task.priority}
          </span>
          {task.dueDate && (
            <span className={styles.dueDate}>
              {new Date(task.dueDate).toLocaleDateString()}
            </span>
          )}
        </div>
      </div>

      <div className={styles.actions}>
        <button
          onClick={() => setIsEditing(true)}
          className={styles.editButton}
          aria-label="Edit task"
        >
          Edit
        </button>
        <button
          onClick={() => onDelete(task.id)}
          className={styles.deleteButton}
          aria-label="Delete task"
        >
          Delete
        </button>
      </div>
    </div>
  )
}

export default TaskItem
