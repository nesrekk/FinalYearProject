import React, { useState } from 'react';
import Icon from '../common/Icon';
import { useAutosave } from '../../utils/useAutosave';
import { LIMITS } from '../../utils/workbenchStore';

// Plain text only: it is rendered as text (React escapes it, and line breaks
// come from CSS), never as HTML or Markdown, so an imported board's note
// can't put markup or links on the page.
export default function NoteBlock({ block, onSave }) {
    const text = block.settings.text || '';
    const [editing, setEditing] = useState(!text);
    const [draft, setDraft, flush] = useAutosave(text, (v) => onSave(v));

    if (editing) {
        return (
            <div className="wb-note">
                <textarea
                    className="wb-note-input"
                    value={draft}
                    maxLength={LIMITS.note}
                    aria-label="Note text"
                    placeholder="Write what this board is for, or what the blocks show."
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={flush}
                />
                <button type="button" className="table-export-btn" onClick={() => { flush(); setEditing(false); }} disabled={!draft.trim()}>
                    <Icon name="check" size={15} /> Done
                </button>
            </div>
        );
    }
    return (
        <div className="wb-note">
            <p className="wb-note-text">{draft}</p>
            <button type="button" className="table-export-btn" onClick={() => setEditing(true)}>
                <Icon name="edit" size={15} /> Edit
            </button>
        </div>
    );
}
