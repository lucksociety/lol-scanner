/* Luck Society · Simulate panel: ratings-based in-browser LoL series sim + Jupiter / any-price edge finder.
   Static (GitHub Pages): reads data/sim_data.json, runs the sim in a Web Worker, writes nothing anywhere. */
(function () {
  const C = window.SimCore, $ = s => document.querySelector(s), E = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = x => (x === null || x === undefined || isNaN(x)) ? "–" : (100 * x).toFixed(1) + "%";
  let D = null, worker = null, lastRes = null, rows = [], ctx = { match: null };
  const qs = new URLSearchParams(location.search);

  function teamBy(key) { return D.teams.find(t => t.name === key) || null; }
  function teamByCode(code, nameHint) {
    const c = D.teams.filter(t => t.code === code);
    if (!c.length) return null;
    return c.find(t => t.name === nameHint) || c.sort((a, b) => b.maps - a.maps)[0];
  }
  function A() { return teamBy($("#ta").value); }
  function B() { return teamBy($("#tb").value); }
  function code(t) { return t ? t.code : "?"; }

  function fillTeams() {
    const opts = D.teams.map(t => `<option value="${E(t.name)}">${E(t.code)} · ${E(t.name)} · ${E(t.league || "–")} · Elo ${t.elo_v2.toFixed(0)}</option>`).join("");
    $("#ta").innerHTML = opts; $("#tb").innerHTML = opts;
    const slate = [];
    Object.keys(D.slate).sort().reverse().forEach(d => D.slate[d].forEach(m => slate.push({ d, m })));
    $("#preset").innerHTML = '<option value="">— pick a slate series to preload —</option>' +
      slate.map(({ d, m }) => `<option value="${E(m.match_id)}">${E(d)} · ${E(m.a)} vs ${E(m.b)} · Bo${m.bo} · ${E(m.start_et)}</option>`).join("");
  }

  function findMatch(id) {
    for (const d in D.slate) for (const m of D.slate[d]) if (m.match_id === id) return { d, m };
    return null;
  }

  function loadMatch(id) {
    const f = findMatch(id); if (!f) return false;
    const m = f.m; ctx.match = m;
    const ta = teamByCode(m.a, m.a_name), tb = teamByCode(m.b, m.b_name);
    if (ta) $("#ta").value = ta.name; if (tb) $("#tb").value = tb.name;
    $("#bo").value = String(m.bo); $("#fearless").checked = !!m.fearless;
    $("#g1").value = m.g1_a_blue === true ? "A" : m.g1_a_blue === false ? "B" : "rand";
    $("#preset").value = id; $("#ovr").value = ""; $("#ovrg").value = "";
    onTeams(); prefillOdds(); return true;
  }

  function baseP() {
    const a = A(), b = B(); if (!a || !b) return 0.5;
    return C.eloP(a.elo_v2, b.elo_v2, D.params.elo_scale);
  }

  function onTeams() {
    const a = A(), b = B();
    if (ctx.match && (!a || !b || a.code !== ctx.match.a || b.code !== ctx.match.b)) { ctx.match = null; $("#preset").value = ""; }
    $("#base").innerHTML = a && b ? `Ratings map win (neutral side): <b>${E(a.code)} ${pct(baseP())}</b> · Elo v2 ${a.elo_v2.toFixed(0)} vs ${b.elo_v2.toFixed(0)} (patch ${E(D.patch || "?")}, patch-recency weighted)` : "";
    $("#sa").textContent = a ? a.code : "A";
    document.querySelectorAll(".lblA").forEach(e => e.textContent = code(a));
    document.querySelectorAll(".lblB").forEach(e => e.textContent = code(b));
    rosterBox(); frozenBox();
  }

  function rosterBox() {
    const a = A(), b = B(), m = ctx.match;
    const one = (t, lu) => {
      if (lu && lu.length) return `<div>${E(t.code)} expected five: ${lu.map(x => `<span class="f ${x.status === "CONFIRMED" ? "w" : x.status === "UNCERTAIN" ? "l" : "p"}">${E(x.role.slice(0, 3))} ${E(x.name)}</span>`).join("")}</div>`;
      const r = (t.roster || []); return `<div>${E(t.code)} official roster: ${r.map(x => `<span class="f">${E((x.role || "").slice(0, 3))} ${E(x.name)}</span>`).join("") || "–"}</div>`;
    };
    $("#roster").innerHTML = a && b ? one(a, m && m.lineups[0]) + one(b, m && m.lineups[1]) : "";
  }

  function frozenBox() {
    const m = ctx.match;
    if (!m) { $("#frozen").innerHTML = '<span class="muted">No frozen protocol forecast for this pairing (custom matchup).</span>'; return; }
    const f = m.frozen, [la, lb] = f.locked_score.split("-").map(Number);
    $("#frozen").innerHTML = `<b>FROZEN PROTOCOL LOCK</b> (${E(f.model_version)}, frozen ${E(f.frozen_at_et)}): <b>${E(la > lb ? m.a : m.b)} ${Math.max(la, lb)}-${Math.min(la, lb)}</b> · ${E(m.a)} series ${pct(f.p_series_a)} · G1 map ${pct(f.p_map_a)} (draft-path sim). ` +
      `<button class="ghost" id="usefz">use frozen G1 map % as override</button>`;
    $("#usefz").onclick = () => { $("#ovr").value = (100 * f.p_map_a).toFixed(1); };
  }

  // ---------------------------------------------------------------- markets
  function stdMarkets(bo) {
    const out = [{ kind: "ml", side: "A" }, { kind: "ml", side: "B" }];
    const need = (bo >> 1) + 1;
    if (bo >= 3) {
      const lines = bo === 3 ? [1.5] : [1.5, 2.5];
      lines.forEach(L => { out.push({ kind: "hcp", side: "A", line: -L }, { kind: "hcp", side: "B", line: +L }, { kind: "hcp", side: "B", line: -L }, { kind: "hcp", side: "A", line: +L }); });
      for (let t = need + 0.5; t < bo; t++) out.push({ kind: "total", side: "over", line: t }, { kind: "total", side: "under", line: t });
    }
    for (let g = 1; g <= bo; g++) out.push({ kind: "game", side: "A", game: g }, { kind: "game", side: "B", game: g });
    for (let y = 0; y < need; y++) { out.push({ kind: "exact", side: "A", line: [need, y] }, { kind: "exact", side: "A", line: [y, need] }); }
    return out;
  }
  const keyOf = r => [r.kind, r.side, JSON.stringify(r.line ?? null), r.game ?? "", !!r.neg].join("|");

  function prefillOdds() {
    const bo = +$("#bo").value, a = A(), b = B();
    rows = [];
    let src = null;
    if (ctx.match && D.odds[ctx.match.match_id]) src = { o: D.odds[ctx.match.match_id], flip: false };
    else if (a && b) for (const id in D.odds) { const o = D.odds[id]; if (o.a === a.code && o.b === b.code) src = { o, flip: false }; else if (o.a === b.code && o.b === a.code) src = { o, flip: true }; }
    const flip = s => s === "A" ? "B" : s === "B" ? "A" : s;
    const seen = new Set();
    if (src) {
      src.o.legs.forEach(l => {
        const r = { kind: l.kind, side: src.flip ? flip(l.side) : l.side, line: l.line, game: l.game, neg: l.neg, price: String(l.ask), pair_id: l.pair_id, src: "Jupiter", fee: true, vol: l.volume };
        if (r.kind === "exact" && src.flip && r.line) r.line = [r.line[1], r.line[0]];
        rows.push(r); seen.add(keyOf(r));
      });
      $("#oddsnote").innerHTML = `Prefilled ${src.o.legs.length} Jupiter legs from the POST-FORECAST snapshot <b>${E(src.o.fetched_at_et)}</b> · <a href="${E(src.o.url)}" target="_blank" rel="noopener">${E(src.o.title)}</a>` +
        (src.o.not_modeled && src.o.not_modeled.length ? ` · ${src.o.not_modeled.length} Jupiter props not modeled (objective/kill props; the sim models map winners only)` : "") + ". Edit any price; blank rows are ignored.";
    } else $("#oddsnote").textContent = "No Jupiter snapshot for this matchup. Type any price: 0.62, 62%, 62c, or American odds (-150 / +130).";
    stdMarkets(bo).forEach(m => { const r = { ...m, neg: false, price: "", src: "manual", pair_id: "std:" + [m.kind, m.kind === "ml" ? "" : m.kind === "game" ? m.game : m.kind === "total" ? m.line : m.kind === "hcp" ? Math.abs(m.line) + (m.side === (m.line < 0 ? "A" : "B") ? "A" : "B") : JSON.stringify(m.line)].join(":") }; if (r.kind === "exact") r.pair_id = ""; if (!seen.has(keyOf(r))) rows.push(r); });
    drawMarkets();
  }

  function drawMarkets() {
    const a = code(A()), b = code(B());
    const ev = lastRes ? C.evaluate(rows, lastRes, D.params.fee_coef) : null;
    const fl = ctx.match && A() && A().code === ctx.match.a ? ctx.match.frozen.locked_score.split("-").map(Number) : null;
    const tr = rows.map((r, i) => {
      const e = ev ? ev.rows[i] : null;
      const best = ev && ev.best && ev.rows[i] === ev.best;
      const cls = e && e.valid ? (e.ev > 0 ? "pos" : "neg") + (best ? " best" : "") : "";
      const lk = e && e.valid ? { true: "✓", false: "✗", null: "–" }[String(e.cashesOnLock)] : "";
      const fz = fl ? { true: "✓", false: "✗", null: "–" }[String(C.outcomeFn(r)(fl[0], fl[1]))] : null;
      return `<tr class="${cls}"><td>${E(C.label(r, a, b))}${best ? ' <span class="bb">BEST BET</span>' : ""}<small class="src">${E(r.src)}</small></td>
        <td><input class="px" data-i="${i}" value="${E(r.price || "")}" placeholder="price" inputmode="decimal"></td>
        <td><input class="opp" data-i="${i}" value="${E(r.opp || "")}" placeholder="${r.pair_id && !r.pair_id.startsWith("std") ? "paired" : "opp (opt)"}" ${r.pair_id && !r.pair_id.startsWith("std") ? "disabled" : ""}></td>
        <td><input type="checkbox" class="fee" data-i="${i}" ${(e && e.valid ? e.feeApplied : r.fee !== false) ? "checked" : ""}></td>
        <td>${e && e.valid ? pct(e.implied) : ""}</td><td>${e && e.valid ? pct(e.fair) : ""}</td><td>${e && e.q !== null && e.q !== undefined ? pct(e.q) : ""}</td>
        <td>${e && e.valid ? (e.edge >= 0 ? "+" : "") + e.edge.toFixed(1) : ""}</td><td>${e && e.valid ? (e.ev >= 0 ? "+" : "") + e.ev.toFixed(3) : ""}</td><td class="lk">${lk}</td>${fl ? `<td class="lk">${fz}</td>` : ""}</tr>`;
    }).join("");
    $("#mk").innerHTML = `<tr><th>Market · side</th><th>Price</th><th>Opp. side</th><th>Fee</th><th>Implied</th><th>No-vig</th><th>Model</th><th>Edge pp</th><th>EV/$1</th><th>Cashes on sim lock</th>${fl ? "<th>Cashes on frozen protocol lock</th>" : ""}</tr>` + tr;
    document.querySelectorAll("#mk input.px").forEach(inp => inp.onchange = () => { rows[+inp.dataset.i].price = inp.value; delete rows[+inp.dataset.i].fee; setTimeout(drawMarkets, 0); });
    document.querySelectorAll("#mk input.opp").forEach(inp => inp.onchange = () => { rows[+inp.dataset.i].opp = inp.value; setTimeout(drawMarkets, 0); });
    document.querySelectorAll("#mk input.fee").forEach(inp => inp.onchange = () => { rows[+inp.dataset.i].fee = inp.checked; setTimeout(drawMarkets, 0); });
    if (!ev) { $("#verdict").innerHTML = '<div class="verdict">Run SIMULATE to price these markets.</div>'; return; }
    const L = ev.lock, lockTxt = `${L.a > L.b ? a : b} ${Math.max(L.a, L.b)}-${Math.min(L.a, L.b)}`;
    if (ev.best) {
      const x = ev.best;
      $("#verdict").innerHTML = `<div class="verdict bet">BEST BET · ${E(C.label(x, a, b))} @ ${E(x.price)} · model ${pct(x.q)} vs no-vig ${pct(x.fair)} · edge ${x.edge >= 0 ? "+" : ""}${x.edge.toFixed(1)} pp · EV ${x.ev >= 0 ? "+" : ""}${x.ev.toFixed(3)}/$1 · cashes on sim lock ${E(lockTxt)} ✓</div>`;
    } else {
      const n = ev.rows.filter(r => r.valid).length;
      $("#verdict").innerHTML = `<div class="verdict pass">PASS · ${n ? "no priced market that cashes on the sim lock " + E(lockTxt) + " has +EV" : "enter at least one price"}</div>` +
        (ev.bestAny ? `<p class="secondary">Ignores the lock rule (not a recommendation): ${E(C.label(ev.bestAny, a, b))} @ ${E(ev.bestAny.price)} · EV ${ev.bestAny.ev.toFixed(3)}/$1 · dies on lock</p>` : "");
    }
  }

  function addCustom() {
    const kind = $("#ck").value, side = $("#cs").value, line = parseFloat($("#cl").value), game = parseInt($("#cg").value, 10);
    const r = { kind, side: kind === "total" ? (side === "A" ? "over" : "under") : side, neg: false, price: $("#cp").value, opp: $("#co").value, src: "custom", pair_id: "" };
    if (kind === "hcp" || kind === "total") { if (isNaN(line)) return alert("Enter a line, e.g. -1.5"); r.line = line; }
    if (kind === "game") { if (!(game >= 1 && game <= +$("#bo").value)) return alert("Game number out of range"); r.game = game; }
    if (kind === "exact") { const m = $("#cl").value.match(/(\d)\s*[-:]\s*(\d)/); if (!m) return alert("Exact score as A-B, e.g. 2-1"); r.line = [+m[1], +m[2]]; r.side = "A"; }
    rows.unshift(r); drawMarkets();
  }

  // ---------------------------------------------------------------- sim
  function simulate() {
    const a = A(), b = B(); if (!a || !b || a.name === b.name) return alert("Pick two different teams");
    const bo = +$("#bo").value, p0 = baseP();
    const ov = parseFloat($("#ovr").value);
    const pg = ($("#ovrg").value || "").split(",").map(s => parseFloat(s));
    const base = [];
    for (let g = 0; g < bo; g++) {
      let p = !isNaN(pg[g]) ? pg[g] : !isNaN(ov) ? ov : 100 * p0;
      p = Math.min(Math.max(p / 100, 0.001), 0.999); base.push(C.logit(p));
    }
    const n = Math.max(1000, Math.min(2000000, parseInt($("#n").value, 10) || 100000));
    const P = D.params;
    const msg = { bo, base, n, seed: parseInt($("#seed").value, 10) || 20261028, fearless: $("#fearless").checked && bo > 1, g1: $("#g1").value,
      useSide: $("#side").checked, sideBlue: P.side_blue_logit, qLoserBlue: P.q_loser_blue, uncertainty: $("#unc").checked,
      ratingSd: P.rating_sd, mapSd: P.map_sd, fearlessExtra: +$("#fx").value || 0 };
    if (worker) worker.terminate();
    worker = new Worker("assets/sim-worker.js");
    $("#go").disabled = true; $("#prog").style.width = "0%"; $("#status").textContent = `Simulating ${n.toLocaleString()} series…`;
    const t0 = performance.now();
    worker.onmessage = ev => {
      const d = ev.data;
      if (d.type === "progress") { $("#prog").style.width = (100 * d.done / d.n).toFixed(0) + "%"; return; }
      lastRes = d; $("#go").disabled = false; $("#prog").style.width = "100%";
      $("#status").textContent = `${n.toLocaleString()} sims in ${((performance.now() - t0) / 1000).toFixed(2)} s · seed ${msg.seed}`;
      drawResults(msg); drawMarkets();
    };
    worker.onerror = e => { $("#go").disabled = false; $("#status").textContent = "Sim failed: " + e.message; };
    worker.postMessage(msg);
  }

  function drawResults(msg) {
    const r = lastRes, a = code(A()), b = code(B()), bo = r.bo, need = (bo >> 1) + 1;
    const L = C.lockOf(r);
    const pa = Object.entries(r.exact).reduce((s, [k, v]) => s + (+k.split("-")[0] > +k.split("-")[1] ? v : 0), 0) / r.n;
    const se = Math.sqrt(pa * (1 - pa) / r.n);
    const em = r.maps.reduce((s, v, i) => s + i * v, 0) / r.n;
    const keys = []; for (let y = 0; y < need; y++) { keys.push(need + "-" + y); keys.unshift(y + "-" + need); }
    keys.sort((x, y) => (+y.split("-")[0] - +y.split("-")[1]) - (+x.split("-")[0] - +x.split("-")[1]));
    const dist = keys.map(k => { const [x, y] = k.split("-").map(Number), v = (r.exact[k] || 0) / r.n;
      return `<div class="sc${k === L.key ? " lk" : ""}"><b>${E(x > y ? a : b)} ${Math.max(x, y)}-${Math.min(x, y)}</b><span>${pct(v)}</span><i style="width:${Math.min(100, v * 220).toFixed(0)}%"></i></div>`; }).join("");
    const der = [];
    if (bo >= 3) {
      const cov = (side, L2) => C.modelProb({ kind: "hcp", side, line: L2 }, r);
      [1.5, 2.5].filter(x => x < bo - 0.5 || (bo === 5 && x === 2.5)).forEach(x => {
        if (bo === 3 && x === 2.5) return;
        der.push(`Handicap ±${x}: ${a} −${x} ${pct(cov("A", -x))} · ${b} +${x} ${pct(cov("B", +x))} · ${b} −${x} ${pct(cov("B", -x))} · ${a} +${x} ${pct(cov("A", +x))}`);
      });
      for (let t = need + 0.5; t < bo; t++) { const o = C.modelProb({ kind: "total", side: "over", line: t }, r); der.push(`Maps O/U ${t}: Over ${pct(o)} · Under ${pct(1 - o)}`); }
    }
    const games = r.reach.map((c, g) => c ? `G${g + 1}: reached ${pct(c / r.n)} · ${a} ${pct(r.gwin[g] / c)} if played` : `G${g + 1}: never reached`).join(" · ");
    $("#res").innerHTML = `<div class="lock">RATINGS-BASED SIM LOCK (in-browser, not the protocol lock): <b>${E(L.a > L.b ? a : b)} ${Math.max(L.a, L.b)}-${Math.min(L.a, L.b)}</b> (${pct(L.p)}) · ${E(a)} series ${pct(pa)} ± ${(196 * se).toFixed(2)} pp (95% MC) · ${E(b)} ${pct(1 - pa)} · E[maps] ${em.toFixed(2)}</div>
      <div class="dist">${dist}</div><div class="derived">${der.map(E).join("<br>")}<br>${E(games)}</div>
      <p class="muted">Inputs: Bo${bo} · Fearless ${msg.fearless ? "on (+" + msg.fearlessExtra + " map sd per later game)" : "off"} · G1 ${msg.g1 === "rand" ? "side 50/50" : (msg.g1 === "A" ? a : b) + " blue"} · side edge ${msg.useSide ? "on (" + D.params.side_blue_logit.toFixed(3) + " logit)" : "off"} · rating/map uncertainty ${msg.uncertainty ? "on (sd " + D.params.rating_sd + " / " + D.params.map_sd + ")" : "off"} · base map logits ${msg.base.map(x => x.toFixed(2)).join(", ")}</p>`;
  }

  // ---------------------------------------------------------------- boot
  fetch("data/sim_data.json", { cache: "no-cache" }).then(r => r.json()).then(d => {
    D = d; fillTeams();
    $("#gen").textContent = `ratings export ${new Date(d.generated_at_utc).toLocaleString("en-US", { timeZone: "America/New_York", dateStyle: "medium", timeStyle: "short" })} ET · patch ${d.patch} · ${d.teams.length} teams`;
    $("#fx").value = d.params.fearless_extra_sd;
    ["#ta", "#tb"].forEach(s => $(s).onchange = () => { onTeams(); prefillOdds(); });
    $("#bo").onchange = () => { prefillOdds(); };
    $("#preset").onchange = () => { if ($("#preset").value) loadMatch($("#preset").value); };
    $("#swap").onclick = () => { const x = $("#ta").value; $("#ta").value = $("#tb").value; $("#tb").value = x; ctx.match = null; onTeams(); prefillOdds(); lastRes = null; $("#res").innerHTML = ""; };
    $("#go").onclick = simulate; $("#add").onclick = addCustom;
    $("#ck").onchange = () => { const k = $("#ck").value; $("#cl").placeholder = k === "exact" ? "A-B e.g. 2-1" : k === "hcp" ? "-1.5" : "2.5"; };
    let ok = false;
    if (qs.get("match")) ok = loadMatch(qs.get("match"));
    if (!ok) {
      const ta = teamByCode(qs.get("a") || d.teams[0].code), tb = teamByCode(qs.get("b") || d.teams[1].code);
      if (ta) $("#ta").value = ta.name; if (tb) $("#tb").value = tb.name;
      if (qs.get("bo")) $("#bo").value = qs.get("bo");
      if (qs.get("fearless")) $("#fearless").checked = qs.get("fearless") === "1";
      onTeams(); prefillOdds();
    }
    if (qs.get("run") === "1") simulate();
  }).catch(e => { $("#status").textContent = "Could not load data/sim_data.json: " + e; });
})();
