import { CATEGORIES, PRIORITIES, SORT_OPTIONS } from '../hooks/useTodos';
import styles from './TodoFilters.module.css';

export default function TodoFilters({ filters, onFilterChange, sortBy, onSortChange }) {
  const handleChange = (key, value) => {
    onFilterChange({ ...filters, [key]: value });
  };

  return (
    <div className={styles.filters}>
      <div className={styles.filterGroup}>
        <label className={styles.label} htmlFor="filter-category">Category</label>
        <select
          id="filter-category"
          className={styles.select}
          value={filters.category}
          onChange={(e) => handleChange('category', e.target.value)}
        >
          <option value="all">All</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c.charAt(0).toUpperCase() + c.slice(1)}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.filterGroup}>
        <label className={styles.label} htmlFor="filter-priority">Priority</label>
        <select
          id="filter-priority"
          className={styles.select}
          value={filters.priority}
          onChange={(e) => handleChange('priority', e.target.value)}
        >
          <option value="all">All</option>
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {p.charAt(0).toUpperCase() + p.slice(1)}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.filterGroup}>
        <label className={styles.label} htmlFor="filter-status">Status</label>
        <select
          id="filter-status"
          className={styles.select}
          value={filters.status}
          onChange={(e) => handleChange('status', e.target.value)}
        >
          <option value="all">All</option>
          <option value="pending">Pending</option>
          <option value="completed">Completed</option>
        </select>
      </div>
      <div className={styles.filterGroup}>
        <label className={styles.label} htmlFor="sort-by">Sort by</label>
        <select
          id="sort-by"
          className={styles.select}
          value={sortBy}
          onChange={(e) => onSortChange(e.target.value)}
        >
          {SORT_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
