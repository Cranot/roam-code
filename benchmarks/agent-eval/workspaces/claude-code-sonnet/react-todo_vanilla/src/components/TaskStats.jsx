import React from 'react';
import styles from './TaskStats.module.css';

const TaskStats = ({ stats }) => {
  return (
    <div className={styles.statsContainer}>
      <div className={styles.statItem}>
        <span className={styles.statValue}>{stats.total}</span>
        <span className={styles.statLabel}>Total</span>
      </div>
      <div className={styles.statItem}>
        <span className={styles.statValue}>{stats.completed}</span>
        <span className={styles.statLabel}>Completed</span>
      </div>
      <div className={styles.statItem}>
        <span className={styles.statValue}>{stats.pending}</span>
        <span className={styles.statLabel}>Pending</span>
      </div>
      {stats.overdue > 0 && (
        <div className={`${styles.statItem} ${styles.overdue}`}>
          <span className={styles.statValue}>{stats.overdue}</span>
          <span className={styles.statLabel}>Overdue</span>
        </div>
      )}
    </div>
  );
};

export default TaskStats;
