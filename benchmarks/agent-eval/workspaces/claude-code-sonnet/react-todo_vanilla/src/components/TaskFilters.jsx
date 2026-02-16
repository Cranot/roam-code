import React from 'react';
import { CATEGORIES, PRIORITIES, FILTER_STATUSES, SORT_OPTIONS } from '../types';
import styles from './TaskFilters.module.css';

const TaskFilters = ({ filters, onFilterChange }) => {
  return (
    <div className={styles.filtersContainer}>
      <div className={styles.filterGroup}>
        <label className={styles.filterLabel}>Status:</label>
        <select
          value={filters.status}
          onChange={(e) => onFilterChange({ ...filters, status: e.target.value })}
          className={styles.filterSelect}
        >
          {FILTER_STATUSES.map(status => (
            <option key={status} value={status}>
              {status.charAt(0).toUpperCase() + status.slice(1)}
            </option>
          ))}
        </select>
      </div>

      <div className={styles.filterGroup}>
        <label className={styles.filterLabel}>Category:</label>
        <select
          value={filters.category}
          onChange={(e) => onFilterChange({ ...filters, category: e.target.value })}
          className={styles.filterSelect}
        >
          <option value="all">All</option>
          {CATEGORIES.map(category => (
            <option key={category} value={category}>
              {category.charAt(0).toUpperCase() + category.slice(1)}
            </option>
          ))}
        </select>
      </div>

      <div className={styles.filterGroup}>
        <label className={styles.filterLabel}>Priority:</label>
        <select
          value={filters.priority}
          onChange={(e) => onFilterChange({ ...filters, priority: e.target.value })}
          className={styles.filterSelect}
        >
          <option value="all">All</option>
          {PRIORITIES.map(priority => (
            <option key={priority} value={priority}>
              {priority.charAt(0).toUpperCase() + priority.slice(1)}
            </option>
          ))}
        </select>
      </div>

      <div className={styles.filterGroup}>
        <label className={styles.filterLabel}>Sort by:</label>
        <select
          value={filters.sortBy}
          onChange={(e) => onFilterChange({ ...filters, sortBy: e.target.value })}
          className={styles.filterSelect}
        >
          {SORT_OPTIONS.map(option => (
            <option key={option} value={option}>
              {option === 'dueDate' ? 'Due Date' :
               option === 'createdAt' ? 'Created Date' :
               'Priority'}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
};

export default TaskFilters;
