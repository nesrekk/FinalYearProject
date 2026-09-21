import React from 'react';

export default function StatCard({ icon, label, value, sub }) {
    return (
        <div className="stat-card">
            <span className="stat-card-icon">{icon}</span>
            <div className="stat-card-body">
                <p className="stat-card-value">{value}</p>
                <p className="stat-card-label">{label}</p>
                {sub && <p className="stat-card-sub">{sub}</p>}
            </div>
        </div>
    );
}
