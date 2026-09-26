const STORAGE_KEY = 'nba-hub-theme';

export function getStoredTheme() {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === 'light' || v === 'dark' || v === 'system' ? v : 'light';
  } catch {
    return 'light';
  }
}

export function setStoredTheme(theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // ignore — private browsing / blocked storage
  }
}

export function applyTheme(theme) {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme === 'dark' || theme === 'system' ? theme : 'light');
}

export function initTheme() {
  const theme = getStoredTheme();
  applyTheme(theme);
  return theme;
}
