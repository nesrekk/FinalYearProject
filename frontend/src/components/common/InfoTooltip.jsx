import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const CLOSE_DELAY = 150;
const VIEWPORT_MARGIN = 10;

export default function InfoTooltip({ label = 'Info', title, children }) {
  const id = useId();
  const popoverId = `it-${id}`;
  const wrapRef = useRef(null);
  const btnRef = useRef(null);
  const popoverRef = useRef(null);
  const closeTimerRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState(null);

  function cancelClose() {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }

  function scheduleClose() {
    cancelClose();
    closeTimerRef.current = setTimeout(() => setOpen(false), CLOSE_DELAY);
  }

  function openNow() {
    cancelClose();
    setOpen(true);
  }

  useEffect(() => () => cancelClose(), []);

  // Click outside (button or portal popover) closes it. Escape closes it too.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (wrapRef.current?.contains(e.target)) return;
      if (popoverRef.current?.contains(e.target)) return;
      setOpen(false);
    }
    function onKeyDown(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick, true);
    document.addEventListener('touchstart', onDocClick, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocClick, true);
      document.removeEventListener('touchstart', onDocClick, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  // Position the portaled popover against the trigger button, clamped to viewport
  // so it can never be clipped by a scrolling ancestor (e.g. a horizontally
  // scrollable stat table) the way an absolutely-positioned descendant would be.
  useLayoutEffect(() => {
    if (!open || !btnRef.current) return;
    function place() {
      const btnRect = btnRef.current.getBoundingClientRect();
      const popEl = popoverRef.current;
      const popWidth = popEl?.offsetWidth || Math.min(360, window.innerWidth * 0.7);
      const popHeight = popEl?.offsetHeight || 0;

      let left = btnRect.left + btnRect.width / 2 - popWidth / 2;
      left = Math.max(VIEWPORT_MARGIN, Math.min(left, window.innerWidth - popWidth - VIEWPORT_MARGIN));

      let top = btnRect.bottom + VIEWPORT_MARGIN;
      if (popHeight && top + popHeight + VIEWPORT_MARGIN > window.innerHeight) {
        top = btnRect.top - popHeight - VIEWPORT_MARGIN;
      }
      setCoords({ top, left });
    }
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [open]);

  return (
    <span
      ref={wrapRef}
      className="it-wrap"
      data-open={open ? 'true' : 'false'}
      onMouseEnter={openNow}
      onMouseLeave={scheduleClose}
    >
      <button
        ref={btnRef}
        type="button"
        className="it-btn"
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={(e) => {
          // Not a toggle: a hover that lands on the button already opens it via
          // onMouseEnter, and a click firing right after would otherwise race
          // that open and immediately flip it back closed. Click only ever
          // guarantees "open" (it's the touch/keyboard path); closing happens
          // via mouseleave, outside click/tap, or Escape.
          e.stopPropagation();
          openNow();
        }}
      >
        i
      </button>
      {open && createPortal(
        <span
          id={popoverId}
          role="tooltip"
          ref={popoverRef}
          className="it-popover"
          style={coords ? { top: coords.top, left: coords.left } : { top: -9999, left: -9999 }}
          onMouseEnter={openNow}
          onMouseLeave={scheduleClose}
        >
          {title ? <span className="it-title">{title}</span> : null}
          <span className="it-body">{children}</span>
        </span>,
        document.body
      )}
    </span>
  );
}
