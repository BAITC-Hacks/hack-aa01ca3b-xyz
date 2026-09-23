const STORAGE_KEY = 'kontur-hackathon-tasks-v1';

function shiftDate(days) {
  const date = new Date();
  date.setHours(12, 0, 0, 0);
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

const demoTasks = [
  {
    id: 'demo-1', title: 'Сдать комплект проектной документации', kind: 'Документация',
    project: 'Павлодарский завод', owner: 'ТОО «Инжиниринг KZ»', dueDate: shiftDate(-2),
    priority: 'high', done: false,
  },
  {
    id: 'demo-2', title: 'Выставить счёт за выполненные работы', kind: 'Счёт',
    project: 'Региональный проект', owner: 'КазСтройМонтаж', dueDate: shiftDate(1),
    priority: 'medium', done: false,
  },
  {
    id: 'demo-3', title: 'Подтвердить график поставки сырья', kind: 'Поставка',
    project: 'Полимерная линия', owner: 'QazChem Supply', dueDate: shiftDate(4),
    priority: 'medium', done: false,
  },
  {
    id: 'demo-4', title: 'Согласовать контрольные точки проекта', kind: 'Документация',
    project: 'Павлодарский завод', owner: 'Проектный офис', dueDate: shiftDate(-1),
    priority: 'low', done: true,
  },
];

const elements = {
  taskList: document.querySelector('#task-list'),
  emptyState: document.querySelector('#empty-state'),
  search: document.querySelector('#search-input'),
  listCount: document.querySelector('#list-count'),
  tableSummary: document.querySelector('#table-summary'),
  clearSearch: document.querySelector('#clear-search'),
  dialog: document.querySelector('#task-dialog'),
  form: document.querySelector('#task-form'),
  attentionList: document.querySelector('#attention-list'),
};

let tasks = loadTasks();
let activeFilter = 'all';
let searchTerm = '';

function loadTasks() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return Array.isArray(saved) ? saved : demoTasks;
  } catch {
    return demoTasks;
  }
}

function saveTasks() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
  } catch {
    // The page remains usable when browser storage is unavailable.
  }
}

function dateAtNoon(value) {
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function daysUntil(value) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = dateAtNoon(value);
  due.setHours(0, 0, 0, 0);
  return Math.round((due - today) / 86400000);
}

