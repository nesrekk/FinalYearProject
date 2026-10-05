import React, { useEffect, useState } from 'react';
import { namesakes, playerSpan } from '../../utils/playerChoice';
import '../../styles/profile.css';

// "Another player is also called Brandon Williams: the one of 1997-98 to
// 2002-03": 19 names belong to two players, and a typed name opens the latest
// one. onPick({ id, name }) switches to the other.
export default function NamesakeNote({ name, id, onPick, style }) {
  const [others, setOthers] = useState({ key: null, list: [] });
  const key = name && id ? `${id}:${name}` : null;
  useEffect(() => {
    if (!key) return undefined;
    let live = true;
    namesakes(name)
      .then((list) => { if (live) setOthers({ key, list: list.filter((p) => p.id !== id) }); })
      .catch(() => { if (live) setOthers({ key, list: [] }); });
    return () => { live = false; };
  }, [key, name, id]);
  const list = others.key === key ? others.list : [];
  if (!list.length) return null;
  return (
    <p className="page-subtitle" style={{ marginTop: '0.5rem', ...style }}>
      Another player is also called {name}:{' '}
      {list.map((p, i) => (
        <React.Fragment key={p.id}>
          {i > 0 && ', '}
          <button type="button" className="pp-link" onClick={() => onPick({ id: p.id, name: p.name })}>
            the one of {playerSpan(p)}
          </button>
        </React.Fragment>
      ))}
    </p>
  );
}
