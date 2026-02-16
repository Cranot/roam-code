import { CATEGORIES, PRIORITIES } from '../utils/taskUtils'
import styles from './Filters.module.css'

export const Filters = ({ filters, onFilterChange, sortBy, onSortChange }) => {
  return (
    <div className={styles.filters}>
      <div className={styles.filterGroup}>
        <label className={styles.label}>Filter by:</label>

        <select
          value={filters.category || ''}
          onChange={(e) => onFilterChange({ ...filters, category: e.target.value || null })}
          className={styles.select}
        >
          <option value="">All Categories</option>
          {CATEGORIES.map(cat => (
            <option key={cat} value={cat}>
              {cat.charAt(0).toUpperCase() + cat.slice(1)}
            </option>
          ))}
        </select>

        <select
          value={filters.priority || ''}
          onChange={(e) => onFilterChange({ ...filters, priority: e.target.value || null })}
          className={styles.select}
        >
          <option value="">All Priorities</option>
          {PRIORITIES.map(pri => (
            <option key={pri} value={pri}>
              {pri.charAt(0).toUpperCase() + pri.slice(1)}
            </option>
          ))}
        </select>

        <select
          value={filters.status || ''}
          onChange={(e) => onFilterChange({ ...filters, status: e.target.value || null })}
          className={styles.select}
        >
          <option value="">All Tasks</option>
          <option value="pending">Pending</option>
          <option value="completed">Completed</option>
        </select>
      </div>

      <div className={styles.sortGroup}>
        <label className={styles.label}>Sort by:</label>

        <select
          value={sortBy}
          onChange={(e) => onSortChange(e.target.value)}
          className={styles.select}
        >
          <option value="created">Date Created</option>
          <option value="dueDate">Due Date</option>
          <option value="priority">Priority</option>
        </select>
      </div>
    </div>
  )
}
