import React, { useState } from 'react';
import Icon from './Icon';
import AddToReport from './AddToReport';
import { downloadChartPng, downloadChartSvg, snapshotChart } from '../../utils/chartExport';
import { slugify } from '../../utils/tableExport';

// Place near a chart's <svg ref={svgRef}>. Downloads exactly what's
// rendered — this app's CSS variables (Paper/Ink colors) are resolved to
// their live computed values first (utils/chartExport.js), so the file
// looks right outside the app too. `name` sets the file name.
export default function ChartExport({ svgRef, name }) {
    const [error, setError] = useState('');

    const run = async (format) => {
        const svg = svgRef?.current;
        if (!svg) {
            setError('Nothing to export yet.');
            return;
        }
        setError('');
        const stamp = new Date().toISOString().slice(0, 10);
        const base = slugify(name || 'chart');
        try {
            if (format === 'svg') downloadChartSvg(svg, `${base}-${stamp}.svg`);
            else await downloadChartPng(svg, `${base}-${stamp}.png`);
        } catch {
            setError('Could not export this chart.');
        }
    };

    const snapshot = () => {
        const svg = svgRef?.current;
        return svg ? { title: name || 'Chart', svg: snapshotChart(svg) } : null;
    };

    return (
        <div className="chart-export" data-export-skip>
            {error && <span className="table-export-error" role="status">{error}</span>}
            <button type="button" className="chart-export-btn" onClick={() => run('png')} aria-label="Download this chart as a PNG image">
                <Icon name="image" size={15} /> PNG
            </button>
            <button type="button" className="chart-export-btn" onClick={() => run('svg')} aria-label="Download this chart as an SVG image">
                <Icon name="download" size={15} /> SVG
            </button>
            <AddToReport kind="chart" getSnapshot={snapshot} />
        </div>
    );
}
