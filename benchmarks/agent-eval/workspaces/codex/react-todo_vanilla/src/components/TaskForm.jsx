import { useState } from 'react';
import { CATEGORY_OPTIONS, PRIORITY_OPTIONS } from '../constants';
import styles from './TaskForm.module.css';

const INITIAL_FORM_STATE = {
  title: '',
  description: '',
  category: 'work',
  priority: 'medium',
  dueDate: '',
};

function TaskForm({ onAddTask }) {
  const [formState, setFormState] = useState(INITIAL_FORM_STATE);

  const handleChange = (event) => {
    const { name, value } = event.target;
    setFormState((currentFormState) => ({ ...currentFormState, [name]: value }));
  };

  const handleSubmit = (event) => {
    event.preventDefault();

    if (!formState.title.trim()) {
      return;
    }

    onAddTask(formState);
    setFormState(INITIAL_FORM_STATE);
  };

  return (
    <section className={styles.panel} aria-label="Create task">
      <h2>Add Task</h2>
      <form className={styles.form} onSubmit={handleSubmit}>
        <label className={styles.field}>
          <span>Task title</span>
          <input
            name="title"
            value={formState.title}
            onChange={handleChange}
            placeholder="Write quarterly report"
            required
          />
        </label>

        <label className={styles.field}>
          <span>Description</span>
          <textarea
            name="description"
            value={formState.description}
            onChange={handleChange}
            rows={2}
            placeholder="Optional notes"
          />
        </label>

        <div className={styles.row}>
          <label className={styles.field}>
            <span>Category</span>
            <select name="category" value={formState.category} onChange={handleChange}>
              {CATEGORY_OPTIONS.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.field}>
            <span>Priority</span>
            <select name="priority" value={formState.priority} onChange={handleChange}>
              {PRIORITY_OPTIONS.map((priority) => (
                <option key={priority} value={priority}>
                  {priority}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.field}>
            <span>Due date</span>
            <input name="dueDate" type="date" value={formState.dueDate} onChange={handleChange} />
          </label>
        </div>

        <div className={styles.actions}>
          <button type="submit">Add task</button>
          <p>Shortcut: press Enter in the title field to add.</p>
        </div>
      </form>
    </section>
  );
}

export default TaskForm;
