import { useEffect, useMemo, useState } from 'react';
import { CATEGORY_OPTIONS, PRIORITY_OPTIONS } from '../constants';
import { isTaskOverdue } from '../utils/tasks';
import styles from './TaskItem.module.css';

function formatDueDate(dateStamp) {
  if (!dateStamp) {
    return 'No due date';
  }

  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(new Date(`${dateStamp}T00:00:00`));
}

function TaskItem({ task, onToggleComplete, onDeleteTask, onUpdateTask }) {
  const [isEditing, setIsEditing] = useState(false);
  const [editState, setEditState] = useState({
    title: task.title,
    description: task.description,
    category: task.category,
    priority: task.priority,
    dueDate: task.dueDate,
  });

  const overdue = useMemo(() => isTaskOverdue(task), [task]);

  useEffect(() => {
    if (!isEditing) {
      setEditState({
        title: task.title,
        description: task.description,
        category: task.category,
        priority: task.priority,
        dueDate: task.dueDate,
      });
    }
  }, [isEditing, task]);

  const handleEditChange = (event) => {
    const { name, value } = event.target;
    setEditState((currentEditState) => ({ ...currentEditState, [name]: value }));
  };

  const handleSave = (event) => {
    event.preventDefault();

    if (!editState.title.trim()) {
      return;
    }

    onUpdateTask(task.id, editState);
    setIsEditing(false);
  };

  const handleCancel = () => {
    setEditState({
      title: task.title,
      description: task.description,
      category: task.category,
      priority: task.priority,
      dueDate: task.dueDate,
    });
    setIsEditing(false);
  };

  const handleEscapeCancel = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      handleCancel();
    }
  };

  if (isEditing) {
    return (
      <li className={styles.item}>
        <form className={styles.editForm} onSubmit={handleSave} onKeyDown={handleEscapeCancel}>
          <label className={styles.field}>
            <span>Edit task title</span>
            <input
              autoFocus
              name="title"
              value={editState.title}
              onChange={handleEditChange}
              required
            />
          </label>

          <label className={styles.field}>
            <span>Edit description</span>
            <textarea name="description" rows={2} value={editState.description} onChange={handleEditChange} />
          </label>

          <div className={styles.editRow}>
            <label className={styles.field}>
              <span>Category</span>
              <select name="category" value={editState.category} onChange={handleEditChange}>
                {CATEGORY_OPTIONS.map((category) => (
                  <option key={category} value={category}>
                    {category}
                  </option>
                ))}
              </select>
            </label>

            <label className={styles.field}>
              <span>Priority</span>
              <select name="priority" value={editState.priority} onChange={handleEditChange}>
                {PRIORITY_OPTIONS.map((priority) => (
                  <option key={priority} value={priority}>
                    {priority}
                  </option>
                ))}
              </select>
            </label>

            <label className={styles.field}>
              <span>Due date</span>
              <input name="dueDate" type="date" value={editState.dueDate} onChange={handleEditChange} />
            </label>
          </div>

          <div className={styles.actions}>
            <button type="submit">Save</button>
            <button type="button" className={styles.ghostButton} onClick={handleCancel}>
              Cancel
            </button>
          </div>
          <p className={styles.hint}>Shortcut: press Escape to cancel edit.</p>
        </form>
      </li>
    );
  }

  return (
    <li className={`${styles.item} ${task.completed ? styles.completed : ''} ${overdue ? styles.overdue : ''}`}>
      <div className={styles.content}>
        <label className={styles.checkboxLabel}>
          <input
            type="checkbox"
            checked={task.completed}
            onChange={() => onToggleComplete(task.id)}
            aria-label={`Mark ${task.title} as complete`}
          />
          <span className={styles.title}>{task.title}</span>
        </label>

        {task.description && <p className={styles.description}>{task.description}</p>}

        <div className={styles.meta}>
          <span className={styles.badge}>{task.category}</span>
          <span className={`${styles.badge} ${styles[task.priority]}`}>{task.priority}</span>
          <span className={`${styles.badge} ${overdue ? styles.overdueTag : ''}`}>
            due {formatDueDate(task.dueDate)}
            {overdue && <strong> (Overdue)</strong>}
          </span>
        </div>
      </div>

      <div className={styles.actions}>
        <button type="button" className={styles.ghostButton} onClick={() => setIsEditing(true)}>
          Edit {task.title}
        </button>
        <button type="button" className={styles.deleteButton} onClick={() => onDeleteTask(task.id)}>
          Delete {task.title}
        </button>
      </div>
    </li>
  );
}

export default TaskItem;
