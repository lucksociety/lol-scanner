/* Luck Society · pure pricing math for the Simulate panel (shared by the page and the Node tests). */
(function (root) {
  const C = {};
  C.sigmoid = x => 1 / (1 + Math.exp(-x));
  C.logit = p => Math.log(p / (1 - p));
  C.eloP = (ra, rb, scale) => 1 / (1 + Math.pow(10, -(ra - rb) / (scale || 400)));

  /** "0.62" | "62%" | "62c" | "+130" | "-150" | "130" -> {p, american} or null. */
  C.parsePrice = function (s) {
    if (s === null || s === undefined) return null;
    s = String(s).trim().replace(/\s+/g, "");
    if (!s) return null;
    let m;
    if ((m = s.match(/^([+-])(\d+(?:\.\d+)?)$/)) && +m[2] >= 100) {
      const v = +m[2]; return { p: m[1] === "-" ? v / (v + 100) : 100 / (v + 100), american: true };
    }
    if ((m = s.match(/^(\d+(?:\.\d+)?)%$/))) { const p = +m[1] / 100; return p > 0 && p < 1 ? { p, american: false } : null; }
    if ((m = s.match(/^(\d+(?:\.\d+)?)(c|¢)$/i))) { const p = +m[1] / 100; return p > 0 && p < 1 ? { p, american: false } : null; }
    if ((m = s.match(/^\d*\.?\d+$/))) {
      const v = +s;
      if (v > 0 && v < 1) return { p: v, american: false };
      if (v >= 100) return { p: 100 / (v + 100), american: true };   // unsigned 3-digit = American plus odds
    }
    return null;
  };
  C.fee = (p, coef) => coef * p * (1 - p);

  /** f(aw, bw) -> true / false / null (null = depends on game order). Mirrors scanner/edge.py outcome_fn. */
  C.outcomeFn = function (mk) {
    const A = mk.side === "A";
    let f;
    switch (mk.kind) {
      case "ml": f = (a, b) => A ? a > b : b > a; break;
      case "hcp": f = (a, b) => A ? (a - b) + mk.line > 0 : (b - a) + mk.line > 0; break;
      case "total": f = (a, b) => mk.side === "over" ? a + b > mk.line : a + b < mk.line; break;
      case "exact": f = (a, b) => a === mk.line[0] && b === mk.line[1]; break;
      case "game": f = (a, b) => {
        const n = a + b, g = mk.game;
        if (g > n) return false;              // not played -> 50-50 void, not a cash
        const aWon = a > b;
        if (g === n || Math.min(a, b) === 0) return aWon === A;
        return null; }; break;
      default: f = () => null;
    }
    if (!mk.neg) return f;
    return (a, b) => { const r = f(a, b); return r === null ? null : !r; };
  };

  /** Model probability (expected payout for game markets) from a sim result {n, exact, reach, gwin}. */
  C.modelProb = function (mk, res) {
    let q;
    if (mk.kind === "game") {
      const g = mk.game - 1;
      if (g < 0 || g >= res.reach.length) return null;
      const reach = res.reach[g] / res.n, pa = res.reach[g] ? res.gwin[g] / res.reach[g] : 0.5;
      q = reach * (mk.side === "A" ? pa : 1 - pa) + (1 - reach) * 0.5;
      return mk.neg ? 1 - q : q;
    }
    const f = C.outcomeFn({ ...mk, neg: false });
    q = 0;
    for (const k in res.exact) { const [a, b] = k.split("-").map(Number); if (f(a, b)) q += res.exact[k] / res.n; }
    return mk.neg ? 1 - q : q;
  };

  C.lockOf = function (res) {
    let best = null, bv = -1;
    for (const k in res.exact) if (res.exact[k] > bv) { bv = res.exact[k]; best = k; }
    const [a, b] = best.split("-").map(Number);
    return { key: best, a, b, p: bv / res.n };
  };

  /** rows: [{kind, side, line, game, neg, price (string), opp (string|undefined), pair_id, fee (bool)}] */
  C.MIN_ASK = 0.03; C.MAX_ASK = 0.97;
  C.sanitySkip = function (x) {
    if (x.implied < C.MIN_ASK) return "price < " + C.MIN_ASK.toFixed(2);
    if (x.implied > C.MAX_ASK) return "price > " + C.MAX_ASK.toFixed(2);
    if (x.volume === 0 && !x.has_book) return "no volume and no orderbook";
    return null;
  };

  C.evaluate = function (rows, res, coef) {
    const lock = C.lockOf(res);
    const parsed = rows.map(r => ({ r, pp: C.parsePrice(r.price) }));
    const byPair = {};
    parsed.forEach(x => { if (x.r.pair_id && x.pp) (byPair[x.r.pair_id] = byPair[x.r.pair_id] || []).push(x); });
    const out = parsed.map(x => {
      const r = x.r, q = C.modelProb(r, res);
      if (!x.pp || q === null) return { ...r, valid: false, q };
      const p = x.pp.p;
      let opp = null;
      const mates = r.pair_id ? (byPair[r.pair_id] || []).filter(y => y !== x) : [];
      if (mates.length === 1) opp = mates[0].pp.p;
      else if (r.opp) { const o = C.parsePrice(r.opp); if (o) opp = o.p; }
      const fair = opp !== null ? p / (p + opp) : p;
      const useFee = r.fee !== undefined ? r.fee : !x.pp.american;
      const cost = p + (useFee ? C.fee(p, coef) : 0);
      const ev = q / cost - 1;
      return { ...r, valid: true, implied: p, fair, overround: opp !== null ? p + opp - 1 : null, q, edge: 100 * (q - fair),
               cost, ev, american: x.pp.american, feeApplied: useFee, cashesOnLock: C.outcomeFn(r)(lock.a, lock.b) };
    });
    // sanity guard: pinned / dead lines can't win (same rule as scanner/edge.py)
    out.forEach(x => { if (x.valid) x.skip = C.sanitySkip(x); });
    const plus = out.filter(x => x.valid && !x.skip && x.ev > 0);
    // BEST BET = highest-EV line, any market, any side. The lock is the headline prediction; cashesOnLock is info only.
    const best = plus.length ? plus.reduce((m, x) => x.ev > m.ev ? x : m) : null;
    const bestAny = best;
    return { rows: out, best, bestAny, lock };
  };

  C.label = function (mk, A, B) {
    const t = mk.side === "A" ? A : mk.side === "B" ? B : "";
    let s;
    switch (mk.kind) {
      case "ml": s = `Series ML · ${t}`; break;
      case "hcp": s = `Handicap · ${t} ${mk.line > 0 ? "+" : ""}${mk.line.toFixed(1)} maps`; break;
      case "total": s = `Total maps · ${mk.side === "over" ? "Over" : "Under"} ${mk.line}`; break;
      case "game": s = `Game ${mk.game} winner · ${t}`; break;
      case "exact": s = `Exact score · ${mk.line[0] > mk.line[1] ? A + " " + mk.line[0] + "-" + mk.line[1] : B + " " + mk.line[1] + "-" + mk.line[0]}`; break;
      default: s = mk.kind;
    }
    return (mk.neg ? "NOT " : "") + s;
  };

  if (typeof module !== "undefined" && module.exports) module.exports = C; else root.SimCore = C;
})(typeof self !== "undefined" ? self : this);
