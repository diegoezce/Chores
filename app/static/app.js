const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const FRASES = ["¡Genia! 🌟", "¡Muy bien! 👏", "¡Eso es! 💪", "¡Qué crack! 🚀", "¡Bravo! 🎉", "¡Sos una campeona! 🏆", "¡Una menos! ✨", "¡Increíble! 🌈", "¡Así se hace! 🔥"];
const FRASES_DIA = ["¡Terminaste todo el día!", "¡Día completo, qué orgullo!", "¡Misión cumplida!"];
const pick = (a) => a[Math.floor(Math.random() * a.length)];

const st = { kid: null, fecha: null, data: null, busy: new Set(), shownPts: 0 };

function fechaLinda(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} de ${["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"][m - 1]}`;
}
function sumarDias(iso, n) {
  const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}
function restaDias(a, b) { return Math.round((new Date(a + "T12:00:00") - new Date(b + "T12:00:00")) / 864e5); }

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  let body = null;
  try { body = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((body && body.detail) || "Algo salió mal. Probá de nuevo.");
  return body;
}

let toastT;
function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toastT); toastT = setTimeout(() => (t.className = "toast"), err ? 3600 : 1800);
}
function floatText(x, y, txt, neg) {
  const e = document.createElement("div"); e.className = "float" + (neg ? " neg" : ""); e.textContent = txt;
  e.style.left = x - 20 + "px"; e.style.top = y - 20 + "px"; document.body.appendChild(e); setTimeout(() => e.remove(), 1000);
}

/* ---------------- Home ---------------- */
async function home() {
  st.kid = null;
  try { localStorage.removeItem("kid"); } catch (e) {}
  const { chicas } = await api("/api/chicas");
  $("#app").innerHTML = `
    <div class="home-hero"><h1>Tareas de la casa</h1><p>¿Quién sos? Tocá tu nombre 👇</p></div>
    <div class="kid-cards">${chicas.map((c) => `
      <button class="kid-card" style="--c:${c.color};--bg:${c.fondo}" data-kid="${c.id}">
        <span class="emo">${c.emoji}</span>
        <span><span class="nm">${esc(c.nombre)}</span><br><span class="sub">${c.racha.actual > 0 ? "🔥 " + c.racha.actual + (c.racha.actual === 1 ? " día" : " días") + " seguidos" : "¡Empezá tu racha hoy!"}</span></span>
        <span class="pts">${c.puntos}<small>puntos</small></span>
      </button>`).join("")}</div>
    <p class="foot">Entre todas mantenemos la casa en orden 💛<br><a href="/padres">Padres</a></p>`;
  document.querySelectorAll("[data-kid]").forEach((b) => (b.onclick = () => { location.hash = "#/" + b.dataset.kid; }));
}

/* ---------------- Pantalla de una chica ---------------- */
async function kidView(kid, fecha) {
  st.kid = kid; st.fecha = fecha;
  try { localStorage.setItem("kid", kid); } catch (e) {}
  const q = fecha ? `?fecha=${fecha}` : "";
  let d;
  try { d = await api(`/api/chicas/${kid}${q}`); } catch (e) { toast(e.message, true); location.hash = "#/" + kid; return; }
  st.data = d;
  render();
}

function render() {
  const d = st.data, dia = d.dia;
  const root = document.documentElement.style;
  root.setProperty("--c", d.color); root.setProperty("--bg", d.fondo);
  const sel = dia.fecha;
  const atras = restaDias(d.hoy, sel);
  const pct = dia.total_tareas ? Math.round((dia.hechas / dia.total_tareas) * 100) : 0;
  const circ = 2 * Math.PI * 34;
  const week = d.semana.map((w) => {
    const cls = w.futuro ? "future" : !w.hay_tareas ? "free" : w.completo ? "done" : w.avance > 0 ? "part" : "";
    const inner = w.completo ? "✓" : !w.hay_tareas ? "·" : "";
    return `<div class="wd ${cls} ${w.fecha === sel ? "sel" : ""}">${w.dia_corto}<button data-f="${w.fecha}" ${w.futuro ? "disabled" : ""} style="--p:${Math.round(w.avance * 100)}">${inner}</button></div>`;
  }).join("");

  let body;
  if (!dia.total_tareas) {
    body = `<div class="free-day"><div class="e">🏡</div><p>${dia.dia_nombre}: día libre.<br>¡Hoy hacemos todo entre todos!</p></div>`;
  } else {
    body = `<div class="tasks">${dia.tareas.map((t) => `
      <button class="task ${t.hecha ? "done" : ""}" data-t="${t.id}">
        <span class="box">${t.hecha ? "✓" : ""}</span><span class="tx">${esc(t.texto)}</span><span class="pt">+${d.puntos_tarea}</span>
      </button>`).join("")}</div>
      <div class="bonus-note ${dia.completo ? "got" : ""}">${dia.completo ? `🎉 ¡Día completo! Ganaste +${d.puntos_bonus} de bonus` : `Completá todas y ganá +${d.puntos_bonus} de bonus ⭐`}</div>`;
  }

  $("#app").innerHTML = `
    <div class="kid-top">
      <div class="bar">
        <button class="icon-btn" id="back" aria-label="Volver">‹</button>
        <div class="who"><span>${d.emoji}</span><span>${esc(d.nombre)}</span></div>
        <button class="icon-btn" id="snd" aria-label="Sonido">${Sound.muted ? "🔇" : "🔊"}</button>
      </div>
      <div class="score">
        <div>
          <div class="big-pts"><span id="pts">${st.shownPts}</span><small>puntos</small></div>
          <div class="chips">
            <span class="chip">🔥 Racha: ${d.racha.actual}</span>
            <span class="chip">🏅 Mejor: ${d.racha.mejor}</span>
            <span class="chip">📅 Semana: ${d.puntos_semana}</span>
          </div>
        </div>
        <div class="ring">
          <svg width="84" height="84"><circle cx="42" cy="42" r="34" fill="none" stroke="rgba(255,255,255,.28)" stroke-width="9"/>
          <circle cx="42" cy="42" r="34" fill="none" stroke="#fff" stroke-width="9" stroke-linecap="round" stroke-dasharray="${circ}" stroke-dashoffset="${circ * (1 - pct / 100)}" style="transition:stroke-dashoffset .6s"/></svg>
          <div class="t">${dia.total_tareas ? dia.hechas + "/" + dia.total_tareas : "🏡"}</div>
        </div>
      </div>
    </div>
    <div class="week">${week}</div>
    <div class="daynav">
      <button class="nav-btn" id="prev" ${atras >= d.max_dias_atras ? "disabled" : ""} aria-label="Día anterior">‹</button>
      <h2>${dia.es_hoy ? "Hoy" : dia.dia_nombre}<small>${dia.es_hoy ? dia.dia_nombre + " " : ""}${fechaLinda(dia.fecha)}</small></h2>
      <button class="nav-btn" id="next" ${dia.es_hoy ? "disabled" : ""} aria-label="Día siguiente">›</button>
    </div>
    ${dia.es_hoy ? "" : `<button class="back-today" id="today">Volver a hoy</button>`}
    ${body}`;

  animarPuntos(st.shownPts, d.puntos);
  st.shownPts = d.puntos;
  $("#back").onclick = () => (location.hash = "#/");
  $("#snd").onclick = (e) => { e.currentTarget.textContent = Sound.toggle() ? "🔇" : "🔊"; };
  $("#prev").onclick = () => (location.hash = `#/${st.kid}/${sumarDias(sel, -1)}`);
  $("#next").onclick = () => { const n = sumarDias(sel, 1); location.hash = n >= d.hoy ? `#/${st.kid}` : `#/${st.kid}/${n}`; };
  const t = $("#today"); if (t) t.onclick = () => (location.hash = `#/${st.kid}`);
  document.querySelectorAll(".wd button[data-f]").forEach((b) => (b.onclick = () => { location.hash = b.dataset.f >= d.hoy ? `#/${st.kid}` : `#/${st.kid}/${b.dataset.f}`; }));
  document.querySelectorAll(".task").forEach((b) => (b.onclick = (ev) => alternar(b, ev)));
}

