import { useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';

// Renders a player-suggestion list into document.body with position:fixed
// computed from the input's own bounding rect, instead of position:absolute
// inside the input's own wrapper. .dashboard-card (the usual ancestor) sets
// overflow:hidden, which was clipping/cutting off the old absolute-positioned
// list wherever the search box sat near a card's bottom edge. A portal with
// fixed positioning escapes that ancestor clipping entirely.
export default function AutocompleteDropdown({ anchorRef, items, onPick }) {
    const [rect, setRect] = useState(null);

    useLayoutEffect(() => {
        if (!anchorRef.current || !items?.length) {
            setRect(null);
            return;
        }
        const update = () => setRect(anchorRef.current.getBoundingClientRect());
        update();
        window.addEventListener('scroll', update, true);
        window.addEventListener('resize', update);
        return () => {
            window.removeEventListener('scroll', update, true);
            window.removeEventListener('resize', update);
        };
    }, [anchorRef, items]);

    if (!items?.length || !rect) return null;

    return createPortal(
        <ul
            className="autocomplete-list"
            style={{
                position: 'fixed',
                top: rect.bottom + 4,
                left: rect.left,
                width: rect.width,
                zIndex: 1000,
                background: 'var(--surface-2)',
                border: '1px solid var(--hairline)',
                borderRadius: 6,
                maxHeight: 220,
                overflowY: 'auto',
                listStyle: 'none',
                padding: 0,
                margin: 0,
                boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            }}
        >
            {items.map((name) => (
                <li key={name}>
                    <button
                        type="button"
                        onClick={() => onPick(name)}
                        style={{ display: 'block', width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem', background: 'transparent', border: 'none', color: 'var(--text)', cursor: 'pointer' }}
                    >
                        {name}
                    </button>
                </li>
            ))}
        </ul>,
        document.body
    );
}
