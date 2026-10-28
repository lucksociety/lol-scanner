/* Luck Society · date selector (dashboard + simulator). Static links work without JS; this rebuilds the strip
   around TODAY in America/New_York, labels Yesterday / Today / Tomorrow, and wires the calendar input. */
(function () {
  const nav = document.getElementById("datenav"); if (!nav) return;
  const mode = nav.dataset.mode, isIndex = nav.dataset.index === "1";
  const qs = new URLSearchParams(location.search);
  const etToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const add = (d, n) => { const t = new Date(d + "T12:00:00Z"); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };
  const fmt = (d, o) => new Date(d + "T12:00:00Z").toLocaleDateString("en-US", { timeZone: "UTC", ...o });
  const today = etToday();
  const valid = d => /^\d{4}-\d{2}-\d{2}$/.test(d || "");
  let cur = mode === "sim" ? (valid(qs.get("date")) ? qs.get("date") : today) : nav.dataset.date;
  const href = d => mode === "sim" ? "simulate.html?date=" + d : d + ".html";
  const msg = t => { const m = document.getElementById("dmsg"); if (m) m.textContent = t || ""; };

  function draw(M) {
    const have = new Map((M.dates || []).map(x => [x.date, x]));
    const days = []; for (let i = -2; i <= 7; i++) days.push(add(today, i));
    if (!days.includes(cur)) { days.push(cur); days.sort(); }
    const rel = { "-1": "Yesterday", "0": "Today", "1": "Tomorrow" };
    const chips = days.map(d => {
      const off = Math.round((new Date(d + "T12:00:00Z") - new Date(today + "T12:00:00Z")) / 864e5);
      const x = have.get(d), n = x && x.n_series ? `<span class="n">${x.n_series} series</span>` : (x ? "no games" : "not scanned");
      const lab = rel[String(off)] || fmt(d, { weekday: "short" });
      return `<a class="dchip${d === cur ? " on" : ""}${x && x.n_series ? "" : " empty"}" data-d="${d}" href="${href(d)}" title="${fmt(d, { weekday: "long", month: "long", day: "numeric" })} (ET)"><b>${lab}</b><small>${fmt(d, { month: "short", day: "numeric" })} · ${n}</small></a>`;
    }).join("");
    const all = [...have.keys()].sort();
    nav.innerHTML = chips + `<label class="dpick">📅 Any date<input type="date" id="dpick" value="${cur}" min="${all[0] || cur}" max="${all[all.length - 1] || cur}"></label><span id="dmsg" role="status"></span>`;
    nav.querySelectorAll("a.dchip").forEach(a => a.onclick = e => {
      if (mode === "sim" && window.SimSetDate) { e.preventDefault(); go(a.dataset.d, have); }
      else if (!have.has(a.dataset.d)) { e.preventDefault(); msg(`No page for ${a.dataset.d} yet: the next scheduled scan builds it.`); }
    });
    document.getElementById("dpick").onchange = e => { const d = e.target.value; if (valid(d)) go(d, have); };
  }

  function go(d, have) {
    if (mode === "sim") {
      cur = d; history.replaceState(null, "", "simulate.html?date=" + d);
      nav.querySelectorAll("a.dchip").forEach(a => a.classList.toggle("on", a.dataset.d === d));
      const dl = document.getElementById("dashlink"); if (dl) dl.href = have.has(d) ? d + ".html" : "index.html";
      if (window.SimSetDate) window.SimSetDate(d);
      msg(have.has(d) ? "" : `${d}: no LoL series scanned for that date.`);
      return;
    }
    if (have.has(d)) location.href = href(d);
    else msg(`No LoL series scanned for ${d}. Dates with pages: ${[...have.keys()].sort().slice(-12).join(", ")}`);
  }

  fetch("data/dates.json", { cache: "no-cache" }).then(r => r.json()).then(M => {
    const have = new Set((M.dates || []).map(x => x.date));
    // index.html must always be TODAY (ET): if the build is from an earlier day, jump to today's page
    if (mode === "dash" && isIndex && cur !== today && have.has(today)) { location.replace(today + ".html"); return; }
    draw(M);
    if (mode === "sim") { const dl = document.getElementById("dashlink"); if (dl && have.has(cur)) dl.href = cur + ".html"; }
  }).catch(() => { /* keep the static links */ });
  window.DateNav = { today: () => today, current: () => cur };
})();
