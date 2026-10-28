/* Luck Society · in-browser ratings-based LoL series sim (Web Worker). Pure function of the inputs; no network. */
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
self.onmessage = function (ev) {
  const o = ev.data, bo = o.bo, need = (bo >> 1) + 1, n = o.n | 0;
  const rnd = mulberry32(o.seed >>> 0);
  let spare = null;
  const gauss = () => { if (spare !== null) { const s = spare; spare = null; return s; }
    let u = 0, v = 0; while (u === 0) u = rnd(); v = rnd();
    const r = Math.sqrt(-2 * Math.log(u)), th = 2 * Math.PI * v; spare = r * Math.sin(th); return r * Math.cos(th); };
  const exact = {}, maps = new Array(bo + 1).fill(0), reach = new Array(bo).fill(0), gwin = new Array(bo).fill(0);
  const sdg = [];
  for (let g = 0; g < bo; g++) {
    const v = (o.uncertainty ? o.mapSd * o.mapSd : 0) + (o.fearless && g > 0 ? o.fearlessExtra * o.fearlessExtra * g : 0);
    sdg.push(Math.sqrt(v));
  }
  const chunk = 10000;
  for (let i = 0; i < n; i++) {
    const z = o.uncertainty ? o.ratingSd * gauss() : 0;
    let aBlue = o.g1 === "A" ? true : o.g1 === "B" ? false : rnd() < 0.5;
    let aw = 0, bw = 0;
    for (let g = 0; g < bo && aw < need && bw < need; g++) {
      const side = o.useSide ? (aBlue ? o.sideBlue : -o.sideBlue) : 0;
      const l = o.base[g] + z + (sdg[g] ? sdg[g] * gauss() : 0) + side;
      const win = rnd() < 1 / (1 + Math.exp(-l));
      reach[g]++; if (win) { gwin[g]++; aw++; } else bw++;
      const loserIsA = !win, loserBlue = rnd() < o.qLoserBlue;
      aBlue = loserIsA ? loserBlue : !loserBlue;
    }
    const k = aw + "-" + bw; exact[k] = (exact[k] || 0) + 1; maps[aw + bw]++;
    if ((i + 1) % chunk === 0) self.postMessage({ type: "progress", done: i + 1, n });
  }
  self.postMessage({ type: "done", n, bo, exact, maps, reach, gwin });
};
