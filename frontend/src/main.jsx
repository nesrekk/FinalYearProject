import './styles/tokens.css';
import './styles/shell.css';
import './styles/kit.css';
import './styles/effects.css';
import './styles/landing.css';
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { initTheme } from './utils/theme';
import { loadSeasonInfo } from './utils/season';

initTheme();

// The current season (utils/season.js) is known before the first render, so every season picker opens on it.
loadSeasonInfo().finally(() => {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
});
