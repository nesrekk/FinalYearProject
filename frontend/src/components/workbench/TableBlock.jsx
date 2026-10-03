import React, { useEffect, useState } from 'react';
import Icon from '../common/Icon';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import TeamLogo from '../common/TeamLogo';
import { runWorkbenchQuery, workbenchError } from '../../services/api';
import { LIMITS } from '../../utils/workbenchStore';
import { useAutosave } from '../../utils/useAutosave';
import { isPlainClick, openPage, pageHref, playerProfileHref, openPlayerProfile } from '../../utils/useUrlState';
import { COLOR_NAMES, METHOD_CARDS, entityWord, formatValue, intervalText, seasonLabel, seriesVar, setFits } from './workbenchShared';
import { LIMIT_CHOICES, buildSpec, defaultColumns, groupLabel, one, rowUnit } from './tableSpec';

// ── Settings ───────────────────────────────────────────────────────────

function ColumnPicker({ ds, chosen, onChange }) {
    const byKey = new Map(ds.columns.map((c) => [c.key, c]));
    const groups = [];
    for (const c of ds.columns) {
        let g = groups.find((x) => x.name === c.group);
        if (!g) groups.push(g = { name: c.group, cols: [] });
        g.cols.push(c);
    }
    const toggle = (key) => onChange(chosen.includes(key) ? chosen.filter((k) => k !== key) : [...chosen, key].slice(0, LIMITS.columns));
    const move = (i, d) => {
        const next = [...chosen];
        [next[i], next[i + d]] = [next[i + d], next[i]];
        onChange(next);
    };
    return (
        <fieldset className="wb-fieldset">
            <legend>Stats ({chosen.length})</legend>
            <ol className="wb-chosen">
                {chosen.map((k, i) => (
                    <li key={k} className="wb-chip">
                        <span>{byKey.get(k)?.label || k}</span>
                        <button type="button" className="wb-icon-btn" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move ${byKey.get(k)?.label || k} left`}><Icon name="chevron_left" size={16} /></button>
                        <button type="button" className="wb-icon-btn" disabled={i === chosen.length - 1} onClick={() => move(i, 1)} aria-label={`Move ${byKey.get(k)?.label || k} right`}><Icon name="chevron_right" size={16} /></button>
                        <button type="button" className="wb-icon-btn" onClick={() => toggle(k)} aria-label={`Remove ${byKey.get(k)?.label || k}`}><Icon name="close" size={16} /></button>
                    </li>
                ))}
            </ol>
            {groups.map((g) => (
                <details key={g.name} className="wb-colgroup">
                    <summary>{g.name} <span className="wb-meta">({g.cols.filter((c) => chosen.includes(c.key)).length} of {g.cols.length})</span></summary>
                    <ul>
                        {g.cols.map((c) => {
                            const off = c.status !== 'verified';
                            return (
                                <li key={c.key}>
                                    <label className={off ? 'wb-col-off' : ''} title={off ? '' : `${c.label}: ${c.combines}${c.note ? `. ${c.note}` : ''}`}>
                                        <input type="checkbox" checked={chosen.includes(c.key)} disabled={off} onChange={() => toggle(c.key)} />
                                        {' '}{c.label}
                                        {!off && c.first_season > ds.seasons.from && <span className="wb-meta"> · from {seasonLabel(c.first_season)}</span>}
                                    </label>
                                    {off && <p className="wb-col-reason">Not offered: {c.reason}</p>}
                                </li>
                            );
                        })}
                    </ul>
                </details>
            ))}
        </fieldset>
    );
}

function TitleInput({ value, onSave }) {
    const [draft, setDraft, flush] = useAutosave(value, onSave);
    return <input className="wb-input" value={draft} maxLength={LIMITS.title} placeholder="(automatic)" onChange={(e) => setDraft(e.target.value)} onBlur={flush} />;
}

function TableSettings({ settings, title, catalogue, ds, sets, onChange, onTitle }) {
    const seasons = [];
    for (let s = ds.seasons.to; s >= ds.seasons.from; s -= 1) seasons.push(s);
    const usable = sets.filter((s) => setFits(s, ds));
    const bound = sets.find((s) => s.id === settings.setId);
    const set = (patch) => onChange(patch);
    const changeDataset = (key) => {
        const next = catalogue.datasets.find((d) => d.key === key);
        const keep = settings.columns.filter((k) => next.columns.some((c) => c.key === k && c.status === 'verified'));
        set({
            dataset: key,
            columns: keep.length ? keep : defaultColumns(next),
            groupBy: next.group_by.includes(settings.groupBy) ? settings.groupBy : 'none',
            per: next.per_modes.some((p) => p.key === settings.per) ? settings.per : 'game',
            setId: bound && setFits(bound, next) ? bound.id : null,
            seasonFrom: null,
            seasonTo: null,
            sort: [],
            minPoss: next.poss_floor ?? null,
        });
    };
    const hasIntervals = ds.columns.some((c) => c.interval && settings.columns.includes(c.key));
    const from = settings.seasonFrom ?? '';
    const to = settings.seasonTo ?? '';
    return (
        <div className="wb-settings">
            <label className="wb-field">
                <span>Title</span>
                <TitleInput value={title} onSave={onTitle} />
            </label>
            <label className="wb-field">
                <span>Data</span>
                <select className="wb-select" value={ds.key} onChange={(e) => changeDataset(e.target.value)}>
                    {catalogue.datasets.map((d) => (
                        <option key={d.key} value={d.key}>{d.label} ({seasonLabel(d.seasons.from)} to {seasonLabel(d.seasons.to)})</option>
                    ))}
                </select>
            </label>
            <label className="wb-field">
                <span>Rows</span>
                <select className="wb-select" value={settings.setId || ''} onChange={(e) => set({ setId: e.target.value || null, seasonFrom: null, seasonTo: null })}>
                    <option value="">All {entityWord(ds.entity)}</option>
                    {usable.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.members.length}){s.kind !== ds.entity ? ': units they’re in' : ''}</option>)}
                </select>
            </label>
            {bound && bound.kind !== ds.entity && setFits(bound, ds) && (
                <label className="wb-field">
                    <span>Units with</span>
                    <select className="wb-select" value={settings.playersMatch} onChange={(e) => set({ playersMatch: e.target.value })}>
                        <option value="any">any of {bound.name}</option>
                        <option value="all">all of {bound.name} together</option>
                    </select>
                </label>
            )}
            <div className="wb-field">
                <span id={`seasons-${ds.key}`}>Seasons</span>
                <div className="wb-row" role="group" aria-labelledby={`seasons-${ds.key}`}>
                    <select className="wb-select" aria-label="First season" value={from} onChange={(e) => set({ seasonFrom: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">{settings.setId ? 'last 5 seasons' : 'latest season'}</option>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                    <span className="wb-meta">to</span>
                    <select className="wb-select" aria-label="Last season" value={to} onChange={(e) => set({ seasonTo: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">latest</option>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </div>
            </div>
            <label className="wb-field">
                <span>Each row is</span>
                <select className="wb-select" value={ds.group_by.includes(settings.groupBy) ? settings.groupBy : 'none'} onChange={(e) => set({ groupBy: e.target.value, sort: [] })}>
                    {ds.group_by.map((g) => <option key={g} value={g}>{groupLabel(g, ds)}</option>)}
                </select>
            </label>
            {ds.per_modes.length > 1 && (
                <label className="wb-field">
                    <span>Counting stats</span>
                    <select className="wb-select" value={settings.per} onChange={(e) => set({ per: e.target.value })}>
                        {ds.per_modes.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                    </select>
                </label>
            )}
            <label className="wb-field">
                <span>At least</span>
                <span className="wb-row">
                    <input className="wb-input wb-input--num" type="number" min="0" step="1" value={settings.minGames ?? ''} placeholder="0"
                        onChange={(e) => set({ minGames: e.target.value === '' ? null : Math.max(0, Math.round(Number(e.target.value))) || null })} />
                    <span className="wb-meta">games a row</span>
                </span>
            </label>
            {ds.poss_floor != null && (
                <label className="wb-field">
                    <span>And at least</span>
                    <span className="wb-row">
                        <input className="wb-input wb-input--num" type="number" min="0" step="10" value={settings.minPoss ?? ''} placeholder="0"
                            onChange={(e) => set({ minPoss: e.target.value === '' ? null : Math.max(0, Math.round(Number(e.target.value))) || null })} />
                        <span className="wb-meta">possessions a row (suggested {ds.poss_floor})</span>
                    </span>
                </label>
            )}
            <label className="wb-field">
                <span>Rows shown</span>
                <select className="wb-select" value={settings.limit} onChange={(e) => set({ limit: Number(e.target.value) })}>
                    {LIMIT_CHOICES.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
            </label>
            <label className="wb-check">
                <input type="checkbox" checked={settings.showN} onChange={(e) => set({ showN: e.target.checked })} />
                {' '}Show each value’s sample (n) under it
            </label>
            {hasIntervals && (
                <label className="wb-check">
                    <input type="checkbox" checked={settings.showCi} onChange={(e) => set({ showCi: e.target.checked })} />
                    {' '}Show the interval the model gives under each value
                </label>
            )}
            <ColumnPicker ds={ds} chosen={settings.columns} onChange={(columns) => set({ columns, sort: [] })} />
        </div>
    );
}

// ── Cells ──────────────────────────────────────────────────────────────

function nameCell(row, field, colorOf) {
    const dot = (id) => {
        const c = colorOf.get(id);
        return c == null ? null : (
            <span className="wb-dot" style={{ '--wb-c': seriesVar(c) }} aria-hidden="true" title={COLOR_NAMES[c]} />
        );
    };
    if (field === 'player_name') {
        return <span className="wb-namecell">{dot(row.player_id)}<PlayerName playerId={row.player_id} name={row.player_name || `Player ${row.player_id}`} size={24} /></span>;
    }
    // team rows: franchise (+ the team's own code and name that season)
    const abbr = row.team || row.franchise;
    return (
        <span className="wb-namecell">
            {dot(row.franchise)}
            <TeamLink abbr={abbr} season={row.season}>
                <TeamLogo abbreviation={abbr} size={20} />
                <span>{row.team_name || abbr}</span>
            </TeamLink>
        </span>
    );
}

// Which response fields become columns, and how each reads.
function fieldColumns(fields) {
    const out = [];
    const has = (f) => fields.includes(f);
    if (has('player_name')) out.push({ key: 'player_name', label: 'Player', name: true });
    else if (has('franchise')) out.push({ key: 'franchise', label: 'Team', name: true });
    for (const f of fields) {
        if (['player_id', 'player_name', 'franchise', 'team_name', 'game_id', 'player_ids', 'player_a', 'player_b', 'player_b_name'].includes(f)) continue;
        if (f === 'team' && has('franchise')) continue; // shown in the name cell
        if (f === 'player_names') { out.push({ key: f, label: 'Lineup', unit: 'lineup' }); continue; }
        if (f === 'player_a_name') { out.push({ key: f, label: 'Pair', unit: 'pair' }); continue; }
        out.push({
            key: f,
            label: { season: 'Season', date: 'Date', team: 'Team', opponent: 'Opp', home: 'Where', win: 'Result' }[f] || f,
        });
    }
    return out;
}

// The players of a lineup or pair: surnames linked to their profiles, the
// bound set's players marked with their colour.
function UnitNames({ ids, names, colorOf }) {
    return (
        <span className="wb-unit">
            {ids.map((id, i) => {
                const name = names[i] || `Player ${id}`;
                const c = colorOf.get(id);
                const onClick = (e) => {
                    if (!isPlainClick(e)) return;
                    e.preventDefault();
                    openPlayerProfile(id);
                };
                return (
                    <React.Fragment key={id}>
                        {i > 0 && <span aria-hidden="true"> · </span>}
                        <a href={playerProfileHref(id)} onClick={onClick} title={name} className={c != null ? 'wb-unit-mine' : ''}>
                            {c != null && <span className="wb-dot" style={{ '--wb-c': seriesVar(c) }} aria-hidden="true" />}
                            {name.split(' ').slice(1).join(' ') || name}
                        </a>
                    </React.Fragment>
                );
            })}
        </span>
    );
}

function unitCell(f, row, colorOf) {
    if (f.unit === 'lineup') return <UnitNames ids={row.player_ids || []} names={row.player_names || []} colorOf={colorOf} />;
    return <UnitNames ids={[row.player_a, row.player_b]} names={[row.player_a_name, row.player_b_name]} colorOf={colorOf} />;
}

// Links to the Methodology cards behind the chosen columns.
export function MethodLinks({ columns }) {
    const ids = [...new Set(columns.map((c) => c.method).filter((m) => m && METHOD_CARDS[m]))];
    if (!ids.length) return null;
    return (
        <p className="wb-meta wb-method-links">
            How these are estimated:{' '}
            {ids.map((id, i) => (
                <React.Fragment key={id}>
                    {i > 0 && ', '}
                    <a href={pageHref('methodology', { card: id })} onClick={(e) => { if (!isPlainClick(e)) return; e.preventDefault(); openPage('methodology', { card: id }); }}>
                        {METHOD_CARDS[id]}
                    </a>
                </React.Fragment>
            ))}
            {' '}(Methodology).
        </p>
    );
}

function fieldValue(f, row) {
    const v = row[f];
    if (v == null) return '—';
    if (f === 'season') return seasonLabel(v);
    if (f === 'home') return v ? 'Home' : 'Away';
    if (f === 'win') return v ? 'W' : 'L';
    if (f === 'team' || f === 'opponent') return <TeamLink abbr={v} season={row.season} logoSize={18} />;
    return String(v);
}

// ── The block ──────────────────────────────────────────────────────────

export default function TableBlock({ block, board, catalogue, editing, onSettings, onTitle }) {
    const { settings } = block;
    const ds = catalogue.datasets.find((d) => d.key === settings.dataset) || catalogue.datasets[0];
    const set = board.sets.find((s) => s.id === settings.setId) || null;
    const { spec, problem, dropped } = buildSpec(settings, ds, set);
    const specKey = spec ? JSON.stringify(spec) : '';
    const [result, setResult] = useState({ key: '', data: null, error: '' });

    useEffect(() => {
        if (!specKey) return undefined;
        let alive = true;
        runWorkbenchQuery(JSON.parse(specKey)).then(
            (data) => alive && setResult({ key: specKey, data, error: '' }),
            (e) => alive && setResult({ key: specKey, data: null, error: workbenchError(e) }),
        );
        return () => { alive = false; };
    }, [specKey]);

    const colorOf = new Map((set?.members || []).map((m) => [m.id, m.color]));
    const current = result.key === specKey ? result : null;
    const data = current?.data;
    const grouped = data && data.spec.group_by !== 'none';

    const sortBy = (key, hib) => {
        const now = data?.spec.sort?.[0] || (spec?.sort?.[0]);
        const first = spec.columns[0];
        const active = now ? now.key : first;
        const activeDir = now ? now.dir : 'desc';
        const dir = active === key ? (activeDir === 'desc' ? 'asc' : 'desc') : (hib === false ? 'asc' : 'desc');
        onSettings({ sort: [{ key, dir }] });
    };
    const sortState = (key) => {
        const s = data?.spec.sort?.[0];
        const k = s ? s.key : spec?.columns[0];
        if (k !== key) return 'none';
        return (s ? s.dir : (data?.columns[0]?.higher_is_better === false ? 'asc' : 'desc')) === 'asc' ? 'ascending' : 'descending';
    };
    const sortHead = ({ k, label, title, hib, className = '' }) => {
        const st = sortState(k);
        return (
            <th key={k} scope="col" aria-sort={st} className={className} title={title}>
                <button type="button" className="wb-sort" onClick={() => sortBy(k, hib)}>
                    {label}
                    <Icon name={st === 'ascending' ? 'arrow_upward' : st === 'descending' ? 'arrow_downward' : 'unfold_more'} size={13} />
                </button>
            </th>
        );
    };

    const fields = data ? fieldColumns(data.fields) : [];
    const nCols = [];
    if (data) {
        if (grouped) {
            nCols.push({ key: 'n_rows', label: one(ds.row_label) === 'game' ? 'G' : 'Seasons', title: `${ds.row_label} combined in the row` });
            if (ds.row_label !== 'games') nCols.push({ key: 'n_games', label: 'G', title: 'games in the row' });
        } else if (ds.row_label !== 'games') {
            nCols.push({ key: 'n_games', label: 'G', title: 'games played that season' });
        }
    }
    const anyNoisy = data?.rows.some((r) => r.reliability && Object.values(r.reliability).some((x) => x?.noisy));
    const notes = data ? data.notes.filter((n) => !n.startsWith('Showing ')) : [];
    const showing = data?.notes.find((n) => n.startsWith('Showing '));
    const columnsByKey = new Map(ds.columns.map((c) => [c.key, c]));

    return (
        <div className="wb-table-block">
            {editing && (
                <TableSettings settings={settings} title={block.title} catalogue={catalogue} ds={ds} sets={board.sets} onChange={onSettings} onTitle={onTitle} />
            )}
            {dropped.length > 0 && (
                <p className="wb-warn">
                    Left out (not in the catalogue, or not offered): {dropped.map((k) => columnsByKey.get(k)?.label || k).join(', ')}.
                </p>
            )}
            {problem && <p className="wb-hint">{problem}</p>}
            {spec && !current && <p className="wb-meta" role="status">Loading…</p>}
            {current?.error && <p className="wb-error" role="alert">{current.error}</p>}
            {data && (
                <>
                    <p className="wb-summary">
                        {data.n.matched.toLocaleString()} {grouped ? 'rows' : rowUnit(ds, data.n.matched !== 1)} match
                        {data.truncated ? `, showing ${data.rows.length.toLocaleString()}` : ''}
                        {' · '}{seasonLabel(data.spec.season_from)}{data.spec.season_to !== data.spec.season_from ? ` to ${seasonLabel(data.spec.season_to)}` : ''}
                        {' · '}{groupLabel(data.spec.group_by, ds).toLowerCase()}
                        {data.columns.some((c) => c.kind === 'count') ? ` · counting stats ${ds.per_modes.find((p) => p.key === data.spec.per)?.label}` : ''}
                        <SourceBadge source={data._source} />
                    </p>
                    {showing && <p className="wb-meta">{showing}</p>}
                    {data.rows.length === 0 ? (
                        <p className="wb-hint">No rows match. Widen the seasons or lower the games floor.</p>
                    ) : (
                        <>
                            <TableExport name={blockTitleForFile(block, ds, set)} />
                            <div className="wb-table-wrap">
                                <table className="wb-table">
                                    <thead>
                                        <tr>
                                            <th scope="col" className="wb-rank">#</th>
                                            {fields.map((f) => (
                                                f.key === 'season' && (data.spec.group_by === 'none' || data.spec.group_by === 'season')
                                                    ? sortHead({ k: 'season', label: f.label, hib: true })
                                                    : <th key={f.key} scope="col" className={f.name ? 'wb-name-col' : ''}>{f.label}</th>
                                            ))}
                                            {nCols.map((c) => sortHead({ k: c.key, label: c.label, title: c.title, className: 'wb-num' }))}
                                            {data.columns.map((c) => (
                                                sortHead({
                                                    k: c.key, label: c.short || c.label, hib: c.higher_is_better, className: 'wb-num',
                                                    title: `${c.label}${c.per && c.kind === 'count' ? ` (${c.per})` : ''}. Combined: ${c.combines}. n is in ${c.n_unit}.${c.note ? ` ${c.note}` : ''}`,
                                                })
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.rows.map((row, i) => (
                                            <tr key={i}>
                                                <td className="wb-rank">{(data.spec.offset || 0) + i + 1}</td>
                                                {fields.map((f) => (
                                                    <td key={f.key} className={f.name ? 'wb-name-col' : f.unit ? 'wb-unit-col' : ''}>
                                                        {f.name ? nameCell(row, f.key, colorOf) : f.unit ? unitCell(f, row, colorOf) : fieldValue(f.key, row)}
                                                    </td>
                                                ))}
                                                {nCols.map((c) => <td key={c.key} className="wb-num">{row[c.key] == null ? '—' : Math.round(row[c.key]).toLocaleString()}</td>)}
                                                {data.columns.map((c) => {
                                                    const v = row[c.key];
                                                    const n = row.n?.[c.key];
                                                    const rel = row.reliability?.[c.key];
                                                    const noisy = rel?.noisy;
                                                    const shown = formatValue(c.format, v);
                                                    const nText = n == null ? '' : `n ${Math.round(n).toLocaleString()}`;
                                                    const ci = intervalText(c.format, row.ci?.[c.key]);
                                                    const tip = v == null
                                                        ? `${c.label}: not recorded`
                                                        : `${c.label}: ${shown}${ci ? ` (${c.interval} ${ci})` : ''} · n = ${n == null ? '—' : Math.round(n).toLocaleString()} ${c.n_unit}${rel ? ` · reliability ${rel.reliability.toFixed(2)}${noisy ? ' (under 0.5: mostly noise)' : ''}` : ''}`;
                                                    return (
                                                        <td key={c.key} className={`wb-num${noisy ? ' wb-noisy' : ''}`} title={tip}>
                                                            {shown}
                                                            {noisy && <span className="wb-noisy-mark" data-export-skip><span aria-hidden="true">*</span><span className="wb-sr">, small sample</span></span>}
                                                            {settings.showCi && ci && <span className="wb-ci" data-export-as={`(${ci})`}>{ci}</span>}
                                                            {settings.showN && v != null && <span className="wb-n" data-export-skip>{nText}</span>}
                                                        </td>
                                                    );
                                                })}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <p className="wb-meta">
                                Hover a value for its sample (n){anyNoisy ? '; greyed values with * have a sample too small to say more about the player than about luck (Stat Stability reliability under 0.5)' : ''}.
                                {' '}Rates over several rows are summed makes ÷ summed attempts, never averages of percentages.
                                {data.columns.some((c) => c.interval) ? ` Under a model’s value: ${[...new Set(data.columns.filter((c) => c.interval).map((c) => c.interval))].join('; ')}${grouped ? ' (shown one row at a time only: an interval doesn’t combine by adding)' : ''}.` : ''}
                            </p>
                            <MethodLinks columns={data.columns} />
                        </>
                    )}
                    {notes.length > 0 && (
                        <details className="wb-notes">
                            <summary>Notes on this data ({notes.length})</summary>
                            <ul>{notes.map((n) => <li key={n}>{n}</li>)}</ul>
                        </details>
                    )}
                </>
            )}
        </div>
    );
}

function blockTitleForFile(block, ds, set) {
    return block.title || `${ds.label}${set ? ` ${set.name}` : ''}`;
}