function animarPuntos(desde, hasta) {
  const el = $("#pts"); if (!el || desde === hasta) return;
  const t0 = performance.now(), dur = 600;
  el.parentElement.classList.remove("bump"); void el.parentElement.offsetWidth; el.parentElement.classList.add("bump");
  (function paso(t) {
    const k = Math.min((t - t0) / dur, 1);
    el.textContent = Math.round(desde + (hasta - desde) * k);
    if (k < 1) requestAnimationFrame(paso);
  })(t0);
}

async function alternar(btn, ev) {
  const id = btn.dataset.t;
  if (st.busy.has(id)) return;
  const tarea = st.data.dia.tareas.find((t) => t.id === id);
  const quiere = !tarea.hecha;
  st.busy.add(id); btn.disabled = true;
  const x = ev.clientX || innerWidth / 2, y = ev.clientY || innerHeight / 2;
  try {
    const r = await api(`/api/chicas/${st.kid}/tareas/${id}`, { method: "PUT", body: JSON.stringify({ hecha: quiere, fecha: st.data.dia.fecha }) });
    if (st.kid !== st.data.id) return;
    st.data = { ...st.data, ...r.resumen, dia: r.dia };
    // refrescar la semana (para los círculos) sin parpadeo
    const fresh = await api(`/api/chicas/${st.kid}?fecha=${r.dia.fecha}`);
    st.data = fresh;
    render();
    if (r.cambio && quiere) {
      floatText(x, y, `+${st.data.puntos_tarea}`);
      if (r.bonus_ganado) {
        Sound.play("big"); FX.big();
        const b = document.createElement("div"); b.className = "banner";
        b.innerHTML = `<div><b>🎉 ${pick(FRASES_DIA)}</b><span>+${st.data.puntos_bonus} de bonus ⭐</span></div>`;
        document.body.appendChild(b); setTimeout(() => b.remove(), 2700);
      } else { Sound.play("ok"); FX.small(x, y); toast(pick(FRASES)); }
    } else if (r.cambio) {
      floatText(x, y, `−${st.data.puntos_tarea}`, true); Sound.play("undo");
    }
  } catch (e) {
    toast(e.message, true);
    render();
  } finally { st.busy.delete(id); }
}

/* ---------------- Router ---------------- */
async function route() {
  const [, kid, fecha] = location.hash.split("/");
  if (!kid) {
    let last = null; try { last = localStorage.getItem("kid"); } catch (e) {}
    if (last && !st.skipAuto) { st.skipAuto = true; location.hash = "#/" + last; return; }
    st.skipAuto = true; return home();
  }
  const d = fecha && /^\d{4}-\d{2}-\d{2}$/.test(fecha) ? fecha : null;
  if (st.kid !== kid) st.shownPts = 0;
  return kidView(kid, d);
}
addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => { if (!document.hidden) route(); });
route();
