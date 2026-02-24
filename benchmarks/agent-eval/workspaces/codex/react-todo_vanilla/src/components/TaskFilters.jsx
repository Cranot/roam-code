import styles from './TaskFilters.module.css';

function TaskFilters({ filters, onFilterChange }) {
  return (
    <section className={styles.panel} aria-label="Task filters">
      <h2>Filter and Sort</h2>
      <div className={styles.controls}>
        <label className={styles.field}>
          <span>Category</span>
          <select
            value={filters.category}
            onChange={(event) => onFilterChange('category', event.target.value)}
          >
            <option value="all">all</option>
            <option value="work">work</option>
            <option value="personal">personal</option>
            <option value="shopping">shopping</option>
            <option value="health">health</option>
          </select>
        </label>

        <label className={styles.field}>
          <span>Priority</span>
          <select
            value={filters.priority}
            onChange={(event) => onFilterChange('priority', event.target.value)}
          >
            <option value="all">all</option>
            <option value="high">high</option>
            <option value="medium">medium</option>
            <option value="low">low</option>
          </select>
        </label>

        <label className={styles.field}>
          <span>Status</span>
          <select value={filters.status} onChange={(event) => onFilterChange('status', event.target.value)}>
            <option value="all">all</option>
            <option value="completed">completed</option>
            <option value="pending">pending</option>
          </select>
        </label>

        <label className={styles.field}>
          <span>Sort by</span>
          <select value={filters.sortBy} onChange={(event) => onFilterChange('sortBy', event.target.value)}>
            <option value="creationDate">creation date</option>
            <option value="dueDate">due date</option>
            <option value="priority">priority</option>
          </select>
        </label>
      </div>
    </section>
  );
}

export default TaskFilters;
