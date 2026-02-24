import styles from './TaskSummary.module.css';

function TaskSummary({ summary }) {
  return (
    <section className={styles.panel} aria-label="Task summary">
      <h2>Summary</h2>
      <div className={styles.metrics}>
        <p>
          <strong>Total:</strong> {summary.total}
        </p>
        <p>
          <strong>Completed:</strong> {summary.completed}
        </p>
        <p>
          <strong>Pending:</strong> {summary.pending}
        </p>
      </div>
    </section>
  );
}

export default TaskSummary;
