// globe.js — 三维地球（three.js，全本地资源）
// 视觉语言参考 bilawalsidhu/gods-eye-view：深空底、昼夜终结线、城市灯光、HUD 式标记。
import * as THREE from 'three';
import { DEG, RAD, gmstDeg, sunRaDec } from './astro.js';

const EARTH_R = 1;
const TILE_R = 1.62;

const vert = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vN;
  varying vec3 vP;
  void main() {
    vUv = uv;
    vN = normalize(mat3(modelMatrix) * normal);
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vP = wp.xyz;
    gl_Position = projectionMatrix * viewMatrix * wp;
  }
`;

const earthFrag = /* glsl */ `
  uniform sampler2D uDay;
  uniform sampler2D uNight;
  uniform sampler2D uSpec;
  uniform vec3 uSun;
  uniform vec3 uCam;
  varying vec2 vUv;
  varying vec3 vN;
  varying vec3 vP;

  void main() {
    vec3 N = normalize(vN);
    vec3 V = normalize(uCam - vP);
    float lam = dot(N, uSun);
    float lit = smoothstep(-0.16, 0.22, lam);
    float diff = max(lam, 0.0);

    vec3 day = texture2D(uDay, vUv).rgb;
    vec3 night = texture2D(uNight, vUv).rgb;
    vec3 spec = texture2D(uSpec, vUv).rgb;

    vec3 H = normalize(uSun + V);
    float specAmt = pow(max(dot(N, H), 0.0), 64.0) * spec.r * 0.85 * smoothstep(0.0, 0.18, lam);

    vec3 col = mix(night * 1.12 + vec3(0.012, 0.018, 0.035), day * (0.16 + 1.02 * diff), lit);
    col += vec3(1.0, 0.96, 0.88) * specAmt;

    float fres = pow(1.0 - max(dot(N, V), 0.0), 3.0);
    col += vec3(0.16, 0.44, 0.92) * fres * 0.38 * smoothstep(-0.25, 0.35, lam);
    col += vec3(0.05, 0.10, 0.22) * fres * 0.55 * (1.0 - lit);

    gl_FragColor = vec4(col, 1.0);
  }
`;

const cloudFrag = /* glsl */ `
  uniform sampler2D uCloud;
  uniform vec3 uSun;
  uniform vec3 uCam;
  varying vec2 vUv;
  varying vec3 vN;
  varying vec3 vP;
  void main() {
    vec4 c = texture2D(uCloud, vUv);
    float lam = dot(normalize(vN), uSun);
    float lit = smoothstep(-0.12, 0.30, lam);
    float rim = pow(1.0 - abs(dot(normalize(vN), normalize(uCam - vP))), 2.0);
    vec3 col = vec3(1.0) * (0.18 + 0.95 * lit) + vec3(0.20, 0.42, 0.85) * rim * 0.25;
    gl_FragColor = vec4(col, c.a * (0.14 + 0.42 * lit));
  }
`;

const atmoFrag = /* glsl */ `
  uniform vec3 uSun;
  uniform vec3 uCam;
  varying vec3 vN;
  varying vec3 vP;
  void main() {
    vec3 N = normalize(vN);
    vec3 V = normalize(uCam - vP);
    float rim = pow(1.0 - abs(dot(N, V)), 2.4);
    float lam = dot(N, uSun);
    float glow = smoothstep(-0.55, 0.45, lam);
    vec3 col = mix(vec3(0.10, 0.22, 0.55), vec3(0.36, 0.68, 1.0), glow);
    gl_FragColor = vec4(col, rim * (0.10 + 0.55 * glow));
  }
