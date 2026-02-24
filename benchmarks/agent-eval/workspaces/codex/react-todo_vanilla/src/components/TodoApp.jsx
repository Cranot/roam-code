import { useMemo, useState } from 'react';
import { DEFAULT_FILTERS } from '../constants';
import { useTodos } from '../hooks/useTodos';
import { applyFiltersAndSort } from '../utils/tasks';
import TaskFilters from './TaskFilters';
import TaskForm from './TaskForm';
import TaskList from './TaskList';
import TaskSummary from './TaskSummary';
import styles from './TodoApp.module.css';

function TodoApp() {
  const { tasks, addTask, updateTask, toggleTaskCompletion, deleteTask } = useTodos();
  const [filters, setFilters] = useState(DEFAULT_FILTERS);

  const visibleTasks = useMemo(() => applyFiltersAndSort(tasks, filters), [tasks, filters]);

  const summary = useMemo(() => {
    const completed = tasks.filter((task) => task.completed).length;
    return {
      total: tasks.length,
      completed,
      pending: tasks.length - completed,
    };
  }, [tasks]);

  const handleFilterChange = (field, value) => {
    setFilters((currentFilters) => ({ ...currentFilters, [field]: value }));
  };

  const handleAddTask = (taskData) => {
    addTask(taskData);
  };

  return (
    <main className={styles.page}>
      <section className={styles.container}>
        <header className={styles.header}>
          <p className={styles.kicker}>TaskFlow</p>
          <h1>TODO Planner</h1>
          <p className={styles.subtitle}>Track priorities, deadlines, and progress in one place.</p>
        </header>

        <TaskForm onAddTask={handleAddTask} />
        <TaskFilters filters={filters} onFilterChange={handleFilterChange} />
        <TaskSummary summary={summary} />

        <TaskList
          tasks={visibleTasks}
          onToggleComplete={toggleTaskCompletion}
          onDeleteTask={deleteTask}
          onUpdateTask={updateTask}
        />
      </section>
    </main>
  );
}

export default TodoApp;
