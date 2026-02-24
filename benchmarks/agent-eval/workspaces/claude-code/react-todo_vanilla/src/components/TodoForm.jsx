import { useState } from 'react';
import { CATEGORIES, PRIORITIES } from '../hooks/useTodos';
import styles from './TodoForm.module.css';

export default function TodoForm({ onAdd }) {
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState('personal');
  const [priority, setPriority] = useState('medium');
  const [dueDate, setDueDate] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) return;
    onAdd({ title: trimmed, category, priority, dueDate: dueDate || null });
    setTitle('');
    setDueDate('');
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      handleSubmit(e);
    }
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <div className={styles.inputRow}>
        <input
          type="text"
          className={styles.titleInput}
          placeholder="What needs to be done?"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={handleKeyDown}
          aria-label="Task title"
        />
        <button type="submit" className={styles.addBtn} disabled={!title.trim()}>
          Add
        </button>
      </div>
      <div className={styles.optionsRow}>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className={styles.select}
          aria-label="Category"
        >
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c.charAt(0).toUpperCase() + c.slice(1)}
            </option>
          ))}
        </select>
        <select
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
          className={styles.select}
          aria-label="Priority"
        >
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {p.charAt(0).toUpperCase() + p.slice(1)}
            </option>
          ))}
        </select>
        <input
          type="date"
          className={styles.dateInput}
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
          aria-label="Due date"
        />
      </div>
    </form>
  );
}
