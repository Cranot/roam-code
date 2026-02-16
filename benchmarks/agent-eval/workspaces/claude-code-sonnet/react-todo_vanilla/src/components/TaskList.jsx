import React from 'react';
import { TaskItem } from './TaskItem';
import styles from './TaskList.module.css';

const TaskList = ({ tasks, onUpdate, onDelete, onToggle }) => {
  if (tasks.length === 0) {
    return (
      <div className={styles.emptyState}>
        <p className={styles.emptyText}>No tasks found</p>
        <p className={styles.emptySubtext}>Add a new task to get started!</p>
      </div>
    );
  }

  return (
    <div className={styles.taskList}>
      {tasks.map(task => (
        <TaskItem
          key={task.id}
          task={task}
          onUpdate={onUpdate}
          onDelete={onDelete}
          onToggle={onToggle}
        />
      ))}
    </div>
  );
};

export default TaskList;
