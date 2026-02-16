# React Todo App

A feature-rich, responsive TODO application built with React and Vite. Organize your tasks efficiently with categories, priorities, due dates, and advanced filtering capabilities.

## Features

### Core Functionality
- **Add, Edit, Delete Tasks** - Full CRUD operations for task management
- **Mark as Complete** - Toggle task completion status with visual feedback
- **Categories** - Organize tasks into Work, Personal, Shopping, or Health categories
- **Priority Levels** - Assign High, Medium, or Low priority with color-coded badges
- **Due Dates** - Set deadlines with automatic overdue detection and highlighting

### Advanced Features
- **Smart Filtering** - Filter by status (all/active/completed), category, and priority
- **Flexible Sorting** - Sort by due date, priority, or creation date
- **Task Statistics** - Real-time summary showing total, completed, pending, and overdue tasks
- **Local Storage** - All data persists automatically in your browser
- **Keyboard Shortcuts** - Press Enter to add tasks, Escape to cancel edits
- **Responsive Design** - Seamless experience on desktop, tablet, and mobile devices

## Tech Stack

- **React 19** - UI library
- **Vite** - Build tool and dev server
- **CSS Modules** - Component-scoped styling
- **Vitest** - Unit testing framework
- **React Testing Library** - Component testing utilities

## Getting Started

### Prerequisites

- Node.js 18 or higher
- npm or yarn

### Installation

1. Install dependencies:
```bash
npm install
```

2. Start the development server:
```bash
npm run dev
```

3. Open your browser and navigate to `http://localhost:5173`

## Available Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm run preview` - Preview production build
- `npm test` - Run unit tests in watch mode
- `npm run test:run` - Run tests once
- `npm run test:ui` - Run tests with UI
- `npm run lint` - Lint code with ESLint

## Project Structure

```
src/
├── components/           # React components
│   ├── AddTask.jsx      # Task creation form
│   ├── TaskList.jsx     # Task list container
│   ├── TaskItem.jsx     # Individual task item
│   ├── TaskFilters.jsx  # Filter controls
│   ├── TaskStats.jsx    # Statistics display
│   └── __tests__/       # Component tests
├── utils/               # Utility functions
│   ├── localStorage.js  # Local storage operations
│   ├── taskUtils.js     # Task-related utilities
│   └── __tests__/       # Utility tests
├── test/                # Test configuration
├── types.js            # Type definitions and constants
├── App.jsx             # Main application component
├── App.css             # Global styles
└── main.jsx            # Application entry point
```

## Usage Guide

### Adding a Task

1. Click the "+ Add New Task" button
2. Enter a task title (required)
3. Optionally add:
   - Description
   - Category (Work, Personal, Shopping, Health)
   - Priority (High, Medium, Low)
   - Due date
4. Click "Add Task" or press Enter

### Editing a Task

1. Click the "Edit" button on any task
2. Modify the fields as needed
3. Click "Save" or press Enter to confirm
4. Click "Cancel" or press Escape to discard changes

### Filtering Tasks

Use the filter controls to narrow down your task list:
- **Status**: Show all tasks, only active, or only completed
- **Category**: Filter by specific category
- **Priority**: Filter by priority level
- **Sort by**: Order tasks by due date, priority, or creation date

### Keyboard Shortcuts

- `Enter` - Submit task form (when adding or editing)
- `Escape` - Cancel current operation

## Visual Indicators

### Category Colors
- **Work** - Blue
- **Personal** - Green
- **Shopping** - Orange
- **Health** - Red

### Priority Colors
- **High** - Red background
- **Medium** - Orange background
- **Low** - Gray background

### Task States
- **Completed** - Reduced opacity with strikethrough text
- **Overdue** - Red left border and pink background
- **Normal** - White background with subtle shadow

## Testing

The application includes unit tests for task utilities and core functionality.

Run tests with:
```bash
npm test
```

Run tests once:
```bash
npm run test:run
```

## Browser Support

- Chrome (latest)
- Firefox (latest)
- Safari (latest)
- Edge (latest)

## Data Persistence

All tasks are automatically saved to your browser's localStorage. Your data will persist across sessions but is specific to:
- The browser you're using
- The domain/origin of the application

**Note**: Clearing browser data will delete all tasks.

## License

MIT
