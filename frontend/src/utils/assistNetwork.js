// Which players and lines an assist network draws (common/AssistNetwork.jsx,
// pages/AssistNetwork.jsx): the top `nPlayers` by minutes (no unidentified
// player), the pairs between them with `minEdge`+ assists, and how many of
// the team's assists between named players the drawing covers.
export function pickNetwork(players, edges, nPlayers, minEdge) {
    const nodes = players.filter((p) => p.player_id !== 0 && p.minutes > 0).slice(0, nPlayers);
    const ids = new Set(nodes.map((p) => p.player_id));
    const inside = edges.filter((e) => ids.has(e.passer_id) && ids.has(e.scorer_id));
    return {
        nodes,
        lines: inside.filter((e) => e.ast >= minEdge),
        insideAst: inside.reduce((s, e) => s + e.ast, 0),
        totalAst: edges.reduce((s, e) => s + (e.scorer_id ? e.ast : 0), 0),
    };
}
