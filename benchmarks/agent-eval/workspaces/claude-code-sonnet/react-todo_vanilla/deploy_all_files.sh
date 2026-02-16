#!/bin/bash

# This script creates all remaining component files

echo "Creating TaskFilters component..."
cat > src/components/TaskFilters.jsx << 'EOF'
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
EOF

cat > src/components/TaskFilters.module.css << 'EOF'
.filtersContainer {
  display: flex;
  gap: 1rem;
  padding: 1rem;
  background: white;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
}

.filterGroup {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  flex: 1;
  min-width: 150px;
}

.filterLabel {
  font-size: 0.75rem;
  font-weight: 600;
  color: #4a5568;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.filterSelect {
  padding: 0.5rem;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  font-size: 0.875rem;
  background: white;
  cursor: pointer;
  transition: all 0.2s;
}

.filterSelect:hover {
  border-color: #cbd5e0;
}

.filterSelect:focus {
  outline: none;
  border-color: #4299e1;
  box-shadow: 0 0 0 3px rgba(66, 153, 225, 0.1);
}

@media (max-width: 640px) {
  .filtersContainer {
    flex-direction: column;
    gap: 0.75rem;
  }

  .filterGroup {
    min-width: 100%;
  }
}
EOF

echo "TaskFilters created"

echo "All component files created successfully!"
