# TaskFlow TODO (React + Vite)

A complete TODO application built with React, JavaScript, and Vite.

## Features

- Add, edit, delete, and complete tasks
- Categories: `work`, `personal`, `shopping`, `health`
- Priority levels with visual indicators: `high`, `medium`, `low`
- Due dates with overdue highlighting
- Filter by category, priority, and completion status
- Sort by due date, priority, or creation date
- Keyboard shortcuts:
  - `Enter`: add a task from the title input
  - `Escape`: cancel task edit mode
- Local storage persistence (`localStorage`)
- Task summary counts: total, completed, pending
- Responsive layout for desktop and mobile

## Tech Stack

- React
- Vite
- CSS Modules
- Vitest + Testing Library

## Setup

1. Install dependencies:

```bash
npm install
```

2. Start the dev server:

```bash
npm run dev
```

3. Open the app:

`http://localhost:5173`

## Scripts

- `npm run dev`: run development server
- `npm run build`: build for production
- `npm run preview`: preview production build
- `npm run test`: run unit tests once
- `npm run test:watch`: run tests in watch mode

## Project Structure

```text
src/
  components/
  hooks/
  test/
  utils/
  App.jsx
  constants.js
  index.css
  main.jsx
```
