/**
 * @typedef {'work' | 'personal' | 'shopping' | 'health'} Category
 * @typedef {'high' | 'medium' | 'low'} Priority
 * @typedef {'all' | 'active' | 'completed'} FilterStatus
 * @typedef {'dueDate' | 'priority' | 'createdAt'} SortBy
 */

/**
 * @typedef {Object} Task
 * @property {string} id
 * @property {string} title
 * @property {string} description
 * @property {Category} category
 * @property {Priority} priority
 * @property {string} dueDate - ISO date string
 * @property {boolean} completed
 * @property {string} createdAt - ISO date string
 */

export const CATEGORIES = ['work', 'personal', 'shopping', 'health'];
export const PRIORITIES = ['high', 'medium', 'low'];
export const FILTER_STATUSES = ['all', 'active', 'completed'];
export const SORT_OPTIONS = ['dueDate', 'priority', 'createdAt'];

export const PRIORITY_WEIGHTS = {
  high: 3,
  medium: 2,
  low: 1
};
