// The app runs one fixed motion language — full spring-physics "advanced"
// motion everywhere (the Simple/Advanced toggle was removed). This stays a
// hook (rather than inlining `true` at every call site) so the many pages
// that already call useMotionMode()/motionPreset() didn't need to change,
// and so the timing presets stay centralized in one place.
export function useMotionMode() {
    return { mode: 'advanced', isAdvanced: true };
}

export function motionPreset() {
    return {
        spring: { type: 'spring', stiffness: 340, damping: 32, mass: 0.9 },
        segmentedSpring: { type: 'spring', stiffness: 420, damping: 34 },
        stagger: 0.05,
        fieldSpring: { type: 'spring', stiffness: 320, damping: 28 },
        tableTransition: { duration: 0.25, ease: [0.4, 0, 0.2, 1] },
    };
}
