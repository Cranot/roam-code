import { useTodos } from './hooks/useTodos';
import TodoForm from './components/TodoForm';
import TodoFilters from './components/TodoFilters';
import TodoSummary from './components/TodoSummary';
import TodoList from './components/TodoList';
import styles from './App.module.css';

export default function App() {
  const {
    todos,
    filters,
    setFilters,
    sortBy,
    setSortBy,
    addTodo,
    updateTodo,
    deleteTodo,
    toggleComplete,
    summary,
  } = useTodos();

  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <h1 className={styles.title}>Todo App</h1>
        <TodoSummary summary={summary} />
      </header>
      <main className={styles.main}>
        <TodoForm onAdd={addTodo} />
        <TodoFilters
          filters={filters}
          onFilterChange={setFilters}
          sortBy={sortBy}
          onSortChange={setSortBy}
        />
        <TodoList
          todos={todos}
          onToggle={toggleComplete}
          onUpdate={updateTodo}
          onDelete={deleteTodo}
        />
      </main>
    </div>
  );
}
