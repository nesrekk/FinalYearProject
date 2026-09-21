import React, { useEffect, useId, useRef, useState } from 'react';

export default function InfoTooltip({ label = 'Info', title, children }) {
  const id = useId();
  const popoverId = `it-${id}`;
  const wrapRef = useRef(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (!wrapRef.current) return;
      if (wrapRef.current.contains(e.target)) return;
      setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick, true);
    document.addEventListener('touchstart', onDocClick, true);
    return () => {
      document.removeEventListener('mousedown', onDocClick, true);
      document.removeEventListener('touchstart', onDocClick, true);
    };
  }, [open]);

  return (
    <span
      ref={wrapRef}
      className="it-wrap"
      data-open={open ? 'true' : 'false'}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="it-btn"
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        i
      </button>
      <span id={popoverId} role="tooltip" className="it-popover">
        {title ? <span className="it-title">{title}</span> : null}
        <span className="it-body">{children}</span>
      </span>
    </span>
  );
}
