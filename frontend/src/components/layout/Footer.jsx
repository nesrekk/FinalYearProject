import React from 'react';

export default function Footer() {
    return (
        <footer className="app-footer">
            <p>
                Data: nba_api (stats.nba.com), ESPN via sportsdataverse, CollegeBasketballData.com,
                NBA Draft Combine, and a live odds provider — every number traces back to a real
                Postgres table built from one of these. No number on this site is fabricated.
            </p>
        </footer>
    );
}
