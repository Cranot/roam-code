import { useState, useRef, useEffect } from 'react';
import { CATEGORIES, PRIORITIES } from '../hooks/useTodos';
import styles from './TodoItem.module.css';

function isOverdue(dueDate) {
  if (!dueDate) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return new Date(dueDate + 'T00:00:00') < today;
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export default function TodoItem({ todo, onToggle, onUpdate, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(todo.title);
  const [editCategory, setEditCategory] = useState(todo.category);
  const [editPriority, setEditPriority] = useState(todo.priority);
  const [editDueDate, setEditDueDate] = useState(todo.dueDate || '');
  const inputRef = useRef(null);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const startEdit = () => {
    setEditTitle(todo.title);
    setEditCategory(todo.category);
    setEditPriority(todo.priority);
    setEditDueDate(todo.dueDate || '');
    setEditing(true);
  };

  const saveEdit = () => {
    const trimmed = editTitle.trim();
    if (!trimmed) return;
    onUpdate(todo.id, {
      title: trimmed,
      category: editCategory,
      priority: editPriority,
      dueDate: editDueDate || null,
    });
    setEditing(false);
  };

  const cancelEdit = () => {
    setEditing(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      saveEdit();
    } else if (e.key === 'Escape') {
      cancelEdit();
    }
  };

  const overdue = !todo.completed && isOverdue(todo.dueDate);

  const itemClass = [
    styles.item,
    todo.completed ? styles.completed : '',
    overdue ? styles.overdue : '',
  ]
    .filter(Boolean)
    .join(' ');

  if (editing) {
    return (
      <li className={`${styles.item} ${styles.editing}`}>
        <div className={styles.editForm}>
          <input
            ref={inputRef}
            type="text"
            className={styles.editInput}
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onKeyDown={handleKeyDown}
            aria-label="Edit task title"
          />
          <div className={styles.editOptions}>
            <select
              value={editCategory}
              onChange={(e) => setEditCategory(e.target.value)}
              className={styles.editSelect}
              aria-label="Edit category"
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c.charAt(0).toUpperCase() + c.slice(1)}
                </option>
              ))}
            </select>
            <select
              value={editPriority}
              onChange={(e) => setEditPriority(e.target.value)}
              className={styles.editSelect}
              aria-label="Edit priority"
            >
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p.charAt(0).toUpperCase() + p.slice(1)}
                </option>
              ))}
            </select>
            <input
              type="date"
              className={styles.editDate}
              value={editDueDate}
              onChange={(e) => setEditDueDate(e.target.value)}
              aria-label="Edit due date"
            />
          </div>
          <div className={styles.editActions}>
            <button className={styles.saveBtn} onClick={saveEdit}>
              Save
            </button>
            <button className={styles.cancelBtn} onClick={cancelEdit}>
              Cancel
            </button>
          </div>
        </div>
      </li>
    );
  }

  return (
    <li className={itemClass}>
      <div className={styles.content}>
        <input
          type="checkbox"
          className={styles.checkbox}
          checked={todo.completed}
          onChange={() => onToggle(todo.id)}
          aria-label={`Mark "${todo.title}" as ${todo.completed ? 'incomplete' : 'complete'}`}
        />
        <div className={styles.details}>
          <span className={styles.title}>{todo.title}</span>
          <div className={styles.meta}>
            <span className={`${styles.category} ${styles[`cat-${todo.category}`]}`}>
              {todo.category}
            </span>
            <span className={`${styles.priority} ${styles[`pri-${todo.priority}`]}`}>
              {todo.priority}
            </span>
            {todo.dueDate && (
              <span className={`${styles.dueDate} ${overdue ? styles.overdueBadge : ''}`}>
                {overdue ? 'Overdue: ' : 'Due: '}
                {formatDate(todo.dueDate)}
              </span>
            )}
          </div>
        </div>
      </div>
      <div className={styles.actions}>
        <button className={styles.editBtn} onClick={startEdit} aria-label="Edit task">
          Edit
        </button>
        <button
          className={styles.deleteBtn}
          onClick={() => onDelete(todo.id)}
          aria-label="Delete task"
        >
          Delete
        </button>
      </div>
    </li>
  );
}
