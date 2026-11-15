// Jones (2010) Physarum agent model: agents sense a chemical trail ahead (left / centre / right),
// turn toward the strongest, move, and deposit. Food sources (entities) emit trail, so the colony
// grows a transport network between them, the same dynamics Physarum applies to graph edges.
(function () {
  const cv = document.getElementById("mold");
  if (!cv) return;
  const ctx = cv.getContext("2d");
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SCALE = 2; // simulation runs at half resolution
  let W, H, trail, next, img, ax, ay, ah, N, foods;
  const LABELS = [
    "United Kingdom",
    "Charles III",
    "Canada",
    "Europe",
    "A. A. Milne",
    "Columbia Records",
    "Australia",
    "English",
    "Kelly Rowland",
    "Czech Republic",
    "Petr Pavel",
    "Jamaica",
  ];

  function init() {
    const r = cv.getBoundingClientRect();
    W = Math.max(200, Math.floor(r.width / SCALE));
    H = Math.max(150, Math.floor(r.height / SCALE));
    cv.width = W;
    cv.height = H;
    trail = new Float32Array(W * H);
    next = new Float32Array(W * H);
    img = ctx.createImageData(W, H);
    N = Math.min(9000, Math.floor((W * H) / 22));
    ax = new Float32Array(N);
    ay = new Float32Array(N);
    ah = new Float32Array(N);
    const rnd = mulberry(7);
    foods = LABELS.map((name, i) => ({ name, x: W * (0.38 + 0.58 * rnd()), y: H * (0.12 + 0.76 * rnd()) }));
    for (let i = 0; i < N; i++) {
      // spawn agents around food so networks form fast
      const f = foods[i % foods.length],
        a = rnd() * 6.283,
        d = rnd() * 40;
      ax[i] = f.x + Math.cos(a) * d;
      ay[i] = f.y + Math.sin(a) * d;
      ah[i] = rnd() * 6.283;
    }
  }
  function mulberry(a) {
    return function () {
      a |= 0;
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const SA = 0.5,
    RA = 0.42,
    SO = 9,
    SS = 1.05,
    DEP = 5,
    DECAY = 0.92;
  function sense(x, y, h) {
    const sx = (x + Math.cos(h) * SO) | 0,
      sy = (y + Math.sin(h) * SO) | 0;
    if (sx < 0 || sy < 0 || sx >= W || sy >= H) return -1;
    return trail[sy * W + sx];
  }
  function step() {
    for (const f of foods) {
      // food emits
      const fx = f.x | 0,
        fy = f.y | 0;
      for (let dy = -2; dy <= 2; dy++)
        for (let dx = -2; dx <= 2; dx++) {
          const x = fx + dx,
            y = fy + dy;
          if (x >= 0 && y >= 0 && x < W && y < H) trail[y * W + x] += 9;
        }
    }
    for (let i = 0; i < N; i++) {
      let h = ah[i];
      const c = sense(ax[i], ay[i], h),
        l = sense(ax[i], ay[i], h - SA),
        r = sense(ax[i], ay[i], h + SA);
      if (c > l && c > r) {
      } else if (c < l && c < r) h += (Math.random() < 0.5 ? -1 : 1) * RA;
      else if (l > r) h -= RA;
      else if (r > l) h += RA;
      let x = ax[i] + Math.cos(h) * SS,
        y = ay[i] + Math.sin(h) * SS;
      if (x < 1 || y < 1 || x >= W - 1 || y >= H - 1) {
        h += Math.PI * (0.5 + Math.random());
        x = ax[i];
        y = ay[i];
      }
      ax[i] = x;
      ay[i] = y;
      ah[i] = h;
      trail[(y | 0) * W + (x | 0)] += DEP;
    }
    for (let y = 1; y < H - 1; y++) {
      // diffuse + decay (3x3 mean)
      const o = y * W;
      for (let x = 1; x < W - 1; x++) {
        const k = o + x;
        next[k] =
          ((trail[k] +
            trail[k - 1] +
            trail[k + 1] +
            trail[k - W] +
            trail[k + W] +
            trail[k - W - 1] +
            trail[k - W + 1] +
            trail[k + W - 1] +
            trail[k + W + 1]) /
            9) *
          DECAY;
      }
    }
    const t = trail;
    trail = next;
    next = t;
  }
  const light = () => document.documentElement.dataset.theme === "light";
  function draw() {
    const d = img.data,
      L = light();
    for (let k = 0, p = 0; k < trail.length; k++, p += 4) {
      const v = Math.min(1, trail[k] / 26);
      const g = v * v;
      if (L) {
        // amber ink on warm paper
        d[p] = 246 - 36 * v;
        d[p + 1] = 244 - 110 * Math.min(1, v * 1.2) + 10 * g;
        d[p + 2] = 239 - 225 * Math.min(1, v * 1.4);
        d[p + 3] = 255;
      } else {
        // glowing gold on black
        d[p] = 12 + 232 * Math.min(1, v * 1.3);
        d[p + 1] = 9 + 175 * g + 30 * v;
        d[p + 2] = 8 + 60 * g * g;
        d[p + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    ctx.font = "5.5px Geist Mono, monospace";
    for (const f of foods) {
      ctx.fillStyle = L ? "rgba(150,90,0,.95)" : "rgba(255,226,150,.95)";
      ctx.beginPath();
      ctx.arc(f.x, f.y, 2.2, 0, 6.283);
      ctx.fill();
      ctx.fillStyle = L ? "rgba(40,34,24,.6)" : "rgba(236,234,228,.55)";
      ctx.fillText(f.name, f.x + 4, f.y - 3);
    }
  }
  let visible = true;
  new IntersectionObserver(e => {
    visible = e[0].isIntersecting;
  }).observe(cv);
  function loop() {
    if (visible && !document.hidden) {
      step();
      if (!reduce) step();
      draw();
    }
    requestAnimationFrame(loop);
  }
  init();
  for (let i = 0; i < 60; i++) step(); // pre-grow so the first frame already shows tubes
  let rt;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(init, 250);
  });
  loop();
})();
