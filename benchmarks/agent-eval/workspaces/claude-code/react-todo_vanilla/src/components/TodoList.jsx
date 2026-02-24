import TodoItem from './TodoItem';
import styles from './TodoList.module.css';

export default function TodoList({ todos, onToggle, onUpdate, onDelete }) {
  if (todos.length === 0) {
    return <p className={styles.empty}>No tasks found. Add one above or adjust your filters.</p>;
  }

  return (
    <ul className={styles.list}>
      {todos.map((todo) => (
        <TodoItem
          key={todo.id}
          todo={todo}
          onToggle={onToggle}
          onUpdate={onUpdate}
          onDelete={onDelete}
        />
      ))}
    </ul>
  );
}
