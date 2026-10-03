// Shared by the Shot Charts page and the Workbench's shot chart block.

// Attempts, makes, FG% and 3P% of a list of shots.
export default function shotTotals(shots) {
  const att = shots.length;
  const makes = shots.reduce((acc, s) => acc + (Number(s.shot_made_flag) === 1 ? 1 : 0), 0);
  const threes = shots.filter((s) => String(s.shot_type || '').includes('3PT'));
  const tAtt = threes.length;
  const tMake = threes.reduce((acc, s) => acc + (Number(s.shot_made_flag) === 1 ? 1 : 0), 0);
  return {
    attempts: att,
    makes,
    fgPct: att ? (makes / att) : 0,
    threeAtt: tAtt,
    threeMake: tMake,
    threePct: tAtt ? (tMake / tAtt) : 0,
  };
}
