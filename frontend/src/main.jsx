import './styles/tokens.css';
import './styles/shell.css';
import './styles/kit.css';
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { initTheme } from './utils/theme';

initTheme();

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