function getStatus(task) {
  if (task.done) return 'done';
  const remaining = daysUntil(task.dueDate);
  if (remaining < 0) return 'late';
  if (remaining <= 3) return 'soon';
  return 'progress';
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function formatDate(value) {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
    .format(dateAtNoon(value)).replace('.', '');
}

function formatDateHint(task) {
  if (task.done) return 'Задача закрыта';
  const remaining = daysUntil(task.dueDate);
  if (remaining < 0) return `Просрочено на ${Math.abs(remaining)} дн.`;
  if (remaining === 0) return 'Срок сегодня';
  if (remaining === 1) return 'Остался 1 день';
  if (remaining <= 3) return `Осталось ${remaining} дн.`;
  return task.owner ? `Ответственный: ${escapeHtml(task.owner)}` : task.kind;
}

function pluralizeTasks(count, formOne, formFew, formMany) {
  const tens = count % 100;
  const units = count % 10;
  if (tens >= 11 && tens <= 14) return formMany;
  if (units === 1) return formOne;
  if (units >= 2 && units <= 4) return formFew;
  return formMany;
}

function visibleTasks() {
  return tasks.filter((task) => {
    const status = getStatus(task);
    const matchesFilter = activeFilter === 'all'
      || (activeFilter === 'risk' && (status === 'late' || status === 'soon'))
      || (activeFilter === 'done' && status === 'done');
    const haystack = `${task.title} ${task.project} ${task.owner || ''} ${task.kind}`.toLocaleLowerCase('ru');
    return matchesFilter && haystack.includes(searchTerm.toLocaleLowerCase('ru'));
  }).sort((a, b) => {
    const rank = { late: 0, soon: 1, progress: 2, done: 3 };
    return rank[getStatus(a)] - rank[getStatus(b)] || daysUntil(a.dueDate) - daysUntil(b.dueDate);
  });
}

function renderMetrics() {
  const overdue = tasks.filter((task) => getStatus(task) === 'late').length;
  const soon = tasks.filter((task) => getStatus(task) === 'soon').length;
  const done = tasks.filter((task) => getStatus(task) === 'done').length;
  const open = tasks.length - done;
  const risk = overdue + soon;
  document.querySelector('#metric-open').textContent = open;
  document.querySelector('#metric-overdue').textContent = overdue;
  document.querySelector('#metric-soon').textContent = soon;
  document.querySelector('#metric-done').textContent = done;
  document.querySelector('#nav-open-count').textContent = open;
  document.querySelector('#tab-risk-count').textContent = risk;
  document.querySelector('#attention-count').textContent = risk;
  document.querySelector('#insight-title').textContent = overdue
    ? `${overdue} ${pluralizeTasks(overdue, 'задача требует', 'задачи требуют', 'задач требуют')} внимания`
    : risk ? 'Сроки близко' : 'Фокус на сроках';
  document.querySelector('#insight-copy').textContent = overdue
    ? 'Есть задачи с истёкшим сроком. Проверьте статус документов и договоритесь о следующем шаге.'
    : risk ? `В ближайшие три дня нужно закрыть ${risk} ${pluralizeTasks(risk, 'задачу', 'задачи', 'задач')}. Проверьте ответственных и статус документов.`
      : 'Просрочек нет. Добавьте задачу, чтобы контролировать срок и ответственного в одном месте.';
}

function renderTasks() {
  const rows = visibleTasks();
  elements.listCount.textContent = rows.length;
  elements.tableSummary.textContent = `Показано ${rows.length} из ${tasks.length} задач`;
  elements.clearSearch.hidden = !searchTerm;
  elements.emptyState.hidden = rows.length > 0;
  document.querySelector('.table-scroll').hidden = rows.length === 0;
  elements.taskList.innerHTML = rows.map((task) => {
    const status = getStatus(task);
    const statusLabels = { late: 'Просрочено', soon: 'Скоро срок', progress: 'В работе', done: 'Завершено' };
    const projectClass = `p${(Array.from(task.project).reduce((sum, char) => sum + char.charCodeAt(0), 0) % 4) + 1}`;
    const hintClass = status === 'late' ? 'is-late' : status === 'soon' ? 'is-soon' : '';
    return `<tr>
      <td><div class="task-main"><button class="task-check ${task.done ? 'is-done' : ''}" data-action="toggle" data-id="${escapeHtml(task.id)}" aria-label="${task.done ? 'Вернуть в работу' : 'Отметить выполненной'}">✓</button><div><div class="task-title">${escapeHtml(task.title)}</div><div class="task-subtitle">${escapeHtml(task.kind)}</div></div></div></td>
      <td class="project-cell"><span class="project-dot ${projectClass}"></span>${escapeHtml(task.project)}</td>
      <td><span class="date-main">${formatDate(task.dueDate)}</span><span class="date-hint ${hintClass}">${formatDateHint(task)}</span></td>
      <td><span class="status-pill is-${status}">${statusLabels[status]}</span></td>
    </tr>`;
  }).join('');
}

function renderAttention() {
  const attention = tasks.filter((task) => ['late', 'soon'].includes(getStatus(task)))
    .sort((a, b) => daysUntil(a.dueDate) - daysUntil(b.dueDate)).slice(0, 3);
  elements.attentionList.innerHTML = attention.length ? attention.map((task) => {
    const late = getStatus(task) === 'late';
    return `<div class="attention-item"><span class="attention-mark ${late ? 'is-late' : ''}">${late ? '!' : '◷'}</span><div class="attention-copy"><strong>${escapeHtml(task.title)}</strong><small>${escapeHtml(task.project)} · ${late ? `−${Math.abs(daysUntil(task.dueDate))}` : daysUntil(task.dueDate) === 0 ? 'сегодня' : `через ${daysUntil(task.dueDate)} дн.`}</small></div></div>`;
  }).join('') : '<div class="attention-empty">Пока всё спокойно — задач с близким или прошедшим сроком нет.</div>';
}

function render() {
  renderMetrics();
  renderTasks();
  renderAttention();
}

function setFilter(filter) {
  activeFilter = filter;
  document.querySelectorAll('.filter-tab').forEach((button) => {
    const selected = button.dataset.filter === filter;
    button.classList.toggle('is-selected', selected);
    button.setAttribute('aria-selected', String(selected));
  });
  renderTasks();
}

document.querySelector('#today-label').textContent = new Intl.DateTimeFormat('ru-RU', {
  weekday: 'short', day: 'numeric', month: 'long',
}).format(new Date());

document.querySelectorAll('.filter-tab').forEach((button) => {
  button.addEventListener('click', () => setFilter(button.dataset.filter));
});

document.querySelectorAll('[data-filter-link]').forEach((link) => {
  link.addEventListener('click', () => {
    setFilter(link.dataset.filterLink);
    document.querySelectorAll('.nav-item').forEach((item) => item.classList.remove('is-active'));
    const navTarget = document.querySelector(`.nav-item[data-filter-link="${link.dataset.filterLink}"]`);
    if (navTarget) navTarget.classList.add('is-active');
  });
});

elements.search.addEventListener('input', (event) => {
  searchTerm = event.target.value.trim();
  renderTasks();
});

elements.clearSearch.addEventListener('click', () => {
  elements.search.value = '';
  searchTerm = '';
  renderTasks();
  elements.search.focus();
});

elements.taskList.addEventListener('click', (event) => {
  const button = event.target.closest('[data-action="toggle"]');
  if (!button) return;
  tasks = tasks.map((task) => task.id === button.dataset.id ? { ...task, done: !task.done } : task);
  saveTasks();
  render();
});

document.querySelector('#open-task-dialog').addEventListener('click', () => {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  elements.form.elements.dueDate.value = tomorrow.toISOString().slice(0, 10);
  elements.dialog.showModal();
  elements.form.elements.title.focus();
});

function closeDialog() { elements.dialog.close(); }
document.querySelector('#close-dialog').addEventListener('click', closeDialog);
document.querySelector('#cancel-dialog').addEventListener('click', closeDialog);
elements.dialog.addEventListener('click', (event) => {
  if (event.target === elements.dialog) closeDialog();
});

elements.form.addEventListener('submit', (event) => {
  event.preventDefault();
  const data = new FormData(elements.form);
  tasks.unshift({
    id: crypto.randomUUID ? crypto.randomUUID() : `task-${Date.now()}`,
    title: data.get('title').trim(),
    project: data.get('project').trim(),
    owner: data.get('owner').trim(),
    dueDate: data.get('dueDate'),
    priority: data.get('priority'),
    kind: data.get('kind'),
    done: false,
  });
  saveTasks();
  elements.form.reset();
  closeDialog();
  setFilter('all');
  render();
});

document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    elements.search.focus();
  }
});

render();
