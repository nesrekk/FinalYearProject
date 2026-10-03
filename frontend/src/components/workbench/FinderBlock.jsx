import React, { useEffect, useState } from 'react';
import Icon from '../common/Icon';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import { fetchWorkbenchTeams, runWorkbenchFinder, workbenchError, workbenchRetryable } from '../../services/api';
import { BlockError, Loading } from './BlockStatus';
import { LIMITS } from '../../utils/workbenchStore';
import { useAutosave } from '../../utils/useAutosave';
import { COLOR_NAMES, formatValue, seasonLabel, seriesVar } from './workbenchShared';
import {
    COUNT_OPS, FINDER_DATASETS, MAX_CONDITIONS, MAX_TESTS, PER_WORDS, VALUE_OPS, buildFinderSpec, conditionTitle,
    defaultMinN, hasSampleFloor, rowWord, shown, stored,
} from './finderSpec';
import { LIMIT_CHOICES } from './tableSpec';

// ── The sentence's boxes ───────────────────────────────────────────────

function StatSelect({ ds, value, onChange, label, combineOnly }) {
    const groups = [];
    for (const c of ds.columns) {
        let g = groups.find((x) => x.name === c.group);
        if (!g) groups.push(g = { name: c.group, cols: [] });
        g.cols.push(c);
    }
    return (
        <select className="wb-select wb-fd-stat" value={value} aria-label={label} onChange={(e) => onChange(e.target.value)}>
            {groups.map((g) => (
                <optgroup key={g.name} label={g.name}>
                    {g.cols.map((c) => {
                        const off = c.status !== 'verified';
                        const single = combineOnly && c.kind === 'none';
                        return (
                            <option key={c.key} value={c.key} disabled={off || single}
                                title={off ? `Not offered: ${c.reason}` : single ? 'One value per season: use it in a single season with season stats, or in a count.' : c.note || ''}>
                                {c.label}{off ? ' (not offered)' : single ? ' (one season at a time)' : ''}
                            </option>
                        );
                    })}
                </optgroup>
            ))}
        </select>
    );
}

function NumberBox({ value, fmt, onChange, label, width = '5em' }) {
    // Keeps what was typed ("0." while typing) and stores the number.
    const [text, setText] = useState(shown(value, fmt));
    const [prev, setPrev] = useState(value);
    if (prev !== value) {
        setPrev(value);
        if (stored(text, fmt) !== value) setText(shown(value, fmt));
    }
    return (
        <span className="wb-fd-num">
            <input className="wb-input" type="number" step="any" inputMode="decimal" value={text} aria-label={label} style={{ width }}
                onChange={(e) => { setText(e.target.value); onChange(stored(e.target.value, fmt)); }} />
            {fmt === 'pct' && <span aria-hidden="true">%</span>}
        </span>
    );
}

