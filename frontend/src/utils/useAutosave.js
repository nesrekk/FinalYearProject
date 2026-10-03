import { useEffect, useLayoutEffect, useRef, useState } from 'react';

// Text that saves itself: when the box loses focus, and a moment after typing
// stops, so a click on Print or a nav link right after typing keeps the text.
// Returns [shown, change, flush]; wire flush to onBlur. (Report builder,
// Workbench.)
//
// Only what the user typed is ever saved: a box nobody edited saves nothing
// (also not when it unmounts), and it shows `value` as it changes from
// outside (another tab, or a rename made elsewhere on the page). While the
// user is editing, their text is shown until the save comes back as `value`.
export function useAutosave(value, save) {
    // { text, base: the value editing started from, dirty: not saved yet }
    const [local, setLocal] = useState(null);
    const timer = useRef(null);
    const latest = useRef({ local, value, save });
    useLayoutEffect(() => { latest.current = { local, value, save }; });

    const shown = local && local.base === value ? local.text : value;

    const flush = () => {
        clearTimeout(timer.current);
        const { local: l, value: v, save: s } = latest.current;
        if (!l || !l.dirty) return;
        if (l.text !== v) s(l.text);
        const done = { ...l, dirty: false };
        latest.current = { ...latest.current, local: done };
        setLocal(done);
    };
    const change = (next) => {
        const { local: l, value: v } = latest.current;
        const updated = { text: next, base: l && l.base === v ? l.base : v, dirty: true };
        latest.current = { ...latest.current, local: updated };
        setLocal(updated);
        clearTimeout(timer.current);
        timer.current = setTimeout(flush, 700);
    };
    useEffect(() => () => flush(), []);
    return [shown, change, flush];
}
