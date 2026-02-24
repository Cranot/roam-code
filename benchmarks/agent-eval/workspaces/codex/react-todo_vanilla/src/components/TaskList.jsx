import TaskItem from './TaskItem';
import styles from './TaskList.module.css';

function TaskList({ tasks, onToggleComplete, onDeleteTask, onUpdateTask }) {
  return (
    <section className={styles.panel} aria-label="Task list">
      <h2>Tasks</h2>
      {tasks.length === 0 ? (
        <p className={styles.empty}>No tasks match the current filters.</p>
      ) : (
        <ul className={styles.list}>
          {tasks.map((task) => (
            <TaskItem
              key={task.id}
              task={task}
              onToggleComplete={onToggleComplete}
              onDeleteTask={onDeleteTask}
              onUpdateTask={onUpdateTask}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

export default TaskList;
