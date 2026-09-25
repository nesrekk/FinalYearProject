import React from 'react';

// Contains any failure inside the decorative welcome intro so it can't unmount
// the whole app: renders nothing and tells the parent to move on to the
// normal landing page.
export default class IntroErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { failed: false };
    }

    static getDerivedStateFromError() {
        return { failed: true };
    }

    componentDidCatch(error) {
        console.warn('Welcome intro failed; skipping it.', error);
        this.props.onError?.();
    }

    render() {
        return this.state.failed ? null : this.props.children;
    }
}
