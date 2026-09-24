import React, { useState } from 'react';
import { getStoredTheme, setStoredTheme, applyTheme } from '../../utils/theme';

const OPTIONS = [
    { value: 'system', label: 'System' },
    { value: 'light', label: 'Light' },
    { value: 'dark', label: 'Dark' },
];

export default function ThemeToggle({ className = '' }) {
    const [theme, setTheme] = useState(getStoredTheme);

    const choose = (value) => {
        setTheme(value);
        setStoredTheme(value);
        applyTheme(value);
    };

    return (
        <div className={`theme-toggle ${className}`} role="group" aria-label="Theme">
            {OPTIONS.map((opt) => (
                <button
                    key={opt.value}
                    type="button"
                    className={`theme-toggle-btn${theme === opt.value ? ' theme-toggle-btn--active' : ''}`}
                    onClick={() => choose(opt.value)}
                    aria-pressed={theme === opt.value}
                >
                    {opt.label}
                </button>
            ))}
        </div>
    );
}
