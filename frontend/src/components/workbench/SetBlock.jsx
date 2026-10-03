import React from 'react';
import Icon from '../common/Icon';
import PlayerName from '../common/PlayerName';
import TeamLink from '../common/TeamLink';
import TeamLogo from '../common/TeamLogo';
import EntitySearch from './EntitySearch';
import { useAutosave } from '../../utils/useAutosave';
import { LIMITS, PALETTE_SIZE } from '../../utils/workbenchStore';
import { COLOR_NAMES, entityWord, nextColor, seriesVar } from './workbenchShared';

function SetName({ set, onRename }) {
    const [name, setName, flush] = useAutosave(set.name, (v) => (v.trim() ? onRename(v) : undefined));
    return (
        <input
            className="wb-input wb-set-name"
            value={name}
            maxLength={60}
            aria-label="Set name"
            onChange={(e) => setName(e.target.value)}
            onBlur={flush}
        />
    );
}

// A named group of players or of teams. Every block bound to the set (tables
// now, charts from step 4) reads its members, so editing it here updates them
// all. The colours are the ones those blocks use for each member.
export default function SetBlock({ set, sets, usedBy, onUpdateSet, onBindSet, onNewSet }) {
    if (!set) {
        return (
            <div className="wb-set">
                <p className="wb-hint">This block isn’t showing a set.</p>
                <div className="wb-row">
                    <button type="button" className="table-export-btn" onClick={() => onNewSet('player')}><Icon name="person_add" size={15} /> New player set</button>
                    <button type="button" className="table-export-btn" onClick={() => onNewSet('team')}><Icon name="group_add" size={15} /> New team set</button>
                </div>
            </div>
        );
    }
    const memberIds = new Set(set.members.map((m) => m.id));
    const full = set.members.length >= LIMITS.members;
    const add = (m) => onUpdateSet(set.id, (s) => {
        if (s.members.some((x) => x.id === m.id) || s.members.length >= LIMITS.members) return s;
        s.members.push({ ...m, color: nextColor(s.members) });
        return s;
    });
    const remove = (id) => onUpdateSet(set.id, (s) => ({ ...s, members: s.members.filter((m) => m.id !== id) }));
    const recolor = (id) => onUpdateSet(set.id, (s) => ({
        ...s, members: s.members.map((m) => (m.id === id ? { ...m, color: (m.color + 1) % PALETTE_SIZE } : m)),
    }));
    const setKind = (kind) => onUpdateSet(set.id, (s) => (s.members.length ? s : {
        ...s, kind, name: s.name.replace(/^(Players|Teams)(?= \d+$)/, kind === 'team' ? 'Teams' : 'Players'),
    }));

    return (
        <div className="wb-set">
            <div className="wb-row wb-set-head">
                <SetName key={set.id} set={set} onRename={(name) => onUpdateSet(set.id, (s) => ({ ...s, name: name.trim().slice(0, 60) }))} />
                {sets.length > 1 && (
                    <select className="wb-select" aria-label="Which set this block shows" value={set.id} onChange={(e) => onBindSet(e.target.value)}>
                        {sets.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.members.length})</option>)}
                    </select>
                )}
            </div>
            {set.members.length === 0 ? (
                <div className="wb-seg" role="group" aria-label="This set holds">
                    {['player', 'team'].map((k) => (
                        <button key={k} type="button" className={`wb-seg-btn${set.kind === k ? ' wb-seg-btn--on' : ''}`}
                            aria-pressed={set.kind === k} onClick={() => setKind(k)}>
                            {k === 'player' ? 'Players' : 'Teams'}
                        </button>
                    ))}
                </div>
            ) : (
                <p className="wb-meta">
                    {set.members.length} {entityWord(set.kind, set.members.length !== 1)}
                    {' · '}
                    {usedBy.length ? `used by ${usedBy.join(', ')}` : 'not used by a table yet'}
                </p>
            )}
            <EntitySearch kind={set.kind} memberIds={memberIds} onPick={add} disabled={full} />
            {full && <p className="wb-meta">A set holds up to {LIMITS.members} {entityWord(set.kind)}.</p>}
            {set.members.length === 0 ? (
                <p className="wb-hint">
                    Add {entityWord(set.kind)} here, then add a Table and choose this set as its rows. Every block bound
                    to the set changes when you change it.
                </p>
            ) : (
                <ul className="wb-members" aria-label={`Members of ${set.name}`}>
                    {set.members.map((m) => (
                        <li key={m.id} className="wb-member">
                            <button
                                type="button"
                                className="wb-swatch"
                                style={{ '--wb-c': seriesVar(m.color) }}
                                onClick={() => recolor(m.id)}
                                aria-label={`${m.name}'s colour is ${COLOR_NAMES[m.color]}; change it`}
                                title={`Colour: ${COLOR_NAMES[m.color]} (click to change)`}
                            />
                            <span className="wb-member-name">
                                {set.kind === 'player'
                                    ? <PlayerName playerId={m.id} name={m.name} size={22} />
                                    : (
                                        <TeamLink abbr={m.id}>
                                            <TeamLogo abbreviation={m.id} size={20} />
                                            <span>{m.name}</span>
                                        </TeamLink>
                                    )}
                            </span>
                            <button type="button" className="wb-icon-btn" onClick={() => remove(m.id)} aria-label={`Remove ${m.name} from ${set.name}`} title="Remove">
                                <Icon name="close" size={16} />
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
