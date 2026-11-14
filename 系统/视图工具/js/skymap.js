// skymap.js — 天区选择器：可缩放的全天图 + 全天小地图
import { DEG, RAD, norm360, norm180, angSep, eclipticToEquatorial, equatorialToGalactic, eqFromGalactic } from './astro.js';

const C = {
  bg0: '#050a12',
  bg1: '#08111c',
  grid: 'rgba(120,175,225,0.14)',
  gridMajor: 'rgba(130,190,240,0.26)',
  axis: 'rgba(150,200,240,0.55)',
  cov: 'rgba(70,205,255,0.55)',
  covDim: 'rgba(70,205,255,0.22)',
  hit: 'rgba(255,180,84,0.85)',
  hitFill: 'rgba(255,180,84,0.055)',
  amber: '#ffb454',
  label: 'rgba(160,200,235,0.75)',
  target: '#8fe9c6',
};

const niceStep = (span, px, targets) => {
  for (const s of targets) if ((s / span) * px > 58) return s;
  return targets[targets.length - 1];
};

export function createSkyMap({ canvas, minimap, fields = [], targets = [], onChange, onHover }) {
  const st = {
    ra: 269.452077,
    dec: 4.693365,
    fov: 3.5,
    viewRa: 269.452077,
    viewDec: 4.693365,
    span: 24,
  };

  const ctx = canvas.getContext('2d');
  const mctx = minimap.getContext('2d');
  let W = 0, H = 0, dpr = 1;

  const map = { x: 0, y: 0, w: 0, h: 0, scale: 1 };
  const mini = { x: 0, y: 0, w: 0, h: 0, scale: 1 };

  function fit() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    const r = canvas.getBoundingClientRect();
    W = Math.max(10, Math.round(r.width));
    H = Math.max(10, Math.round(r.height));
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const mr = minimap.getBoundingClientRect();
    minimap.width = Math.round(mr.width * dpr);
    minimap.height = Math.round(mr.height * dpr);
    mctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    redraw();
  }

  // ---- 投影 ----
  const cosDeg = (d) => Math.cos(d * DEG);
  function proj(ra, dec) {
    const dra = norm180(ra - st.viewRa) * cosDeg(st.viewDec);
    const ddec = dec - st.viewDec;
    return {
      x: W / 2 + (dra / st.span) * W,
      y: H / 2 - (ddec / (st.span / (W / H))) * H,
    };
  }
  function unproj(x, y) {
    const ra = st.viewRa + ((x - W / 2) / W) * st.span / cosDeg(st.viewDec);
    const dec = st.viewDec - ((y - H / 2) / H) * (st.span / (W / H));
    return { ra: norm360(ra), dec: Math.max(-89.9, Math.min(89.9, dec)) };
  }

  // ---- 绘制 ----
  function drawGrid() {
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, C.bg0);
    g.addColorStop(0.55, C.bg1);
    g.addColorStop(1, '#060c14');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    const step = niceStep(st.span, W, [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60]);
    const halfW = st.span / 2 / cosDeg(st.viewDec);
    const halfH = st.span / (W / H) / 2;

    const ra0 = Math.floor((st.viewRa - halfW) / step) * step;
    const ra1 = st.viewRa + halfW;
    const dec0 = Math.max(-90, Math.floor((st.viewDec - halfH) / step) * step);
    const dec1 = Math.min(90, st.viewDec + halfH);

    ctx.lineWidth = 1;
    ctx.font = '9px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textBaseline = 'top';

    for (let r = ra0; r <= ra1; r += step) {
      const p = proj(r, st.viewDec);
      if (p.x < -2 || p.x > W + 2) continue;
      const major = Math.abs(norm360(r) % 30) < 1e-6;
      ctx.strokeStyle = major ? C.gridMajor : C.grid;
      ctx.beginPath();
      ctx.moveTo(Math.round(p.x) + 0.5, 0);
      ctx.lineTo(Math.round(p.x) + 0.5, H);
      ctx.stroke();
      if (major) {
        ctx.fillStyle = C.label;
        ctx.fillText(`${norm360(r).toFixed(0)}°`, Math.round(p.x) + 3, 3);
      }
    }
    for (let d = dec0; d <= dec1; d += step) {
      const p = proj(st.viewRa, d);
      if (p.y < -2 || p.y > H + 2) continue;
      const major = Math.abs(d % 30) < 1e-6;
      ctx.strokeStyle = major ? C.gridMajor : C.grid;
      ctx.beginPath();
      ctx.moveTo(0, Math.round(p.y) + 0.5);
      ctx.lineTo(W, Math.round(p.y) + 0.5);
      ctx.stroke();
      if (major) {
        ctx.fillStyle = C.label;
        ctx.fillText(`${d.toFixed(0)}°`, 3, Math.round(p.y) + 2);
      }
    }

    // 银道面
    ctx.strokeStyle = 'rgba(140,120,255,0.30)';
    ctx.setLineDash([3, 4]);
    ctx.beginPath();
    let started = false;
    for (let l = 0; l <= 360; l += 2) {
      const e = eqFromGalactic(l, 0);
      const p = proj(e.ra, e.dec);
      if (!started) { ctx.moveTo(p.x, p.y); started = true; }
      else ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();

    // 黄道
    ctx.strokeStyle = 'rgba(255,210,120,0.28)';
    ctx.beginPath();
    started = false;
    for (let lam = 0; lam <= 360; lam += 2) {
      const e = eclipticToEquatorial(lam);
      const p = proj(e.ra, e.dec);
      if (!started) { ctx.moveTo(p.x, p.y); started = true; }
      else ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function drawCoverage() {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const size = st.span > 90 ? 1 : 1.6;
    for (const f of fields) {
      const p = proj(f.ra, f.dec);
      if (p.x < -4 || p.x > W + 4 || p.y < -4 || p.y > H + 4) continue;
      ctx.fillStyle = C.covDim;
      ctx.fillRect(p.x - size / 2, p.y - size / 2, size, size);
    }
    ctx.restore();
  }

  function hits() {
    if (!fields.length) return [];
    const lim = 2.47; // 单片 3.49° 方视场的半对角线（与 main.js 的 HIT_RADIUS_DEG 一致）
    const out = [];
    for (const f of fields) {
      if (Math.abs(f.dec - st.dec) > 3) continue;
      const sep = angSep(f.ra, f.dec, st.ra, st.dec);
      if (sep <= lim) out.push({ f, sep });
    }
    out.sort((a, b) => a.sep - b.sep);
    return out;
  }

  function drawHits(list) {
    const half = 3.49 / 2; // 足迹方块按真实半宽绘制；命中半径仍是 2.47° 半对角线
    const px = (half * 2) / st.span * W;
    if (px < 3 || px > W * 0.55) return; // 视野太宽/太窄时足迹没有信息量
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (const h of list.slice(0, 240)) {
      const p = proj(h.f.ra, h.f.dec);
      if (p.x < -px || p.x > W + px || p.y < -px || p.y > H + px) continue;
      ctx.fillStyle = C.hitFill;
      ctx.fillRect(p.x - px / 2, p.y - px / 2, px, px);
      ctx.strokeStyle = 'rgba(255,180,84,0.18)';
      ctx.lineWidth = 0.6;
      ctx.strokeRect(p.x - px / 2, p.y - px / 2, px, px);
    }
    ctx.restore();
  }

  function drawTargets() {
    ctx.font = '9px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textBaseline = 'middle';
    for (const t of targets) {
      const p = proj(t.ra, t.dec);
      if (p.x < -20 || p.x > W + 20 || p.y < -20 || p.y > H + 20) continue;
      ctx.strokeStyle = C.target;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(p.x - 4, p.y);
      ctx.lineTo(p.x + 4, p.y);
      ctx.moveTo(p.x, p.y - 4);
      ctx.lineTo(p.x, p.y + 4);
      ctx.stroke();
      ctx.fillStyle = 'rgba(143,233,198,0.9)';
      const short = t.short || t.name;
      ctx.fillText(short, p.x + 6, p.y - 6);
    }
  }

  function drawBox() {
    const wDeg = Math.min(360, st.fov / Math.max(0.12, cosDeg(st.dec)));
    const hDeg = st.fov;
    const p0 = proj(st.ra - wDeg / 2, st.dec + hDeg / 2);
    const p1 = proj(st.ra + wDeg / 2, st.dec - hDeg / 2);
    const x = p0.x, y = p0.y, w = p1.x - p0.x, h = p1.y - p0.y;

    ctx.save();
    ctx.fillStyle = 'rgba(255,180,84,0.07)';
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = C.amber;
    ctx.lineWidth = 1.2;
    ctx.strokeRect(Math.round(x) + 0.5, Math.round(y) + 0.5, Math.max(2, Math.round(w)), Math.max(2, Math.round(h)));

    // 四角把手
    const k = 5;
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#ffd39a';
    for (const [cx, cy, sx, sy] of [
      [x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1],
    ]) {
      ctx.beginPath();
      ctx.moveTo(cx + sx * k, cy);
      ctx.lineTo(cx, cy);
      ctx.lineTo(cx, cy + sy * k);
      ctx.stroke();
    }

    // 中心十字
    const cx = x + w / 2, cy = y + h / 2;
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255,211,154,0.9)';
    ctx.beginPath();
    ctx.moveTo(cx - 6, cy);
    ctx.lineTo(cx + 6, cy);
    ctx.moveTo(cx, cy - 6);
    ctx.lineTo(cx, cy + 6);
    ctx.stroke();

    // 尺寸标签
    ctx.font = '9px ui-monospace, SFMono-Regular, Menlo, monospace';
    const label = `${st.fov.toFixed(2)}°`;
    const tw = ctx.measureText(label).width;
    const ly = Math.min(H - 12, Math.max(10, y - 8));
    ctx.fillStyle = 'rgba(12,18,28,0.85)';
    ctx.fillRect(Math.max(2, x), ly - 8, tw + 8, 12);
    ctx.fillStyle = C.amber;
    ctx.fillText(label, Math.max(6, x + 4), ly - 2);
    ctx.restore();
  }

  function drawScaleBar() {
    const deg = st.span / 5;
    const px = (deg / st.span) * W;
    const y = H - 8;
    ctx.save();
    ctx.strokeStyle = 'rgba(160,200,235,0.55)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(8, y);
    ctx.lineTo(8 + px, y);
    ctx.moveTo(8, y - 3);
    ctx.lineTo(8, y + 1);
    ctx.moveTo(8 + px, y - 3);
    ctx.lineTo(8 + px, y + 1);
    ctx.stroke();
    ctx.fillStyle = 'rgba(160,200,235,0.7)';
    ctx.font = '9px ui-monospace, monospace';
    ctx.textBaseline = 'bottom';
    ctx.fillText(deg >= 1 ? `${deg.toFixed(0)}°` : `${deg.toFixed(1)}°`, 12 + px, y + 1);
    ctx.restore();
  }

  function drawMinimap() {
    const mw = minimap.width / dpr;
    const mh = minimap.height / dpr;
    mctx.clearRect(0, 0, mw, mh);
    mctx.fillStyle = 'rgba(5,10,18,0.78)';
    mctx.fillRect(0, 0, mw, mh);

    // coverage 极简密度
    mctx.save();
    mctx.globalCompositeOperation = 'lighter';
    mctx.fillStyle = 'rgba(70,205,255,0.16)';
    const s = 1;
    for (const f of fields) {
      const x = (norm360(f.ra) / 360) * mw;
      const y = ((90 - f.dec) / 180) * mh;
      mctx.fillRect(x, y, s, s);
    }
    mctx.restore();

    mctx.strokeStyle = 'rgba(120,175,225,0.20)';
    mctx.lineWidth = 0.6;
    for (let r = 0; r <= 360; r += 60) {
      const x = (r / 360) * mw;
      mctx.beginPath();
      mctx.moveTo(x, 0);
      mctx.lineTo(x, mh);
      mctx.stroke();
    }
    for (let d = -60; d <= 60; d += 30) {
      const y = ((90 - d) / 180) * mh;
      mctx.beginPath();
      mctx.moveTo(0, y);
      mctx.lineTo(mw, y);
      mctx.stroke();
    }

    // 视野框
    const spanDeg = st.span;
    const halfW = spanDeg / 2 / cosDeg(st.viewDec);
    const halfH = spanDeg / (W / H) / 2;
    const vx = ((st.viewRa - halfW) / 360) * mw;
    const vw = ((halfW * 2) / 360) * mw;
    const vy = ((90 - (st.viewDec + halfH)) / 180) * mh;
    const vh = ((halfH * 2) / 180) * mh;
    mctx.strokeStyle = 'rgba(200,230,255,0.55)';
    mctx.lineWidth = 0.8;
    mctx.strokeRect(vx, vy, Math.max(2, vw), Math.max(2, vh));

    // 选中天区
    const bx = (st.ra / 360) * mw;
    const by = ((90 - st.dec) / 180) * mh;
    mctx.strokeStyle = C.amber;
    mctx.lineWidth = 1;
    mctx.beginPath();
    mctx.arc(bx, by, 2.2, 0, Math.PI * 2);
    mctx.stroke();
    mctx.fillStyle = C.amber;
    mctx.fillRect(bx - 0.7, by - 0.7, 1.4, 1.4);
  }

  function redraw() {
    if (!W || !H) return;
    drawGrid();
    drawCoverage();
    drawTargets();
    drawHits(hits());
    drawBox();
    drawScaleBar();
    drawMinimap();
  }

  // ---- 交互 ----
  let drag = null;

  const localPos = (e) => {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  function boxScreen() {
    const wDeg = Math.min(360, st.fov / Math.max(0.12, cosDeg(st.dec)));
    const p0 = proj(st.ra - wDeg / 2, st.dec + st.fov / 2);
    const p1 = proj(st.ra + wDeg / 2, st.dec - st.fov / 2);
    return { x: p0.x, y: p0.y, w: p1.x - p0.x, h: p1.y - p0.y };
  }

  canvas.addEventListener('pointerdown', (e) => {
    const p = localPos(e);
    const b = boxScreen();
    const k = 9;
    const corners = [
      ['nw', b.x, b.y], ['ne', b.x + b.w, b.y],
      ['sw', b.x, b.y + b.h], ['se', b.x + b.w, b.y + b.h],
    ];
    const hit = corners.find(([, x, y]) => Math.abs(p.x - x) < k && Math.abs(p.y - y) < k);
    const inside = p.x > b.x && p.x < b.x + b.w && p.y > b.y && p.y < b.y + b.h;

    if (hit) {
      drag = { mode: 'resize', corner: hit[0], x: p.x, y: p.y, fov: st.fov, dec: st.dec };
    } else if (inside) {
      drag = { mode: 'move', x: p.x, y: p.y, ra: st.ra, dec: st.dec };
    } else {
      drag = { mode: 'pan', x: p.x, y: p.y, vra: st.viewRa, vdec: st.viewDec, moved: 0 };
    }
    canvas.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  canvas.addEventListener('pointermove', (e) => {
    const p = localPos(e);
    if (!drag) {
      const sky = unproj(p.x, p.y);
      onHover?.(sky, angSep(sky.ra, sky.dec, st.ra, st.dec));
      canvas.style.cursor = insideBox(p) ? 'move' : 'crosshair';
      return;
    }
    if (drag.mode === 'pan') {
      const dx = p.x - drag.x;
      const dy = p.y - drag.y;
      drag.moved = Math.max(drag.moved, Math.abs(dx) + Math.abs(dy));
      st.viewRa = norm360(drag.vra - (dx / W) * st.span / cosDeg(drag.vdec));
      st.viewDec = Math.max(-89, Math.min(89, drag.vdec + (dy / H) * (st.span / (W / H))));
      redraw();
    } else if (drag.mode === 'move') {
      const ra = drag.ra - ((p.x - drag.x) / W) * st.span / cosDeg(st.viewDec);
      const dec = drag.dec + ((p.y - drag.y) / H) * (st.span / (W / H));
      st.ra = norm360(ra);
      st.dec = Math.max(-89.5, Math.min(89.5, dec));
      redraw();
      onChange?.({ ra: st.ra, dec: st.dec, fov: st.fov, source: 'map' });
    } else if (drag.mode === 'resize') {
      const dy = p.y - drag.y;
      const f = Math.min(180, Math.max(0.05, st.fov + (dy / H) * (st.span / (W / H)) * 2));
      st.fov = f;
      redraw();
      onChange?.({ ra: st.ra, dec: st.dec, fov: st.fov, source: 'map' });
    }
  });

  function insideBox(p) {
    const b = boxScreen();
    return p.x > b.x && p.x < b.x + b.w && p.y > b.y && p.y < b.y + b.h;
  }

  function endDrag(e) {
    if (drag?.mode === 'pan' && drag.moved < 4) {
      const p = localPos(e);
      const sky = unproj(p.x, p.y);
      st.ra = sky.ra;
      st.dec = sky.dec;
      redraw();
      onChange?.({ ra: st.ra, dec: st.dec, fov: st.fov, source: 'map' });
    }
    drag = null;
    canvas.releasePointerCapture?.(e.pointerId);
  }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', () => (drag = null));

  canvas.addEventListener(
    'wheel',
    (e) => {
      e.preventDefault();
      st.span = Math.min(360, Math.max(1.5, st.span * Math.exp(e.deltaY * 0.0015)));
      // 缩放时保持中心在视图中
      st.viewRa = st.ra;
      st.viewDec = st.dec;
      redraw();
    },
    { passive: false }
  );

  canvas.addEventListener('dblclick', () => {
    st.span = Math.min(360, Math.max(1.5, st.fov * 7));
    st.viewRa = st.ra;
    st.viewDec = st.dec;
    redraw();
  });

  // 小地图
  let miniDrag = false;
  minimap.addEventListener('pointerdown', (e) => {
    miniDrag = true;
    jumpMini(e);
  });
  minimap.addEventListener('pointermove', (e) => {
    if (miniDrag) jumpMini(e);
  });
  window.addEventListener('pointerup', () => (miniDrag = false));

  function jumpMini(e) {
    const r = minimap.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    st.ra = norm360(x * 360);
    st.dec = Math.max(-89.5, Math.min(89.5, 90 - y * 180));
    st.viewRa = st.ra;
    st.viewDec = st.dec;
    redraw();
    onChange?.({ ra: st.ra, dec: st.dec, fov: st.fov, source: 'map' });
  }

  // ---- 对外 ----
  const ro = new ResizeObserver(fit);
  ro.observe(canvas);

  function setSelection({ ra, dec, fov }, opts = {}) {
    if (ra !== undefined) st.ra = norm360(ra);
    if (dec !== undefined) st.dec = Math.max(-89.5, Math.min(89.5, dec));
    if (fov !== undefined) st.fov = Math.max(0.05, Math.min(180, fov));
    if (opts.recenter !== false) {
      st.viewRa = st.ra;
      st.viewDec = st.dec;
    }
    if (opts.rezoom) st.span = Math.min(360, Math.max(1.5, st.fov * 7));
    redraw();
  }

  function setFields(list) {
    fields = list;
    redraw();
  }

  return {
    state: st,
    setSelection,
    setFields,
    redraw,
    hits,
    fit,
  };
}