function TestBoxes({ ds, test, onChange, label }) {
    const col = ds.columns.find((c) => c.key === test.stat);
    const fmt = col?.format;
    return (
        <span className="wb-fd-test">
            <StatSelect ds={ds} value={test.stat} label={`${label}: stat`} onChange={(stat) => onChange({ stat })} />
            <select className="wb-select" value={test.op} aria-label={`${label}: comparison`} onChange={(e) => onChange({ op: e.target.value })}>
                {VALUE_OPS.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
            </select>
            <NumberBox value={test.value} fmt={fmt} label={`${label}: ${test.op === 'between' ? 'from' : 'value'}`} onChange={(value) => onChange({ value })} />
            {test.op === 'between' && (
                <>
                    <span className="wb-meta">and</span>
                    <NumberBox value={test.value2} fmt={fmt} label={`${label}: to`} onChange={(value2) => onChange({ value2 })} />
                </>
            )}
        </span>
    );
}

function ConditionLine({ i, cond, ds, scope, catalogue, minGames, onChange, onRemove, canRemove }) {
    const rows = rowWord(ds.key);
    const row = rowWord(ds.key, false);
    const col = cond.type === 'value' ? ds.columns.find((c) => c.key === cond.stat) : null;
    const name = `Condition ${i + 1}`;
    const setType = (type) => {
        if (type === cond.type) return;
        const stat = cond.type === 'value' ? cond.stat : cond.tests[0].stat;
        const value = cond.type === 'value' ? cond.value : cond.tests[0].value;
        if (type === 'value') onChange({ type, stat, op: 'gte', value, value2: null, per: 'game', minN: null }, true);
        else onChange({ type, tests: [{ stat, op: 'gte', value, value2: null }], countOp: 'gte', count: type === 'count' ? 10 : 5 }, true);
    };
    const changeTest = (j, patch) => onChange({ tests: cond.tests.map((t, k) => (k === j ? { ...t, ...patch } : t)) });
    const single = ds.key === 'player_season' && scope === 'season';
    return (
        <li className="wb-fd-cond">
            <span className="wb-fd-and">{i === 0 ? 'who' : 'and'}</span>
            <select className="wb-select" value={cond.type} aria-label={`${name}: kind`} onChange={(e) => setType(e.target.value)}>
                <option value="value">{single ? 'had (that season)' : `over the ${rows}, had`}</option>
                <option value="count">{`in a number of ${rows}, had`}</option>
                <option value="streak">{`${rows} in a row, had`}</option>
            </select>
            {cond.type === 'value' && (
                <span className="wb-fd-test">
                    <StatSelect ds={ds} value={cond.stat} label={`${name}: stat`} combineOnly={!single}
                        onChange={(stat) => {
                            const c = ds.columns.find((x) => x.key === stat);
                            onChange({ stat, per: c?.kind === 'count' ? cond.per : 'game', minN: hasSampleFloor(c) ? defaultMinN(catalogue, stat, minGames) : null });
                        }} />
                    {col?.kind === 'count' && (
                        <select className="wb-select" value={(col.per_modes || []).includes(cond.per) ? cond.per : 'game'} aria-label={`${name}: per`} onChange={(e) => onChange({ per: e.target.value })}>
                            {(col.per_modes || ['game']).map((p) => <option key={p} value={p}>{PER_WORDS[p]}</option>)}
                        </select>
                    )}
                    <select className="wb-select" value={cond.op} aria-label={`${name}: comparison`} onChange={(e) => onChange({ op: e.target.value })}>
                        {VALUE_OPS.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
                    </select>
                    <NumberBox value={cond.value} fmt={col?.format} label={`${name}: ${cond.op === 'between' ? 'from' : 'value'}`} onChange={(value) => onChange({ value })} />
                    {cond.op === 'between' && (
                        <>
                            <span className="wb-meta">and</span>
                            <NumberBox value={cond.value2} fmt={col?.format} label={`${name}: to`} onChange={(value2) => onChange({ value2 })} />
                        </>
                    )}
                    {hasSampleFloor(col) && (
                        <span className="wb-fd-floor">
                            on at least
                            <NumberBox value={cond.minN} label={`${name}: minimum ${col.n_unit}`} width="5.5em"
                                onChange={(v) => onChange({ minN: v != null && v > 0 ? Math.round(v) : null })} />
                            {col.n_unit.split(' (')[0]}
                        </span>
                    )}
                </span>
            )}
            {cond.type !== 'value' && (
                <span className="wb-fd-tests">
                    <span className="wb-fd-test">
                        in
                        {cond.type === 'count' ? (
                            <select className="wb-select" value={cond.countOp} aria-label={`${name}: how many`} onChange={(e) => onChange({ countOp: e.target.value })}>
                                {COUNT_OPS.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
                            </select>
                        ) : ' at least '}
                        <NumberBox value={cond.count} label={`${name}: number of ${rows}`} width="4.5em"
                            onChange={(v) => onChange({ count: v != null && v >= 1 ? Math.min(100000, Math.round(v)) : null })} />
                        {cond.type === 'count' ? rows : `straight ${rows} he played`}, had
                    </span>
                    {cond.tests.map((t, j) => (
                        <span key={j} className="wb-fd-test">
                            {j > 0 && <span className="wb-fd-and">and</span>}
                            <TestBoxes ds={ds} test={t} label={`${name}, test ${j + 1}`} onChange={(patch) => changeTest(j, patch)} />
                            {cond.tests.length > 1 && (
                                <button type="button" className="wb-icon-btn" aria-label={`Remove test ${j + 1} of condition ${i + 1}`}
                                    onClick={() => onChange({ tests: cond.tests.filter((_, k) => k !== j) })}><Icon name="close" size={15} /></button>
                            )}
                        </span>
                    ))}
                    {cond.tests.length > 1 && <span className="wb-meta">all in the same {row}</span>}
                    {cond.tests.length < MAX_TESTS && (
                        <button type="button" className="wb-link" onClick={() => onChange({ tests: [...cond.tests, { ...cond.tests[cond.tests.length - 1] }] })}>
                            + and, in the same {row}
                        </button>
                    )}
                </span>
            )}
            {canRemove && (
                <button type="button" className="wb-icon-btn" onClick={onRemove} aria-label={`Remove condition ${i + 1}`} title="Remove condition">
                    <Icon name="delete" size={16} />
                </button>
            )}
        </li>
    );
}

function TitleInput({ value, onSave }) {
    const [draft, setDraft, flush] = useAutosave(value, onSave);
    return <input className="wb-input" value={draft} maxLength={LIMITS.title} placeholder="(automatic)" onChange={(e) => setDraft(e.target.value)} onBlur={flush} />;
}

function Sentence({ draft, ds, catalogue, sets, teams, onDraft }) {
    const set = (patch) => onDraft({ ...draft, ...patch });
    const seasons = [];
    for (let s = ds.seasons.to; s >= ds.seasons.from; s -= 1) seasons.push(s);
    const rows = rowWord(ds.key);
    const changeDataset = (key) => {
        const next = catalogue.datasets.find((d) => d.key === key);
        const has = (k) => next.columns.some((c) => c.key === k && c.status === 'verified');
        const conditions = draft.conditions.map((c) => (c.type === 'value'
            ? { ...c, stat: has(c.stat) ? c.stat : 'pts' }
            : { ...c, tests: c.tests.map((t) => ({ ...t, stat: has(t.stat) ? t.stat : 'pts' })) }));
        set({ dataset: key, conditions, seasonFrom: null, seasonTo: null, where: 'all', result: 'all', opponent: null, sort: null });
    };
    const changeCond = (i, patch, replace) => set({
        conditions: draft.conditions.map((c, k) => (k === i ? (replace ? patch : { ...c, ...patch }) : c)),
        sort: replace ? null : draft.sort,
    });
    const playerSets = sets.filter((s) => s.kind === 'player');
    return (
        <div className="wb-fd-sentence">
            <p className="wb-fd-line">
                <span className="wb-fd-lead">Players</span>
                <select className="wb-select" value={draft.setId || ''} aria-label="Look through" onChange={(e) => set({ setId: e.target.value || null })}>
                    <option value="">(anyone)</option>
                    {playerSets.map((s) => <option key={s.id} value={s.id}>in {s.name} ({s.members.length})</option>)}
                </select>
                <span>using</span>
                <select className="wb-select" value={ds.key} aria-label="Data" onChange={(e) => changeDataset(e.target.value)}>
                    {FINDER_DATASETS.map((k) => {
                        const d = catalogue.datasets.find((x) => x.key === k);
                        return d && <option key={k} value={k}>{k === 'player_game' ? 'game logs' : 'season stats'} ({seasonLabel(d.seasons.from)} on)</option>;
                    })}
                </select>
                <select className="wb-select" value={draft.scope} aria-label="Judged over" onChange={(e) => set({ scope: e.target.value, sort: null })}>
                    <option value="season">in a single season</option>
                    <option value="span">over all the {rows} combined</option>
                </select>
                <span>from</span>
                <select className="wb-select" aria-label="First season" value={draft.seasonFrom ?? ''} onChange={(e) => set({ seasonFrom: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">earliest ({seasonLabel(ds.seasons.from)})</option>
                    {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                </select>
                <span>to</span>
                <select className="wb-select" aria-label="Last season" value={draft.seasonTo ?? ''} onChange={(e) => set({ seasonTo: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">latest ({seasonLabel(ds.seasons.to)})</option>
                    {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                </select>
                <span>, over at least</span>
                <NumberBox value={draft.minGames} label="Minimum games" width="4.5em"
                    onChange={(v) => set({ minGames: v != null && v > 0 ? Math.min(5000, Math.round(v)) : null })} />
                <span>games,</span>
            </p>
            <p className="wb-fd-line">
                <span>counting only</span>
                {ds.key === 'player_game' && (
                    <>
                        <select className="wb-select" value={draft.where} aria-label="Home or away" onChange={(e) => set({ where: e.target.value })}>
                            <option value="all">home and away</option>
                            <option value="home">home</option>
                            <option value="away">away</option>
                        </select>
                        <select className="wb-select" value={draft.result} aria-label="Result" onChange={(e) => set({ result: e.target.value })}>
                            <option value="all">games, won or lost,</option>
                            <option value="W">wins</option>
                            <option value="L">losses</option>
                        </select>
                        <select className="wb-select" value={draft.opponent || ''} aria-label="Opponent" onChange={(e) => set({ opponent: e.target.value || null })}>
                            <option value="">against anyone</option>
                            {teams.map((t) => <option key={t.id} value={t.team}>against {t.team}</option>)}
                        </select>
                    </>
                )}
                <span>{ds.key === 'player_game' ? 'with' : `${rows} with`} at least</span>
                <NumberBox value={draft.minMinutes} label={ds.key === 'player_game' ? 'Minimum minutes in the game' : 'Minimum minutes a game that season'} width="4em"
                    onChange={(v) => set({ minMinutes: v != null && v > 0 ? Math.min(60, v) : null })} />
                <span>{ds.key === 'player_game' ? 'minutes played' : 'minutes a game'},</span>
            </p>
            <ol className="wb-fd-conds">
                {draft.conditions.map((c, i) => (
                    <ConditionLine key={i} i={i} cond={c} ds={ds} scope={draft.scope} catalogue={catalogue} minGames={draft.minGames}
                        onChange={(patch, replace) => changeCond(i, patch, replace)}
                        onRemove={() => set({ conditions: draft.conditions.filter((_, k) => k !== i), sort: null })}
                        canRemove={draft.conditions.length > 1} />
                ))}
            </ol>
            {draft.conditions.length < MAX_CONDITIONS && (
                <button type="button" className="wb-link" onClick={() => set({ conditions: [...draft.conditions, { type: 'value', stat: 'ast', op: 'gte', value: 5, value2: null, per: 'game', minN: null }] })}>
                    + Add a condition
                </button>
            )}
        </div>
    );
}

// ── Result cells ───────────────────────────────────────────────────────

function ValueCell({ meta, c }) {
    const col = meta.column;
    const rel = c.reliability;
    const noisy = rel?.noisy;
    const nText = c.n == null ? '—' : `${Math.round(c.n).toLocaleString()} ${col.n_unit.split(' (')[0]}`;
    return (
        <td className={`wb-num${noisy ? ' wb-noisy' : ''}`}
            title={`${col.label}: ${formatValue(col.format, c.value)} · n = ${nText}${rel ? ` · reliability ${rel.reliability.toFixed(2)}${noisy ? ' (under 0.5: mostly noise)' : ''}` : ''}`}>
            {formatValue(col.format, c.value)}
            {noisy && <span className="wb-noisy-mark" data-export-skip><span aria-hidden="true">*</span><span className="wb-sr">, small sample</span></span>}
            <span className="wb-n" data-export-skip>n {nText}</span>
        </td>
    );
}

function cellFor(meta, c, dsKey) {
    if (meta.type === 'value') return <ValueCell key={meta.index} meta={meta} c={c} />;
    if (meta.type === 'count') {
        return (
            <td key={meta.index} className="wb-num" title={`${meta.text}: ${c.count} of his ${c.of} ${rowWord(dsKey)}`}>
                {c.count.toLocaleString()}<span className="wb-n" data-export-skip>of {c.of.toLocaleString()}</span>
            </td>
        );
    }
    const span = c.streak ? (dsKey === 'player_game' ? `${c.from} to ${c.to}` : `${seasonLabel(c.from)} to ${seasonLabel(c.to)}`) : 'none';
    return (
        <td key={meta.index} className="wb-num" title={`Longest run: ${c.streak} (${span})`}>
            {c.streak.toLocaleString()}<span className="wb-n" data-export-skip>{span}</span>
        </td>
    );
}

// ── Making a set from the result ───────────────────────────────────────

function UsePanel({ players, sets, truncated, onAddToSet, onNewBoard }) {
    const playerSets = sets.filter((s) => s.kind === 'player');
    const [target, setTarget] = useState('');
    const [name, setName] = useState('');
    const capped = Math.min(players.length, LIMITS.members);
    return (
        <div className="wb-fd-use" role="group" aria-label="Use these players">
            <span className="wb-meta">
                {players.length.toLocaleString()} player{players.length === 1 ? '' : 's'} listed{truncated ? ' (only the rows shown; raise “Rows shown” for more)' : ''}{players.length > LIMITS.members ? `; a set holds the first ${LIMITS.members}` : ''}.
            </span>
            <select className="wb-select" value={target} aria-label="Add them to" onChange={(e) => setTarget(e.target.value)}>
                <option value="">a new set…</option>
                {playerSets.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.members.length})</option>)}
            </select>
            {!target && <input className="wb-input" value={name} maxLength={60} placeholder="New set’s name" aria-label="New set’s name" onChange={(e) => setName(e.target.value)} />}
            <button type="button" className="table-export-btn" disabled={!capped} onClick={() => onAddToSet(target || null, players.slice(0, capped), name.trim())}>
                <Icon name="group_add" size={15} /> Add to set
            </button>
            <button type="button" className="table-export-btn" disabled={!capped} onClick={() => onNewBoard(players.slice(0, capped), name.trim())}>
                <Icon name="dashboard_customize" size={15} /> New board from these
            </button>
        </div>
    );
}

// ── The block ──────────────────────────────────────────────────────────

export default function FinderBlock({ block, board, catalogue, editing, onSettings, onTitle, onAddToSet, onNewBoard }) {
    const { settings } = block;
    const settingsKey = JSON.stringify(settings);
    // The boxes edit a draft; Find players stores it as the block's settings,
    // and the result always shows the stored settings (what was run).
    const [draft, setDraft] = useState(settings);
    const [base, setBase] = useState(settingsKey);
    if (base !== settingsKey) {
        // Stored settings changed (a run, a sort, another tab). Unsaved edits
        // to the boxes stay; only the order and row count follow.
        const edited = JSON.stringify(draft) !== base;
        setBase(settingsKey);
        setDraft(edited ? { ...draft, sort: settings.sort, limit: settings.limit } : settings);
    }
    const [teams, setTeams] = useState([]);
    useEffect(() => {
        let alive = true;
        fetchWorkbenchTeams().then((d) => alive && setTeams(d.results.filter((t) => t.to >= 2021)), () => {});
        return () => { alive = false; };
    }, []);

    const dsOf = (s) => catalogue.datasets.find((d) => d.key === s.dataset) || catalogue.datasets.find((d) => d.key === 'player_season');
    const ds = dsOf(settings);
    const draftDs = dsOf(draft);
    const set = board.sets.find((s) => s.id === settings.setId) || null;
    const draftSet = board.sets.find((s) => s.id === draft.setId) || null;
    const { spec, problems } = buildFinderSpec(settings, ds, set);
    const draftCheck = buildFinderSpec(draft, draftDs, draftSet);
    const specKey = spec ? JSON.stringify(spec) : '';
    const dirty = JSON.stringify(draft) !== settingsKey;
    const [result, setResult] = useState({ key: '', data: null, error: '', retry: false });
    const [attempt, setAttempt] = useState(0);

    useEffect(() => {
        if (!specKey) return undefined;
        let alive = true;
        runWorkbenchFinder(JSON.parse(specKey)).then(
            (data) => alive && setResult({ key: specKey, data, error: '', retry: false }),
            (e) => alive && setResult({ key: specKey, data: null, error: workbenchError(e), retry: workbenchRetryable(e) }),
        );
        return () => { alive = false; };
    }, [specKey, attempt]);
    const retry = () => { setResult((r) => ({ ...r, key: '' })); setAttempt((n) => n + 1); };

    const current = result.key === specKey ? result : null;
    const data = current?.data;
    const colorOf = new Map((set?.members || []).map((m) => [m.id, m.color]));
    const bySeason = data?.spec.scope === 'season';
    const sortNow = data?.spec.sort;
    const sortBy = (key, defaultDir) => {
        const dir = sortNow?.key === key ? (sortNow.dir === 'desc' ? 'asc' : 'desc') : defaultDir;
        onSettings({ sort: { key, dir } });
    };
    const head = ({ k, label, title, dir = 'desc' }) => {
        const st = sortNow?.key === k ? (sortNow.dir === 'asc' ? 'ascending' : 'descending') : 'none';
        return (
            <th key={k} scope="col" className="wb-num" aria-sort={st} title={title}>
                <button type="button" className="wb-sort" onClick={() => sortBy(k, dir)}>
                    {label}
                    <Icon name={st === 'ascending' ? 'arrow_upward' : st === 'descending' ? 'arrow_downward' : 'unfold_more'} size={13} />
                </button>
            </th>
        );
    };
    const players = [];
    if (data) {
        const seen = new Set();
        for (const r of data.rows) {
            if (!seen.has(r.player_id)) {
                seen.add(r.player_id);
                players.push({ id: r.player_id, name: r.player_name || `Player ${r.player_id}`, team: r.team });
            }
        }
    }
    const anyNoisy = data?.rows.some((r) => r.conditions.some((c) => c.reliability?.noisy));

    return (
        <div className="wb-table-block wb-finder">
            {editing && (
                <div className="wb-settings">
                    <label className="wb-field">
                        <span>Title</span>
                        <TitleInput value={block.title} onSave={onTitle} />
                    </label>
                    <label className="wb-field">
                        <span>Rows shown</span>
                        <select className="wb-select" value={settings.limit} onChange={(e) => onSettings({ limit: Number(e.target.value) })}>
                            {LIMIT_CHOICES.map((n) => <option key={n} value={n}>{n}</option>)}
                        </select>
                    </label>
                </div>
            )}
            <Sentence draft={draft} ds={draftDs} catalogue={catalogue} sets={board.sets} teams={teams} onDraft={setDraft} />
            <div className="wb-row wb-fd-run">
                <button type="button" className="table-export-btn wb-fd-go" disabled={!draftCheck.spec || !dirty} onClick={() => onSettings(draft)}>
                    <Icon name="person_search" size={15} /> Find players
                </button>
                {dirty && <button type="button" className="wb-link" onClick={() => setDraft(settings)}>Undo changes</button>}
                <span className="wb-meta" role="status">
                    {dirty ? (draftCheck.spec ? 'Changed: press Find players to run it.' : '') : ''}
                </span>
            </div>
            {dirty && draftCheck.problems.length > 0 && <p className="wb-hint">{draftCheck.problems.join(' ')}</p>}
            {!dirty && problems.length > 0 && <p className="wb-hint">{problems.join(' ')}</p>}
            {spec && !current && <Loading what="players who match" />}
            {current?.error && <BlockError message={current.error} onRetry={current.retry ? retry : null} />}
            {data && (
                <>
                    <p className="wb-fd-ran"><strong>Ran:</strong> {data.sentence}</p>
                    <p className="wb-summary">
                        {data.n.matched.toLocaleString()} {bySeason ? `player-${rowWord('player_season', false)}s` : 'players'} match
                        {bySeason ? ` (${data.n.players.toLocaleString()} players)` : ''} of {data.n.pool.toLocaleString()} looked at
                        {data.truncated ? `, showing ${data.rows.length.toLocaleString()}` : ''}
                        <SourceBadge source={data._source} />
                    </p>
                    {data.rows.length === 0 ? (
                        <p className="wb-hint">No one matches. Loosen a condition, widen the seasons or lower the games floor.</p>
                    ) : (
                        <>
                            <TableExport name={block.title || 'Player Finder'} />
                            <div className="wb-table-wrap">
                                <table className="wb-table">
                                    <thead>
                                        <tr>
                                            <th scope="col" className="wb-rank">#</th>
                                            <th scope="col" className="wb-name-col">Player</th>
                                            {bySeason && head({ k: 'season', label: 'Season' })}
                                            <th scope="col">Team</th>
                                            {head({ k: 'n_games', label: 'G', title: `games ${bySeason ? 'that season' : 'in all'}` })}
                                            {data.conditions.map((m, i) => head({
                                                k: `c${i}`,
                                                label: conditionTitle(data.spec.conditions[i] && toSettingsCond(data.spec.conditions[i]), ds),
                                                title: m.text,
                                                dir: m.type === 'value' && ['lte', 'lt'].includes(m.op) ? 'asc' : 'desc',
                                            }))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.rows.map((r, i) => {
                                            const c = colorOf.get(r.player_id);
                                            return (
                                                <tr key={`${r.player_id}-${r.season ?? ''}`}>
                                                    <td className="wb-rank">{(data.spec.offset || 0) + i + 1}</td>
                                                    <td className="wb-name-col">
                                                        <span className="wb-namecell">
                                                            {c != null && <span className="wb-dot" style={{ '--wb-c': seriesVar(c) }} aria-hidden="true" title={COLOR_NAMES[c]} />}
                                                            <PlayerName playerId={r.player_id} name={r.player_name || `Player ${r.player_id}`} size={24} />
                                                        </span>
                                                    </td>
                                                    {bySeason && <td>{seasonLabel(r.season)}</td>}
                                                    <td>{r.team ? <TeamLink abbr={r.team} season={r.season} logoSize={18} /> : '—'}</td>
                                                    <td className="wb-num">{r.n_games == null ? '—' : Math.round(r.n_games).toLocaleString()}</td>
                                                    {data.conditions.map((m, k) => cellFor(m, r.conditions[k], ds.key))}
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                            <p className="wb-meta">
                                Each value shows its sample (n){anyNoisy ? '; greyed values with * rest on a sample too small to say more about the player than about luck (Stat Stability reliability under 0.5)' : ''}.
                                {' '}Averages are summed stats ÷ summed games, percentages summed makes ÷ summed attempts; counts show his {rowWord(ds.key)} meeting the condition out of all of them; a run shows its first and last {rowWord(ds.key, false)}.
                            </p>
                            <UsePanel key={specKey} players={players} sets={board.sets} truncated={data.truncated}
                                onAddToSet={(setId, list, name) => onAddToSet(setId, list, name)}
                                onNewBoard={(list, name) => onNewBoard(list, name, settings, data.sentence)} />
                        </>
                    )}
                    {data.notes.length > 0 && (
                        <details className="wb-notes">
                            <summary>Notes on this data ({data.notes.length})</summary>
                            <ul>{data.notes.map((n) => <li key={n}>{n}</li>)}</ul>
                        </details>
                    )}
                </>
            )}
        </div>
    );
}

// The API's condition back in the settings' shape, for its column title.
function toSettingsCond(c) {
    const one = (t) => ({ ...t, value: Array.isArray(t.value) ? t.value[0] : t.value, value2: Array.isArray(t.value) ? t.value[1] : null });
    return c.type === 'value' ? one(c) : { ...c, tests: c.tests.map(one) };
}
