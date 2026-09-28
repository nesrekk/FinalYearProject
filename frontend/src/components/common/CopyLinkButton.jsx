import React, { useEffect, useRef, useState } from 'react';
import Icon from './Icon';

// Copies the current URL, which carries the page and the tool's inputs
// (utils/useUrlState.js), so whoever opens it sees the same view.
export default function CopyLinkButton() {
    const [status, setStatus] = useState('idle'); // 'idle' | 'copied' | 'failed'
    const timer = useRef(null);

    useEffect(() => () => clearTimeout(timer.current), []);

    const copy = async () => {
        const url = window.location.href;
        let ok = false;
        try {
            await navigator.clipboard.writeText(url);
            ok = true;
        } catch {
            // Clipboard API blocked (e.g. insecure context): the old way.
            const box = document.createElement('textarea');
            box.value = url;
            box.setAttribute('readonly', '');
            box.style.position = 'fixed';
            box.style.opacity = '0';
            document.body.appendChild(box);
            box.select();
            try { ok = document.execCommand('copy'); } catch { ok = false; }
            box.remove();
        }
        setStatus(ok ? 'copied' : 'failed');
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setStatus('idle'), 2500);
    };

    return (
        <span className="copy-link">
            <button type="button" className="table-export-btn" onClick={copy}
                aria-label="Copy a link to this exact view">
                <Icon name={status === 'copied' ? 'check' : 'link'} size={15} />
                {status === 'copied' ? 'Link copied' : 'Copy link'}
            </button>
            <span className="copy-link-status" role="status">
                {status === 'failed' ? 'Couldn’t copy. Use the address bar.' : ''}
            </span>
        </span>
    );
}
