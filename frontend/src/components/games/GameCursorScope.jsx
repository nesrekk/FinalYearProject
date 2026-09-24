import React, { useRef } from 'react';
import CustomCursor from '../landing/CustomCursor';

// Wraps a game screen's content with the same custom cursor (dot + lagging
// ring + magnetic buttons) used on the landing hero. Off on touch/reduced
// motion automatically (CustomCursor's own check).
export default function GameCursorScope({ children }) {
    const scopeRef = useRef(null);
    return (
        <div ref={scopeRef} className="game-cursor-scope">
            <CustomCursor scopeRef={scopeRef} />
            {children}
        </div>
    );
}
