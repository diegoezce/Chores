// Fuegos artificiales y confeti en canvas, sin librerías.
(function () {
  const canvas = document.createElement("canvas");
  canvas.id = "fx";
  Object.assign(canvas.style, { position: "fixed", inset: 0, width: "100%", height: "100%", pointerEvents: "none", zIndex: 50 });
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(canvas));
  const ctx = canvas.getContext("2d");
  let parts = [], running = false, dpr = 1;
  const COLORS = ["#ff4d8d", "#ffb703", "#3ddc97", "#4cc9f0", "#9b5de5", "#ff6b35", "#ffe66d"];

  function resize() {
    dpr = window.devicePixelRatio || 1;
    canvas.width = innerWidth * dpr; canvas.height = innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  addEventListener("resize", resize); resize();

  function burst(x, y, n = 60, power = 1) {
    const base = COLORS[Math.floor(Math.random() * COLORS.length)];
    for (let i = 0; i < n; i++) {
      const a = Math.random() * Math.PI * 2, v = (2 + Math.random() * 5) * power;
      parts.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, life: 1, decay: .012 + Math.random() * .014,
        size: 2 + Math.random() * 3, color: Math.random() < .7 ? base : COLORS[Math.floor(Math.random() * COLORS.length)], g: .07 });
    }
    start();
  }
  function confetti(n = 120) {
    for (let i = 0; i < n; i++) {
      parts.push({ x: Math.random() * innerWidth, y: -10 - Math.random() * 200, vx: (Math.random() - .5) * 3, vy: 2 + Math.random() * 3,
        life: 1, decay: .004, size: 5 + Math.random() * 5, color: COLORS[Math.floor(Math.random() * COLORS.length)], g: .03,
        rect: true, rot: Math.random() * 6, vr: (Math.random() - .5) * .3 });
    }
    start();
  }
  function start() { if (!running) { running = true; requestAnimationFrame(tick); } }
  function tick() {
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    parts = parts.filter(p => p.life > 0 && p.y < innerHeight + 30);
    for (const p of parts) {
      p.x += p.vx; p.y += p.vy; p.vy += p.g; p.vx *= .99; p.life -= p.decay;
      ctx.globalAlpha = Math.max(p.life, 0); ctx.fillStyle = p.color;
      if (p.rect) { p.rot += p.vr; ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2); ctx.restore(); }
      else { ctx.beginPath(); ctx.arc(p.x, p.y, p.size * Math.max(p.life, .3), 0, 7); ctx.fill(); }
    }
    ctx.globalAlpha = 1;
    if (parts.length) requestAnimationFrame(tick); else running = false;
  }
  window.FX = {
    small(x, y) { burst(x, y, 45, .8); },
    big() {
      const w = innerWidth, h = innerHeight;
      [[.25, .3], [.75, .3], [.5, .2], [.35, .5], [.65, .5], [.5, .4]].forEach(([fx, fy], i) =>
        setTimeout(() => burst(w * fx, h * fy, 90, 1.3), i * 260));
      confetti(140);
    },
  };

  // Sonidos con WebAudio (sin archivos)
  let ac, mute = false;
  try { mute = localStorage.getItem("mute") === "1"; } catch (e) {}
  function tone(f, t, d, type = "triangle", vol = .15) {
    const o = ac.createOscillator(), g = ac.createGain();
    o.type = type; o.frequency.value = f; o.connect(g); g.connect(ac.destination);
    g.gain.setValueAtTime(0, ac.currentTime + t);
    g.gain.linearRampToValueAtTime(vol, ac.currentTime + t + .02);
    g.gain.exponentialRampToValueAtTime(.001, ac.currentTime + t + d);
    o.start(ac.currentTime + t); o.stop(ac.currentTime + t + d + .05);
  }
  window.Sound = {
    get muted() { return mute; },
    toggle() { mute = !mute; try { localStorage.setItem("mute", mute ? "1" : "0"); } catch (e) {} return mute; },
    play(kind) {
      if (mute) return;
      try {
        ac = ac || new (window.AudioContext || window.webkitAudioContext)();
        if (kind === "ok") { tone(660, 0, .15); tone(880, .09, .22); }
        else if (kind === "big") { [523, 659, 784, 1047, 1319].forEach((f, i) => tone(f, i * .1, .35)); }
        else if (kind === "undo") { tone(330, 0, .15, "sine", .08); }
      } catch (e) {}
    },
  };
})();
