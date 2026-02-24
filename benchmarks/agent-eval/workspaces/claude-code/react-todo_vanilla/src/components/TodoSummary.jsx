import styles from './TodoSummary.module.css';

export default function TodoSummary({ summary }) {
  return (
    <div className={styles.summary}>
      <span className={styles.stat}>
        <strong>{summary.total}</strong> total
      </span>
      <span className={styles.divider}>|</span>
      <span className={`${styles.stat} ${styles.completed}`}>
        <strong>{summary.completed}</strong> completed
      </span>
      <span className={styles.divider}>|</span>
      <span className={`${styles.stat} ${styles.pending}`}>
        <strong>{summary.pending}</strong> pending
      </span>
    </div>
  );
}
