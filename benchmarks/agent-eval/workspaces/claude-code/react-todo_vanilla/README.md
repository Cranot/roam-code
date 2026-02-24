# Todo App

A feature-rich TODO application built with React, JavaScript, and Vite.

## Features

- Add, edit, delete, and mark tasks as complete
- Task categories: work, personal, shopping, health
- Priority levels: high, medium, low with color-coded indicators
- Due dates with overdue highlighting
- Filter by category, priority, and completion status
- Sort by due date, priority, or creation date
- Data persisted to localStorage
- Responsive design for mobile and desktop
- Keyboard shortcuts: Enter to add/save, Escape to cancel edit
- Task count summary (total, completed, pending)

## Setup

### Prerequisites

- Node.js 18+
- npm 9+

### Install

```bash
npm install
```

### Development

```bash
npm run dev
```

Opens the app at `http://localhost:5173`.

### Build

```bash
npm run build
```

Output is written to `dist/`.

### Preview production build

```bash
npm run preview
```

### Run tests

```bash
npm test
```

Watch mode:

```bash
npm run test:watch
```

## Project Structure

```
src/
  App.jsx                  # Root component
  App.module.css
  main.jsx                 # Entry point
  index.css                # Global styles and CSS variables
  hooks/
    useLocalStorage.js     # localStorage sync hook
    useTodos.js            # Core state management (CRUD, filtering, sorting)
  components/
    TodoForm.jsx           # Add task form with category, priority, due date
    TodoForm.module.css
    TodoItem.jsx           # Single task with edit/delete/complete
    TodoItem.module.css
    TodoList.jsx           # Task list container
    TodoList.module.css
    TodoFilters.jsx        # Category, priority, status, sort controls
    TodoFilters.module.css
    TodoSummary.jsx        # Total/completed/pending counts
    TodoSummary.module.css
  test/
    setup.js               # Vitest setup (jest-dom matchers)
    App.test.jsx            # Integration tests
    TodoForm.test.jsx       # Form unit tests
    TodoItem.test.jsx       # Item unit tests
    TodoList.test.jsx       # List unit tests
    TodoFilters.test.jsx    # Filter unit tests
    TodoSummary.test.jsx    # Summary unit tests
    useTodos.test.jsx       # Hook unit tests
```

## Tech Stack

- React 18
- Vite 6
- CSS Modules
- Vitest + Testing Library