`;

const tileVert = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const tileFrag = /* glsl */ `
  uniform float uTime;
  uniform vec3 uColor;
  uniform float uOpacity;
  varying vec2 vUv;
  void main() {
    vec2 uv = vUv;
    vec2 d = abs(uv - 0.5) * 2.0;

    vec2 g = abs(fract(uv * 6.0) - 0.5);
    float grid = 1.0 - smoothstep(0.0, 0.055, min(g.x, g.y));
    float border = step(0.975, max(d.x, d.y));
    float corner = step(0.80, max(d.x, d.y)) * (1.0 - step(0.962, max(d.x, d.y)))
                 * step(0.60, min(d.x, d.y));
    float scan = exp(-pow((uv.y - fract(uTime * 0.11)) * 26.0, 2.0)) * 0.30;
    float fill = 0.022 + 0.02 * (1.0 - uv.y);

    float a = (fill + grid * 0.26 + border * 0.8 + corner * 0.65 + scan) * uOpacity;
    vec3 col = uColor * (0.55 + border * 0.7 + corner * 1.1 + scan * 0.6);
    gl_FragColor = vec4(col, a);
  }
`;

const starVert = /* glsl */ `
  attribute float aSize;
  attribute vec3 aColor;
  varying vec3 vC;
  varying float vTw;
  uniform float uTime;
  void main() {
    vC = aColor;
    vTw = 0.78 + 0.22 * sin(uTime * 1.7 + aSize * 91.0);
    gl_PointSize = aSize;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const starFrag = /* glsl */ `
  varying vec3 vC;
  varying float vTw;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float a = smoothstep(0.5, 0.0, length(c));
    gl_FragColor = vec4(vC, a * vTw);
  }
`;

const trackVert = /* glsl */ `
  attribute float aMjd;
  attribute float aAlpha;
  varying float vA;
  uniform float uNow;
  uniform float uWindow;
  void main() {
    float d = abs(aMjd - uNow) / uWindow;
    vA = exp(-d * d * 1.4) * aAlpha;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const trackFrag = /* glsl */ `
  uniform vec3 uColor;
  varying float vA;
  void main() {
    if (vA < 0.004) discard;
    gl_FragColor = vec4(uColor, vA);
  }
`;

function lonLatToVec3(latDeg, lonDeg, r = 1) {
  const la = latDeg * DEG;
  const lo = lonDeg * DEG;
  return new THREE.Vector3(
    r * Math.cos(la) * Math.cos(lo),
    r * Math.sin(la),
    -r * Math.cos(la) * Math.sin(lo)
  );
}

function vec3ToLonLat(v) {
  const n = v.clone().normalize();
  const lat = Math.asin(THREE.MathUtils.clamp(n.y, -1, 1)) * RAD;
  const lon = Math.atan2(-n.z, n.x) * RAD;
  return { lat, lon };
}

export function createGlobe(canvas, opts = {}) {
  const state = {
    clock: 60000,
    ra: 210.80227,
    dec: 54.34895,
    fov: 3.5,
    showTrack: true,
    showCoverage: true,
    onPick: opts.onPick || (() => {}),
    onHover: opts.onHover || null,
  };

  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    powerPreference: 'high-performance',
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 400);

  const manager = new THREE.LoadingManager();
  const ready = new Promise((resolve) => { manager.onLoad = () => resolve(); });
  const loader = new THREE.TextureLoader(manager);
  const tex = (path, srgb = true) => {
    const t = loader.load(path);
    t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    t.anisotropy = renderer.capabilities.getMaxAnisotropy();
    return t;
  };

  const sunDir = { value: new THREE.Vector3(1, 0, 0) };
  const camPos = { value: new THREE.Vector3() };

  // ---------- 地球 ----------
  const earthGroup = new THREE.Group();
  scene.add(earthGroup);

  const earth = new THREE.Mesh(
    new THREE.SphereGeometry(EARTH_R, 128, 96),
    new THREE.ShaderMaterial({
      uniforms: {
        uDay: { value: tex('./assets/earth_atmos_2048.jpg') },
        uNight: { value: tex('./assets/earth_lights_2048.png') },
        uSpec: { value: tex('./assets/earth_specular_2048.jpg', false) },
        uSun: sunDir,
        uCam: camPos,
      },
      vertexShader: vert,
      fragmentShader: earthFrag,
    })
  );
  earthGroup.add(earth);

  const clouds = new THREE.Mesh(
    new THREE.SphereGeometry(EARTH_R * 1.004, 96, 64),
    new THREE.ShaderMaterial({
      uniforms: {
        uCloud: { value: tex('./assets/earth_clouds_1024.png') },
        uSun: sunDir,
        uCam: camPos,
      },
      vertexShader: vert,
      fragmentShader: cloudFrag,
      transparent: true,
      depthWrite: false,
    })
  );
  earthGroup.add(clouds);

  const atmo = new THREE.Mesh(
    new THREE.SphereGeometry(EARTH_R * 1.075, 96, 64),
    new THREE.ShaderMaterial({
      uniforms: { uSun: sunDir, uCam: camPos },
      vertexShader: vert,
      fragmentShader: atmoFrag,
      transparent: true,
      depthWrite: false,
      side: THREE.BackSide,
      blending: THREE.AdditiveBlending,
    })
  );
  scene.add(atmo);

  // ---------- 星空 ----------
  const starGeo = new THREE.BufferGeometry();
  const N = 9000;
  const pos = new Float32Array(N * 3);
  const col = new Float32Array(N * 3);
  const siz = new Float32Array(N);
  const v = new THREE.Vector3();
  for (let i = 0; i < N; i++) {
    v.randomDirection().multiplyScalar(60 + Math.random() * 40);
    pos.set([v.x, v.y, v.z], i * 3);
    const t = Math.random();
    const c = t > 0.92 ? [1.0, 0.82, 0.62] : t > 0.7 ? [0.82, 0.9, 1.0] : [1, 1, 1];
    col.set(c, i * 3);
    siz[i] = Math.random() < 0.04 ? 1.6 + Math.random() * 1.8 : 0.4 + Math.random() * 0.9;
  }
  starGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  starGeo.setAttribute('aColor', new THREE.BufferAttribute(col, 3));
  starGeo.setAttribute('aSize', new THREE.BufferAttribute(siz, 1));
  const starMat = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 } },
    vertexShader: starVert,
    fragmentShader: starFrag,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  scene.add(new THREE.Points(starGeo, starMat));

  // ---------- 数据层 ----------
  const trackMat = new THREE.ShaderMaterial({
    uniforms: {
      uNow: { value: 0 },
      uWindow: { value: 0.5 },
      uColor: { value: new THREE.Color('#54e8ff') },
    },
    vertexShader: trackVert,
    fragmentShader: trackFrag,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const trackLine = new THREE.LineSegments(new THREE.BufferGeometry(), trackMat);
  earthGroup.add(trackLine);

  const coverage = new THREE.Points(
    new THREE.BufferGeometry(),
    new THREE.PointsMaterial({
      color: 0x2f8fbf,
      size: 1.3,
      sizeAttenuation: false,
      transparent: true,
      opacity: 0.28,
      depthWrite: false,
    })
  );
  earthGroup.add(coverage);

  // 卫星标记（贴合地表的圆环 + 光点）
  const sat = new THREE.Group();
  sat.add(new THREE.Mesh(new THREE.SphereGeometry(0.011, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffffff })));
  const satHalo = new THREE.Mesh(
    new THREE.RingGeometry(0.024, 0.032, 32),
    new THREE.MeshBasicMaterial({
      color: 0x54e8ff,
      transparent: true,
      opacity: 0.85,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    })
  );
  sat.add(satHalo);
  earthGroup.add(sat);

  // ---------- 选中天区标记 ----------
  const selGroup = new THREE.Group();
  scene.add(selGroup);

  const tileMat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uColor: { value: new THREE.Color('#ffb454') },
      uOpacity: { value: 1 },
    },
    vertexShader: tileVert,
    fragmentShader: tileFrag,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
  });
  const tile = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), tileMat);
  selGroup.add(tile);
  const selQuat = new THREE.Quaternion();

  const beamGeo = new THREE.BufferGeometry();
  beamGeo.setAttribute(
    'position',
    new THREE.BufferAttribute(new Float32Array([0, EARTH_R * 1.012, 0, 0, TILE_R - 0.04, 0]), 3)
  );
  const beam = new THREE.Line(
    beamGeo,
    new THREE.LineDashedMaterial({
      color: 0xffb454,
      dashSize: 0.02,
      gapSize: 0.02,
      transparent: true,
      opacity: 0.5,
      depthWrite: false,
    })
  );
  beam.computeLineDistances();
  selGroup.add(beam);

  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.6, 0.76, 64),
    new THREE.MeshBasicMaterial({
      color: 0xffb454,
      transparent: true,
      opacity: 0.7,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    })
  );
  selGroup.add(ring);

  const siteDot = new THREE.Mesh(
    new THREE.SphereGeometry(0.008, 12, 10),
    new THREE.MeshBasicMaterial({ color: 0xffe0b0 })
  );
  selGroup.add(siteDot);

  // ---------- 相机控制 ----------
  const orbit = {
    radius: 3.9,
    yaw: 210 * DEG,
    pitch: 54 * DEG,
    targetRadius: 3.9,
    targetYaw: 210 * DEG,
    targetPitch: 54 * DEG,
    dragging: false,
    lastX: 0,
    lastY: 0,
    moved: 0,
    downAt: 0,
    tween: false,
  };

  function placeCamera() {
    const r = orbit.radius;
    const cp = Math.cos(orbit.pitch);
    camera.position.set(
      r * cp * Math.cos(orbit.yaw),
      r * Math.sin(orbit.pitch),
      -r * cp * Math.sin(orbit.yaw)
    );
    camera.lookAt(0, 0, 0);
    camPos.value.copy(camera.position);
  }
  placeCamera();

  function focus(ra, dec, immediate = false) {
    const yaw = ra * DEG;
    const pitch = THREE.MathUtils.clamp(dec * DEG, -1.2, 1.2);
    let dy = yaw - orbit.yaw;
    while (dy > Math.PI) dy -= Math.PI * 2;
    while (dy < -Math.PI) dy += Math.PI * 2;
    orbit.targetYaw = orbit.yaw + dy;
    orbit.targetPitch = pitch;
    orbit.tween = true;
    if (immediate) {
      orbit.yaw = orbit.targetYaw;
      orbit.pitch = orbit.targetPitch;
      orbit.tween = false;
      placeCamera();
    }
  }

  canvas.addEventListener('pointerdown', (e) => {
    orbit.dragging = true;
    orbit.moved = 0;
    orbit.downAt = performance.now();
    orbit.lastX = e.clientX;
    orbit.lastY = e.clientY;
    canvas.setPointerCapture(e.pointerId);
  });

  canvas.addEventListener('pointermove', (e) => {
    if (!orbit.dragging) {
      if (state.onHover) {
        const ll = hitAt(e);
        state.onHover(ll);
      }
      return;
    }
    const dx = e.clientX - orbit.lastX;
    const dy = e.clientY - orbit.lastY;
    orbit.lastX = e.clientX;
    orbit.lastY = e.clientY;
    orbit.moved += Math.abs(dx) + Math.abs(dy);
    orbit.tween = false;
    orbit.yaw -= dx * 0.0045;
    orbit.pitch = THREE.MathUtils.clamp(orbit.pitch + dy * 0.0045, -1.25, 1.25);
    orbit.targetYaw = orbit.yaw;
    orbit.targetPitch = orbit.pitch;
  });

  function endDrag(e) {
    const dt = performance.now() - orbit.downAt;
    orbit.dragging = false;
    canvas.releasePointerCapture?.(e.pointerId);
    if (orbit.moved < 6 && dt < 450) pick(e);
  }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointerleave', () => state.onHover?.(null));
  canvas.addEventListener('pointercancel', () => (orbit.dragging = false));

  canvas.addEventListener(
    'wheel',
    (e) => {
      e.preventDefault();
      orbit.targetRadius = THREE.MathUtils.clamp(
        orbit.targetRadius * Math.exp(e.deltaY * 0.0012),
        1.45,
        9
      );
    },
    { passive: false }
  );

  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function hitAt(e) {
    const rect = canvas.getBoundingClientRect();
    ndc.set(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObject(earth, false)[0];
    if (!hit) return null;
    const local = earth.worldToLocal(hit.point.clone());
    return vec3ToLonLat(local);
  }

  function pick(e) {
    const rect = canvas.getBoundingClientRect();
    ndc.set(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObject(earth, false)[0];
    if (!hit) return;
    const local = earth.worldToLocal(hit.point.clone());
    const ll = vec3ToLonLat(local);
    state.onPick(ll.lat, ll.lon);
  }

  // ---------- 数据 ----------
  let fields = [];

  function setFields(list) {
    fields = list;
    if (!list.length) return;

    const p = new Float32Array(list.length * 3);
    list.forEach((f, i) => {
      const v3 = lonLatToVec3(f.lat, f.lon, EARTH_R * 1.007);
      p.set([v3.x, v3.y, v3.z], i * 3);
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(p, 3));
    coverage.geometry.dispose();
    coverage.geometry = g;

    const segs = [];
    const mjdAttr = [];
    let prev = null;
    for (const f of list) {
      const cur = lonLatToVec3(f.lat, f.lon, EARTH_R * 1.012);
      if (prev) {
        const dt = f.mjd - prev.f.mjd;
        const dlon = Math.abs(((f.lon - prev.f.lon + 540) % 360) - 180);
        if (dt < 0.02 && dlon < 20) {
          segs.push(prev.v.x, prev.v.y, prev.v.z, cur.x, cur.y, cur.z);
          mjdAttr.push(prev.f.mjd, f.mjd);
        }
      }
      prev = { v: cur, f };
    }
    const tg = new THREE.BufferGeometry();
    tg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(segs), 3));
    tg.setAttribute('aMjd', new THREE.BufferAttribute(new Float32Array(mjdAttr), 1));
    tg.setAttribute(
      'aAlpha',
      new THREE.BufferAttribute(new Float32Array(mjdAttr.length).fill(0.9), 1)
    );
    trackLine.geometry.dispose();
    trackLine.geometry = tg;
  }

  function nearestField(mjd) {
    if (!fields.length) return null;
    let lo = 0;
    let hi = fields.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (fields[mid].mjd < mjd) lo = mid + 1;
      else hi = mid;
    }
    const a = fields[Math.max(0, lo - 1)];
    const b = fields[Math.min(fields.length - 1, lo)];
    return Math.abs(a.mjd - mjd) <= Math.abs(b.mjd - mjd) ? a : b;
  }

  // ---------- 更新 ----------
  function setClock(mjd) {
    state.clock = mjd;
    earthGroup.rotation.y = gmstDeg(mjd) * DEG;

    const s = sunRaDec(mjd);
    sunDir.value.copy(lonLatToVec3(s.dec, s.ra, 1)).normalize();
    trackMat.uniforms.uNow.value = mjd;

    const nf = nearestField(mjd);
    if (nf) {
      const dir = lonLatToVec3(nf.lat, nf.lon, 1);
      sat.position.copy(dir).multiplyScalar(EARTH_R * 1.016);
      sat.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), dir);
      sat.visible = true;
    } else {
      sat.visible = false;
    }
    updateSelection();
  }

  function setSelection({ ra, dec, fov }) {
    if (ra !== undefined) state.ra = ra;
    if (dec !== undefined) state.dec = dec;
    if (fov !== undefined) state.fov = fov;
    updateSelection();
  }

  const m4 = new THREE.Matrix4();
  const dirV = new THREE.Vector3();
  const northV = new THREE.Vector3();
  const eastV = new THREE.Vector3();

  function updateSelection() {
    dirV.copy(lonLatToVec3(state.dec, state.ra, 1));
    northV.set(0, 1, 0).addScaledVector(dirV, -dirV.y);
    if (northV.lengthSq() < 1e-8) northV.set(0, 0, 1);
    northV.normalize();
    eastV.crossVectors(northV, dirV).normalize();
    m4.makeBasis(eastV, northV, dirV);
    selQuat.setFromRotationMatrix(m4);

    tile.position.copy(dirV).multiplyScalar(TILE_R);
    tile.quaternion.copy(selQuat);
    ring.position.copy(dirV).multiplyScalar(EARTH_R * 1.002);
    ring.quaternion.copy(selQuat);
    siteDot.position.copy(dirV).multiplyScalar(EARTH_R * 1.004);

    const size = THREE.MathUtils.clamp(0.14 + state.fov * 0.014, 0.15, 0.42);
    tile.scale.set(size, size, 1);
    ring.scale.setScalar(THREE.MathUtils.clamp(0.030 + state.fov * 0.004, 0.032, 0.075));

    const a = dirV.clone().multiplyScalar(EARTH_R * 1.012);
    const b = dirV.clone().multiplyScalar(TILE_R - 0.04);
    const bp = beam.geometry.attributes.position;
    bp.setXYZ(0, a.x, a.y, a.z);
    bp.setXYZ(1, b.x, b.y, b.z);
    bp.needsUpdate = true;
  }

  function setOptions(o) {
    if ('showTrack' in o) {
      state.showTrack = o.showTrack;
      trackLine.visible = o.showTrack;
      sat.visible = o.showTrack && !!nearestField(state.clock);
    }
    if ('showCoverage' in o) {
      state.showCoverage = o.showCoverage;
      coverage.visible = o.showCoverage;
    }
  }

  function resize() {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);

  const t0 = performance.now();
  let raf = 0;

  function frame(now) {
    raf = requestAnimationFrame(frame);
    const t = (now - t0) / 1000;

    if (orbit.tween) {
      orbit.yaw += (orbit.targetYaw - orbit.yaw) * 0.1;
      orbit.pitch += (orbit.targetPitch - orbit.pitch) * 0.1;
      if (
        Math.abs(orbit.yaw - orbit.targetYaw) < 1e-4 &&
        Math.abs(orbit.pitch - orbit.targetPitch) < 1e-4
      ) {
        orbit.yaw = orbit.targetYaw;
        orbit.pitch = orbit.targetPitch;
        orbit.tween = false;
      }
    }
    orbit.radius += (orbit.targetRadius - orbit.radius) * 0.12;
    placeCamera();

    starMat.uniforms.uTime.value = t;
    tileMat.uniforms.uTime.value = t;
    ring.material.opacity = 0.4 + 0.3 * (0.5 + 0.5 * Math.sin(t * 2.2));
    satHalo.rotation.z = t * 0.8;

    renderer.render(scene, camera);
  }
  resize();
  requestAnimationFrame(frame);

  return {
    state,
    setFields,
    setClock,
    setSelection,
    setOptions,
    focus,
    resize,
    ready,
    _debug: { scene, camera, renderer, earth, clouds, tile, ring, beam, trackLine, coverage, sat, atmo },
    visible(lat, lon) {
      const d = lonLatToVec3(lat, lon, 1);
      return d.dot(camPos.value.clone().normalize()) > 0.12;
    },
    selectionScreen() {
      const half = tile.scale.x / 2;
      const c = dirV.clone().multiplyScalar(TILE_R);
      const e = c.clone().addScaledVector(eastV, half);
      const pc = c.project(camera);
      const pe = e.project(camera);
      const w = canvas.clientWidth || 1;
      const h = canvas.clientHeight || 1;
      return {
        x: (pc.x * 0.5 + 0.5) * w,
        y: (-pc.y * 0.5 + 0.5) * h,
        halfPx: Math.abs(pe.x - pc.x) * 0.5 * w,
        behind: dirV.dot(camPos.value.clone().normalize()) < 0.12,
      };
    },
    projectAt(lat, lon, r = EARTH_R * 1.01) {
      const p = lonLatToVec3(lat, lon, r).project(camera);
      return { x: p.x * 0.5 + 0.5, y: -p.y * 0.5 + 0.5, z: p.z };
    },
    project(lat, lon) {
      return this.projectAt(lat, lon, EARTH_R * 1.01);
    },
    dispose() {
      cancelAnimationFrame(raf);
      ro.disconnect();
      renderer.dispose();
    },
  };
}
