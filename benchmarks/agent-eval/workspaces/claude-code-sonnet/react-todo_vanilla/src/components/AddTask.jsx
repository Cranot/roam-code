import React, { useState } from 'react';
import { CATEGORIES, PRIORITIES } from '../types';
import { generateId } from '../utils/taskUtils';
import styles from './AddTask.module.css';

const AddTask = ({ onAdd }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [formData, setFormData] = useState({
    title: '',
    description: '',
    category: 'personal',
    priority: 'medium',
    dueDate: ''
  });

  const handleSubmit = (e) => {
    e.preventDefault();

    if (!formData.title.trim()) return;

    const newTask = {
      id: generateId(),
      title: formData.title.trim(),
      description: formData.description.trim(),
      category: formData.category,
      priority: formData.priority,
      dueDate: formData.dueDate,
      completed: false,
      createdAt: new Date().toISOString()
    };

    onAdd(newTask);

    setFormData({
      title: '',
      description: '',
      category: 'personal',
      priority: 'medium',
      dueDate: ''
    });
    setIsExpanded(false);
  };

  const handleCancel = () => {
    setFormData({
      title: '',
      description: '',
      category: 'personal',
      priority: 'medium',
      dueDate: ''
    });
    setIsExpanded(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    } else if (e.key === 'Escape') {
      handleCancel();
    }
  };

  if (!isExpanded) {
    return (
      <div className={styles.addTaskContainer}>
        <button
          className={styles.expandButton}
          onClick={() => setIsExpanded(true)}
        >
          + Add New Task
        </button>
      </div>
    );
  }

  return (
    <div className={styles.addTaskContainer}>
      <form className={styles.form} onSubmit={handleSubmit}>
        <div className={styles.formGroup}>
          <input
            type="text"
            placeholder="Task title *"
            value={formData.title}
            onChange={(e) => setFormData({ ...formData, title: e.target.value })}
            onKeyDown={handleKeyDown}
            className={styles.input}
            autoFocus
          />
        </div>

        <div className={styles.formGroup}>
          <textarea
            placeholder="Description (optional)"
            value={formData.description}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            className={styles.textarea}
            rows="3"
          />
        </div>

        <div className={styles.formRow}>
          <div className={styles.formGroup}>
            <label className={styles.label}>Category</label>
            <select
              value={formData.category}
              onChange={(e) => setFormData({ ...formData, category: e.target.value })}
              className={styles.select}
            >
              {CATEGORIES.map(cat => (
                <option key={cat} value={cat}>
                  {cat.charAt(0).toUpperCase() + cat.slice(1)}
                </option>
              ))}
            </select>
          </div>

          <div className={styles.formGroup}>
            <label className={styles.label}>Priority</label>
            <select
              value={formData.priority}
              onChange={(e) => setFormData({ ...formData, priority: e.target.value })}
              className={styles.select}
            >
              {PRIORITIES.map(pri => (
                <option key={pri} value={pri}>
                  {pri.charAt(0).toUpperCase() + pri.slice(1)}
                </option>
              ))}
            </select>
          </div>

          <div className={styles.formGroup}>
            <label className={styles.label}>Due Date</label>
            <input
              type="date"
              value={formData.dueDate}
              onChange={(e) => setFormData({ ...formData, dueDate: e.target.value })}
              className={styles.dateInput}
            />
          </div>
        </div>

        <div className={styles.buttonGroup}>
          <button type="submit" className={styles.submitButton}>
            Add Task
          </button>
          <button
            type="button"
            onClick={handleCancel}
            className={styles.cancelButton}
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
};

export default AddTask;
