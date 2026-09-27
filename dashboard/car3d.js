/* ImpactIQ — 3D F1 car stage (Three.js r128, vendored, no external assets).
 *
 * The car is built procedurally: lofted superellipse sections for the nose,
 * monocoque, sidepods and engine cover; NACA-section airfoils for the wings;
 * lathed tyres. The four wheels ARE the four uprights.
 *
 * window.Car3D:
 *   init(containerId, { onCornerClick, reducedMotion }) -> bool
 *   setActive(id, colorHex, priority)   steady glow on the struck corner (idempotent)
 *   impact(id, colorHex, priority)      one-shot FX: sparks, shockwave, camera swing
 *   reset()                             clear glow
 *   setCornerStatus(id, priority, text) update the floating corner tag
 *   setSelected(id)                     selection ring + tag highlight
 */
(function () {
  "use strict";

  const COL = {
    paint: 0xeef1f6, accent: 0x2f6bff, carbon: 0x15181e, dark: 0x06080b,
    tire: 0x0d0e11, rim: 0x2a2f38, silver: 0xb9c2d0,
  };
  const CORNERS = ["front_left", "front_right", "rear_left", "rear_right"];
  const SHORT = { front_left: "FL", front_right: "FR", rear_left: "RL", rear_right: "RR" };

  let renderer, scene, camera, container, layer, clock;
  let car, selRing, opts = {};
  const wheels = {}, tags = {}, ripples = [], sparks = [];
  const target = new THREE.Vector3(0, 0.34, -0.05);
  let theta = -0.6, phi = 1.05, radius = 13, tTheta = 0.85, tPhi = 1.12, tRadius = 7;
  let baseRadius = 7, userZoomed = false;
  let lastInteract = -1e9, dragging = false, moved = false, downX = 0, downY = 0, lastX = 0, lastY = 0;
  let shake = 0, hoverId = null, active = null;
  const raycaster = new THREE.Raycaster(), ndc = new THREE.Vector2(), tmp = new THREE.Vector3();
  const V3 = (x, y, z) => new THREE.Vector3(x, y, z);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const now = () => performance.now();

  // ---------- materials & helpers ----------
  const std = (color, o) => new THREE.MeshStandardMaterial(Object.assign({ color, roughness: 0.45, metalness: 0.2 }, o || {}));
  const phys = (color, o) => new THREE.MeshPhysicalMaterial(Object.assign(
    { color, roughness: 0.3, metalness: 0.12, clearcoat: 1, clearcoatRoughness: 0.07 }, o || {}));
  function mesh(geo, mat) { const m = new THREE.Mesh(geo, mat); m.castShadow = true; m.receiveShadow = true; return m; }

  function rod(a, b, r, mat) {
    const A = V3(...a), B = V3(...b), d = B.clone().sub(A), L = d.length();
    const m = mesh(new THREE.CylinderGeometry(r, r, L, 10), mat);
    m.position.copy(A).add(B).multiplyScalar(0.5);
    m.quaternion.setFromUnitVectors(V3(0, 1, 0), d.normalize());
    return m;
  }

  // Loft superellipse cross-sections (ordered front -> back) into a smooth closed body.
  function loft(sections, o) {
    o = o || {};
    const segs = o.segs || 40, n = o.n || 2.5, e = 2 / n;
    const pos = [], idx = [];
    const ring = (s) => {
      const out = [];
      for (let i = 0; i < segs; i++) {
        const a = (i / segs) * Math.PI * 2, c = Math.cos(a), si = Math.sin(a);
        out.push([(s.x || 0) + Math.sign(c) * Math.pow(Math.abs(c), e) * s.w,
                  s.y + Math.sign(si) * Math.pow(Math.abs(si), e) * s.h, s.z]);
      }
      return out;
    };
    const rings = sections.map(ring);
    rings.forEach(r => r.forEach(p => pos.push(...p)));
    for (let s = 0; s < rings.length - 1; s++) {
      for (let i = 0; i < segs; i++) {
        const a = s * segs + i, b = s * segs + (i + 1) % segs;
        const c = (s + 1) * segs + i, d = (s + 1) * segs + (i + 1) % segs;
        idx.push(a, c, b, b, c, d);
      }
    }
    const cap = (r, front) => {
      const base = pos.length / 3;
      r.forEach(p => pos.push(...p));
      const cx = r.reduce((t, p) => t + p[0], 0) / segs, cy = r.reduce((t, p) => t + p[1], 0) / segs;
      pos.push(cx, cy, r[0][2]);
      const ctr = base + segs;
      for (let i = 0; i < segs; i++) {
        const j = (i + 1) % segs;
        front ? idx.push(ctr, base + i, base + j) : idx.push(ctr, base + j, base + i);
      }
    };
    const caps = o.caps || [true, true];
    if (caps[0]) cap(rings[0], true);
    if (caps[1]) cap(rings[rings.length - 1], false);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }

  // NACA-style section with downforce camber; leading edge at local +x.
  function airfoil(chord, thick, camber) {
    const N = 22, up = [], lo = [];
    for (let i = 0; i <= N; i++) {
      const x = (1 - Math.cos(Math.PI * i / N)) / 2;
      const yt = 5 * thick * (0.2969 * Math.sqrt(x) - 0.126 * x - 0.3516 * x * x + 0.2843 * x ** 3 - 0.1015 * x ** 4);
      const yc = -camber * 4 * x * (1 - x);
      up.push([(1 - x) * chord, (yc + yt) * chord]);
      lo.push([(1 - x) * chord, (yc - yt) * chord]);
    }
    const s = new THREE.Shape();
    s.moveTo(up[0][0], up[0][1]);
    for (let i = 1; i <= N; i++) s.lineTo(up[i][0], up[i][1]);
    for (let i = N; i >= 0; i--) s.lineTo(lo[i][0], lo[i][1]);
    return s;
  }
  function wing(chord, span, aoa, p, mat, thick, camber) {
    const g = new THREE.ExtrudeGeometry(airfoil(chord, thick || 0.1, camber || 0.05), { depth: span, bevelEnabled: false });
    g.translate(-chord / 2, 0, -span / 2);
    const m = mesh(g, mat);
    m.rotation.set(aoa, -Math.PI / 2, 0);
    m.position.set(...p);
    return m;
  }
  // flat plate from a (z, y) side profile, thickness along x
  function plate(pts, thick, mat, x) {
    const s = new THREE.Shape();
    pts.forEach(([z, y], i) => (i ? s.lineTo(z, y) : s.moveTo(z, y)));
    const m = mesh(new THREE.ExtrudeGeometry(s, { depth: thick, bevelEnabled: false }), mat);
    m.rotation.y = -Math.PI / 2;
    m.position.x = x + thick / 2;
    return m;
  }

  function numberTexture(txt, color) {
    const c = document.createElement("canvas"); c.width = c.height = 256;
    const g = c.getContext("2d");
    g.fillStyle = color; g.textAlign = "center"; g.textBaseline = "middle";
    g.font = "800 200px 'Barlow Condensed', 'Arial Narrow', Arial, sans-serif";
    g.fillText(txt, 128, 140);
    const t = new THREE.CanvasTexture(c); t.encoding = THREE.sRGBEncoding; t.anisotropy = 4;
    return t;
  }
  function decal(tex, size, pos, faceSign) {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(size, size),
      new THREE.MeshStandardMaterial({ map: tex, transparent: true, roughness: 0.4, polygonOffset: true, polygonOffsetFactor: -2 }));
    m.position.set(...pos);
    m.rotation.y = faceSign > 0 ? Math.PI / 2 : -Math.PI / 2;
    return m;
  }
  function radialTexture(inner, outer) {
    const c = document.createElement("canvas"); c.width = c.height = 256;
    const g = c.getContext("2d"), gr = g.createRadialGradient(128, 128, 0, 128, 128, 128);
    gr.addColorStop(0, inner); gr.addColorStop(1, outer);
    g.fillStyle = gr; g.fillRect(0, 0, 256, 256);
    const t = new THREE.CanvasTexture(c); t.encoding = THREE.sRGBEncoding;
    return t;
  }

  // ---------- wheel = upright ----------
  function makeWheel(W) {
    const g = new THREE.Group();
    const R = 0.36, Ri = 0.235, hw = W / 2, cr = 0.075, pts = [];
    pts.push(new THREE.Vector2(Ri, -hw + 0.02), new THREE.Vector2(R - cr - 0.02, -hw));
    for (let k = 0; k <= 8; k++) { const a = -Math.PI / 2 + (k / 8) * (Math.PI / 2); pts.push(new THREE.Vector2(R - cr + cr * Math.cos(a), -hw + cr + cr * Math.sin(a))); }
    for (let k = 0; k <= 8; k++) { const a = (k / 8) * (Math.PI / 2); pts.push(new THREE.Vector2(R - cr + cr * Math.cos(a), hw - cr + cr * Math.sin(a))); }
    pts.push(new THREE.Vector2(R - cr - 0.02, hw), new THREE.Vector2(Ri, hw - 0.02));
    const tg = new THREE.LatheGeometry(pts, 64); tg.rotateZ(Math.PI / 2);
    const tireMat = std(COL.tire, { roughness: 0.88, metalness: 0, side: THREE.DoubleSide });
    const tire = mesh(tg, tireMat); g.add(tire);

    const band = new THREE.Mesh(new THREE.TorusGeometry(0.3, 0.006, 8, 72), std(0xe9edf2, { roughness: 0.6 }));
    band.rotation.y = Math.PI / 2; band.position.x = hw + 0.001; g.add(band);

    const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.236, 0.236, W * 0.92, 40, 1, true),
      std(COL.rim, { metalness: 0.85, roughness: 0.35, side: THREE.DoubleSide }));
    barrel.rotation.z = Math.PI / 2; g.add(barrel);

    const face = new THREE.Mesh(new THREE.CircleGeometry(0.235, 48), std(0x1a1e25, { metalness: 0.7, roughness: 0.4 }));
    face.rotation.y = Math.PI / 2; face.position.x = W * 0.3; g.add(face);

    const glowMat = new THREE.MeshStandardMaterial({ color: 0x2a2f38, emissive: 0x000000, emissiveIntensity: 0, metalness: 0.5, roughness: 0.4 });
    const glow = new THREE.Mesh(new THREE.TorusGeometry(0.165, 0.03, 12, 48), glowMat);
    glow.rotation.y = Math.PI / 2; glow.position.x = W * 0.32; g.add(glow);

    const spokeMat = std(COL.silver, { metalness: 0.9, roughness: 0.25 });
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2, s = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.2, 0.035), spokeMat);
      s.position.set(W * 0.36, Math.cos(a) * 0.12, Math.sin(a) * 0.12); s.rotation.x = a; g.add(s);
    }
    const lip = new THREE.Mesh(new THREE.TorusGeometry(0.232, 0.009, 8, 72), std(COL.accent, { metalness: 0.6, roughness: 0.3 }));
    lip.rotation.y = Math.PI / 2; lip.position.x = W * 0.4; g.add(lip);
    const nut = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.05, 6), spokeMat);
    nut.rotation.z = Math.PI / 2; nut.position.x = W * 0.4; g.add(nut);

    const light = new THREE.PointLight(0xffffff, 0, 2.6, 2); light.position.x = W * 0.8; g.add(light);
    return { group: g, tireMat, glowMat, light };
  }

  // ---------- the car ----------
  function buildCar() {
    const c = new THREE.Group();
    const paint = phys(COL.paint), accent = phys(COL.accent, { roughness: 0.26 });
    const carbon = std(0x0c0e12, { roughness: 0.55, metalness: 0.2, envMapIntensity: 0.45 }), dark = std(COL.dark, { roughness: 0.7, metalness: 0.1 });

    // floor
    const fl = [[0.28, 1.35], [0.55, 0.95], [0.78, 0.7], [0.8, -1.25], [0.62, -1.55], [0.5, -2.05],
      [-0.5, -2.05], [-0.62, -1.55], [-0.8, -1.25], [-0.78, 0.7], [-0.55, 0.95], [-0.28, 1.35]];
    const fs = new THREE.Shape(); fl.forEach(([x, z], i) => (i ? fs.lineTo(x, -z) : fs.moveTo(x, -z)));
    const floor = mesh(new THREE.ExtrudeGeometry(fs, { depth: 0.03, bevelEnabled: false }), carbon);
    floor.rotation.x = -Math.PI / 2; floor.position.y = 0.045; c.add(floor);
    const diff = mesh(new THREE.BoxGeometry(1.0, 0.02, 0.55), carbon);
    diff.position.set(0, 0.13, -2.2); diff.rotation.x = 0.28; c.add(diff);

    // nose (accent) + monocoque / engine cover (paint)
    const nose = [
      { z: 2.64, w: 0.045, h: 0.035, y: 0.2 }, { z: 2.45, w: 0.075, h: 0.058, y: 0.215 },
      { z: 2.15, w: 0.1, h: 0.085, y: 0.24 }, { z: 1.8, w: 0.13, h: 0.11, y: 0.28 },
      { z: 1.4, w: 0.17, h: 0.145, y: 0.33 }];
    const body = [
      { z: 1.4, w: 0.17, h: 0.145, y: 0.33 }, { z: 1.0, w: 0.21, h: 0.18, y: 0.38 },
      { z: 0.6, w: 0.25, h: 0.2, y: 0.41 }, { z: 0.2, w: 0.27, h: 0.21, y: 0.42 },
      { z: -0.2, w: 0.27, h: 0.22, y: 0.43 }, { z: -0.6, w: 0.24, h: 0.235, y: 0.45 },
      { z: -1.0, w: 0.2, h: 0.21, y: 0.43 }, { z: -1.4, w: 0.15, h: 0.17, y: 0.39 },
      { z: -1.8, w: 0.11, h: 0.13, y: 0.34 }, { z: -2.15, w: 0.08, h: 0.1, y: 0.3 },
      { z: -2.32, w: 0.055, h: 0.07, y: 0.29 }];
    c.add(mesh(loft(nose, { n: 2.4, caps: [true, false] }), accent));
    c.add(mesh(loft(body, { n: 2.6, caps: [false, true] }), paint));

    // sidepods with dark inlets
    [-1, 1].forEach(s => {
      c.add(mesh(loft([
        { z: 0.48, x: 0.43 * s, w: 0.19, h: 0.15, y: 0.34 }, { z: 0.25, x: 0.45 * s, w: 0.21, h: 0.175, y: 0.35 },
        { z: -0.25, x: 0.41 * s, w: 0.19, h: 0.17, y: 0.34 }, { z: -0.75, x: 0.33 * s, w: 0.13, h: 0.13, y: 0.31 },
        { z: -1.25, x: 0.22 * s, w: 0.05, h: 0.08, y: 0.27 }], { n: 2.8 }), paint));
      const inlet = mesh(new THREE.CircleGeometry(1, 32), dark);
      inlet.scale.set(0.15, 0.11, 1); inlet.position.set(0.43 * s, 0.35, 0.486); c.add(inlet);
    });

    // airbox + intake + T-cam
    c.add(mesh(loft([
      { z: 0.08, w: 0.085, h: 0.095, y: 0.82 }, { z: -0.1, w: 0.11, h: 0.13, y: 0.78 },
      { z: -0.45, w: 0.11, h: 0.12, y: 0.72 }, { z: -0.85, w: 0.075, h: 0.08, y: 0.64 },
      { z: -1.25, w: 0.035, h: 0.04, y: 0.55 }], { n: 2.4 }), paint));
    const intake = mesh(new THREE.CircleGeometry(1, 32), dark);
    intake.scale.set(0.06, 0.07, 1); intake.position.set(0, 0.83, 0.086); c.add(intake);
    const tcam = mesh(new THREE.BoxGeometry(0.12, 0.035, 0.06), accent); tcam.position.set(0, 0.94, -0.05); c.add(tcam);

    // shark fin with race number
    c.add(plate([[-0.35, 0.86], [-1.9, 0.82], [-1.98, 0.72], [-1.98, 0.46], [-1.2, 0.55], [-0.35, 0.8]], 0.012, paint, 0));
    const finNum = numberTexture("07", "#0b0f16");
    c.add(decal(finNum, 0.3, [0.0085, 0.67, -1.35], 1));
    c.add(decal(finNum, 0.3, [-0.0085, 0.67, -1.35], -1));

    // cockpit, driver, halo, mirrors
    const cockpit = mesh(new THREE.SphereGeometry(1, 32, 16), dark);
    cockpit.scale.set(0.19, 0.06, 0.42); cockpit.position.set(0, 0.62, 0.42); c.add(cockpit);
    const helmet = mesh(new THREE.SphereGeometry(0.115, 32, 20), phys(0xfafbff)); helmet.position.set(0, 0.74, 0.26); c.add(helmet);
    const visor = mesh(new THREE.SphereGeometry(0.117, 32, 12, Math.PI / 4, Math.PI / 2, Math.PI * 0.38, Math.PI * 0.16),
      std(0x0a1a3a, { metalness: 0.9, roughness: 0.1 }));
    visor.position.copy(helmet.position); c.add(visor);
    const halo = new THREE.CatmullRomCurve3([V3(-0.24, 0.64, 0.02), V3(-0.23, 0.8, 0.22), V3(-0.14, 0.84, 0.48),
      V3(0, 0.845, 0.56), V3(0.14, 0.84, 0.48), V3(0.23, 0.8, 0.22), V3(0.24, 0.64, 0.02)]);
    c.add(mesh(new THREE.TubeGeometry(halo, 64, 0.028, 12, false), carbon));
    c.add(rod([0, 0.845, 0.56], [0, 0.66, 0.78], 0.022, carbon));
    [-1, 1].forEach(s => {
      c.add(rod([0.22 * s, 0.62, 0.66], [0.38 * s, 0.69, 0.62], 0.012, carbon));
      const m = mesh(new THREE.SphereGeometry(1, 20, 12), paint);
      m.scale.set(0.075, 0.035, 0.035); m.position.set(0.41 * s, 0.7, 0.61); c.add(m);
    });

    // front wing: three elements + endplates + pylons
    c.add(wing(0.3, 1.9, 0.04, [0, 0.085, 2.7], carbon, 0.09, 0.04));
    c.add(wing(0.17, 1.86, 0.32, [0, 0.145, 2.55], carbon, 0.09, 0.05));
    c.add(wing(0.13, 1.8, 0.58, [0, 0.2, 2.47], accent, 0.09, 0.05));
    [-1, 1].forEach(s => {
      c.add(plate([[2.3, 0.03], [2.98, 0.03], [2.98, 0.19], [2.82, 0.3], [2.45, 0.3], [2.3, 0.17]], 0.016, accent, 0.955 * s));
      c.add(rod([0.04 * s, 0.17, 2.52], [0.04 * s, 0.1, 2.62], 0.012, carbon));
    });

    // rear wing: main plane, DRS flap, beam wing, endplates with number, swan necks
    c.add(wing(0.34, 1.02, 0.2, [0, 0.92, -2.28], carbon, 0.11, 0.05));
    c.add(wing(0.22, 1.0, 0.62, [0, 1.03, -2.47], accent, 0.1, 0.05));
    c.add(wing(0.16, 0.8, 0.3, [0, 0.45, -2.3], carbon, 0.1, 0.04));
    const plateNum = numberTexture("07", "#ffffff");
    [-1, 1].forEach(s => {
      c.add(plate([[-2.02, 0.52], [-2.02, 1.02], [-2.16, 1.09], [-2.64, 1.09], [-2.64, 0.6], [-2.36, 0.52]], 0.018, accent, 0.52 * s));
      c.add(decal(plateNum, 0.34, [0.52 * s + 0.011 * s, 0.8, -2.33], s));
      const neck = new THREE.CatmullRomCurve3([V3(0.05 * s, 0.36, -2.08), V3(0.05 * s, 0.72, -2.02), V3(0.05 * s, 1.02, -2.12), V3(0.05 * s, 1.0, -2.26)]);
      c.add(mesh(new THREE.TubeGeometry(neck, 24, 0.016, 8, false), carbon));
    });
    const exhaust = mesh(new THREE.CylinderGeometry(0.035, 0.04, 0.1, 20), std(0x3a3f48, { metalness: 0.9, roughness: 0.3 }));
    exhaust.rotation.x = Math.PI / 2; exhaust.position.set(0, 0.37, -2.34); c.add(exhaust);
    const rain = mesh(new THREE.BoxGeometry(0.1, 0.03, 0.02), new THREE.MeshStandardMaterial({ color: 0x330008, emissive: 0xff2244, emissiveIntensity: 0.6 }));
    rain.position.set(0, 0.28, -2.36); c.add(rain);

    // suspension (wishbones + push/track rods)
    [-1, 1].forEach(s => {
      [[1.75, 0.66, 0.17], [-1.85, 0.6, 0.16]].forEach(([z, xh, xc]) => {
        c.add(rod([xh * s, 0.47, z], [xc * s, 0.47, z + 0.22], 0.013, carbon));
        c.add(rod([xh * s, 0.47, z], [xc * s, 0.47, z - 0.22], 0.013, carbon));
        c.add(rod([xh * s, 0.24, z], [xc * s, 0.22, z + 0.25], 0.013, carbon));
        c.add(rod([xh * s, 0.24, z], [xc * s, 0.22, z - 0.25], 0.013, carbon));
        c.add(rod([xh * s, 0.34, z - 0.08], [xc * s, 0.36, z - 0.1], 0.011, carbon));
      });
    });

    // wheels
    const wp = { front_left: [-0.8, 1.75, 0.3], front_right: [0.8, 1.75, 0.3], rear_left: [-0.79, -1.85, 0.38], rear_right: [0.79, -1.85, 0.38] };
    CORNERS.forEach(id => {
      const [x, z, W] = wp[id], w = makeWheel(W);
      w.group.position.set(x, 0.36, z);
      if (x < 0) w.group.rotation.y = Math.PI;
      w.group.traverse(o => { o.userData.corner = id; });
      w.pos = V3(x, 0.36, z);
      c.add(w.group); wheels[id] = w;
    });
    return c;
  }

  function buildEnv() {
    const env = new THREE.Scene();
    env.add(new THREE.Mesh(new THREE.BoxGeometry(30, 30, 30), new THREE.MeshBasicMaterial({ color: 0x0b0f16, side: THREE.BackSide })));
    const box = (w, h, p, r, k, tint) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
        new THREE.MeshBasicMaterial({ color: new THREE.Color(tint || 0xffffff).multiplyScalar(k), side: THREE.DoubleSide }));
      m.position.set(...p); m.rotation.set(...r); env.add(m);
    };
    box(10, 10, [0, 12, 0], [Math.PI / 2, 0, 0], 3);
    box(3, 12, [12, 4, 0], [0, -Math.PI / 2, 0], 2.2);
    box(3, 12, [-12, 4, 0], [0, Math.PI / 2, 0], 1.6, 0xcfe0ff);
    box(12, 3, [0, 3, -12], [0, 0, 0], 1.2, 0x9fb8ff);
    const pm = new THREE.PMREMGenerator(renderer);
    const tex = pm.fromScene(env, 0.04).texture; pm.dispose();
    return tex;
  }

  function buildStage() {
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(16, 16), new THREE.ShadowMaterial({ opacity: 0.5 }));
    shadow.rotation.x = -Math.PI / 2; shadow.receiveShadow = true; scene.add(shadow);
    const glow = new THREE.Mesh(new THREE.CircleGeometry(4.4, 64), new THREE.MeshBasicMaterial({
      map: radialTexture("rgba(127,168,255,0.16)", "rgba(127,168,255,0)"), transparent: true, depthWrite: false }));
    glow.rotation.x = -Math.PI / 2; glow.position.y = 0.002; scene.add(glow);
    const grid = new THREE.PolarGridHelper(3.9, 16, 5, 96, 0x33415c, 0x1f2939);
    grid.material.transparent = true; grid.material.opacity = 0.4; grid.position.y = 0.003; scene.add(grid);
    const rim = new THREE.Mesh(new THREE.RingGeometry(3.88, 3.93, 128),
      new THREE.MeshBasicMaterial({ color: 0x7fa8ff, transparent: true, opacity: 0.35, side: THREE.DoubleSide }));
    rim.rotation.x = -Math.PI / 2; rim.position.y = 0.004; scene.add(rim);
    selRing = new THREE.Mesh(new THREE.RingGeometry(0.46, 0.5, 64),
      new THREE.MeshBasicMaterial({ color: 0x7fa8ff, transparent: true, opacity: 0.7, side: THREE.DoubleSide, depthWrite: false }));
    selRing.rotation.x = -Math.PI / 2; selRing.position.y = 0.006; selRing.visible = false; scene.add(selRing);
  }

  function buildTags() {
    layer = document.createElement("div"); layer.className = "corner-layer"; container.appendChild(layer);
    CORNERS.forEach(id => {
      const t = document.createElement("button");
      t.type = "button"; t.className = "corner-tag";
      t.setAttribute("aria-label", "Select " + id.replace("_", " ") + " upright");
      t.innerHTML = `<span class="ct-dot"></span><span class="ct-name">${SHORT[id]}</span><span class="ct-status">clear</span>`;
      t.addEventListener("click", () => opts.onCornerClick && opts.onCornerClick(id));
      layer.appendChild(t); tags[id] = t;
    });
  }

  // ---------- interaction ----------
  function pick(e) {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObject(car, true)[0];
    return hit ? hit.object.userData.corner || null : null;
  }
  function bindControls() {
    const dom = renderer.domElement;
    dom.style.cursor = "grab";
    dom.addEventListener("pointerdown", e => {
      dragging = true; moved = false; downX = lastX = e.clientX; downY = lastY = e.clientY;
      lastInteract = now(); dom.setPointerCapture(e.pointerId); dom.style.cursor = "grabbing";
    });
    dom.addEventListener("pointermove", e => {
      if (dragging) {
        tTheta -= (e.clientX - lastX) * 0.0065;
        tPhi = clamp(tPhi - (e.clientY - lastY) * 0.005, 0.45, 1.42);
        lastX = e.clientX; lastY = e.clientY; lastInteract = now();
        if (Math.abs(e.clientX - downX) + Math.abs(e.clientY - downY) > 4) moved = true;
        return;
      }
      const id = pick(e);
      if (id !== hoverId) {
        hoverId = id; dom.style.cursor = id ? "pointer" : "grab";
        CORNERS.forEach(k => tags[k].classList.toggle("hover", k === id));
      }
    });
    const end = e => {
      if (dragging && !moved) { const id = pick(e); if (id && opts.onCornerClick) opts.onCornerClick(id); }
      dragging = false; dom.style.cursor = hoverId ? "pointer" : "grab";
    };
    dom.addEventListener("pointerup", end);
    dom.addEventListener("pointercancel", () => { dragging = false; });
    dom.addEventListener("wheel", e => {
      e.preventDefault(); userZoomed = true;
      tRadius = clamp(tRadius * (1 + Math.sign(e.deltaY) * 0.08), 4.2, 12);
      lastInteract = now();
    }, { passive: false });
  }

  function onResize() {
    if (!renderer) return;
    const w = container.clientWidth, h = container.clientHeight || 420, a = w / h;
    camera.aspect = a; camera.updateProjectionMatrix(); renderer.setSize(w, h);
    baseRadius = a >= 1.6 ? 7.0 : a >= 1.2 ? 7.9 : 9.8;
    if (!userZoomed) tRadius = baseRadius;
  }

  // ---------- FX ----------
  function shockwave(p, color, priority) {
    const n = priority === "HIGH" ? 3 : priority === "MODERATE" ? 2 : 1;
    for (let i = 0; i < n; i++) {
      const r = new THREE.Mesh(new THREE.RingGeometry(0.42, 0.5, 64), new THREE.MeshBasicMaterial({
        color, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }));
      r.rotation.x = -Math.PI / 2; r.position.set(p.x, 0.01, p.z);
      r.userData = { t: -i * 0.16, max: priority === "HIGH" ? 7 : 5 };
      scene.add(r); ripples.push(r);
    }
  }
  function burst(p, color, priority) {
    const N = priority === "HIGH" ? 130 : priority === "MODERATE" ? 70 : 30, side = Math.sign(p.x) || 1;
    const pos = new Float32Array(N * 3), vel = [];
    for (let i = 0; i < N; i++) {
      pos.set([p.x + side * 0.2, 0.3, p.z], i * 3);
      vel.push([side * (0.5 + Math.random() * 2.2), 1 + Math.random() * 2.6, (Math.random() - 0.5) * 2.6]);
    }
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({ color, size: 0.05, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
    pts.userData = { vel, life: 0 };
    scene.add(pts); sparks.push(pts);
  }
  function focusCorner(id) {
    const p = wheels[id].pos;
    let d = Math.atan2(p.x, p.z) - tTheta;
    d = Math.atan2(Math.sin(d), Math.cos(d));
    tTheta += d; tPhi = Math.min(tPhi, 1.2); lastInteract = now();
  }

  // ---------- frame loop ----------
  function updateTags() {
    const w = container.clientWidth, h = container.clientHeight;
    CORNERS.forEach(id => {
      const t = tags[id], p = wheels[id].pos;
      tmp.copy(p); tmp.y += 0.64; tmp.project(camera);
      const x = (tmp.x + 1) / 2 * w, y = (1 - tmp.y) / 2 * h, d = camera.position.distanceTo(p);
      t.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) translate(-50%, -100%)`;
      t.style.opacity = tmp.z < 1 ? String(clamp(1.9 - d / (radius * 0.9), 0.45, 1)) : "0";
      t.style.zIndex = String(Math.round(200 - d * 10));
    });
  }

  function animate() {
    requestAnimationFrame(animate);
    const dt = Math.min(0.05, clock.getDelta()), t = now() / 1000;
    if (!opts.reducedMotion && now() - lastInteract > 6000) tTheta += dt * 0.16;
    const k = Math.min(1, dt * 3.2);
    theta += (tTheta - theta) * k; phi += (tPhi - phi) * k; radius += (tRadius - radius) * k;
    const sp = Math.sin(phi);
    camera.position.set(target.x + radius * sp * Math.sin(theta), target.y + radius * Math.cos(phi), target.z + radius * sp * Math.cos(theta));
    if (shake > 0) {
      camera.position.x += (Math.random() - 0.5) * shake; camera.position.y += (Math.random() - 0.5) * shake;
      shake = Math.max(0, shake - dt * 0.3);
    }
    camera.lookAt(target);

    if (active) {
      const w = wheels[active.id], speed = { HIGH: 7, MODERATE: 4, LOW: 2 }[active.priority] || 2;
      const base = { HIGH: 3.4, MODERATE: 2.3, LOW: 1.3 }[active.priority] || 1.3;
      const s = opts.reducedMotion ? 1 : 0.75 + 0.25 * Math.sin(t * speed);
      w.glowMat.emissiveIntensity = 1.6 * s + 0.4; w.light.intensity = base * s;
    }
    if (selRing.visible) selRing.material.opacity = opts.reducedMotion ? 0.6 : 0.45 + 0.3 * Math.sin(t * 2.2);

    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i]; r.userData.t += dt;
      const u = r.userData.t;
      if (u < 0) continue;
      const s = 1 + u * r.userData.max; r.scale.set(s, s, s);
      r.material.opacity = Math.max(0, 0.9 - u * 0.85);
      if (u > 1.1) { scene.remove(r); r.geometry.dispose(); ripples.splice(i, 1); }
    }
    for (let i = sparks.length - 1; i >= 0; i--) {
      const s = sparks[i], a = s.geometry.attributes.position, v = s.userData.vel;
      s.userData.life += dt;
      for (let j = 0; j < v.length; j++) {
        v[j][1] -= 6 * dt;
        a.array[j * 3] += v[j][0] * dt; a.array[j * 3 + 1] = Math.max(0.01, a.array[j * 3 + 1] + v[j][1] * dt); a.array[j * 3 + 2] += v[j][2] * dt;
      }
      a.needsUpdate = true;
      s.material.opacity = Math.max(0, 1 - s.userData.life / 0.9);
      if (s.userData.life > 0.9) { scene.remove(s); s.geometry.dispose(); sparks.splice(i, 1); }
    }
    updateTags();
    renderer.render(scene, camera);
  }

  // ---------- public API ----------
  function init(containerId, o) {
    container = document.getElementById(containerId);
    if (!container || typeof THREE === "undefined") return false;
    opts = o || {};
    clock = new THREE.Clock();
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(renderer.domElement);

    scene.environment = buildEnv();
    scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x0a0d12, 0.35));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(4, 9, 5); key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, { left: -4.5, right: 4.5, top: 4.5, bottom: -4.5, near: 1, far: 25 });
    key.shadow.bias = -0.0004; key.shadow.normalBias = 0.02;
    scene.add(key);
    const rimL = new THREE.DirectionalLight(0x8fb0ff, 0.8); rimL.position.set(-5, 3, -6); scene.add(rimL);

    buildStage();
    car = buildCar(); scene.add(car);
    buildTags(); bindControls();
    onResize();
    if (window.ResizeObserver) new ResizeObserver(onResize).observe(container);
    else window.addEventListener("resize", onResize);
    if (opts.reducedMotion) { theta = tTheta; phi = tPhi; radius = tRadius; }
    animate();
    return true;
  }

  function clearGlow() {
    CORNERS.forEach(id => {
      const w = wheels[id];
      w.glowMat.emissive.setHex(0x000000); w.glowMat.color.setHex(0x2a2f38); w.glowMat.emissiveIntensity = 0;
      w.tireMat.emissive.setHex(0x000000); w.light.intensity = 0;
    });
  }
  function setActive(id, color, priority) {
    if (!wheels[id]) return;
    if (active && active.id === id && active.color === color) return;
    clearGlow();
    active = { id, color, priority };
    const w = wheels[id];
    w.glowMat.emissive.setHex(color); w.glowMat.color.setHex(color);
    w.light.color.setHex(color);
  }
  function impact(id, color, priority) {
    if (!wheels[id]) return;
    setActive(id, color, priority);
    shockwave(wheels[id].pos, color, priority);
    if (!opts.reducedMotion) {
      burst(wheels[id].pos, color, priority);
      shake = { HIGH: 0.09, MODERATE: 0.045, LOW: 0.015 }[priority] || 0.015;
    }
    focusCorner(id);
  }
  function reset() { clearGlow(); active = null; }
  function setCornerStatus(id, priority, text) {
    const t = tags[id]; if (!t) return;
    t.classList.remove("ct-LOW", "ct-MODERATE", "ct-HIGH");
    if (priority) t.classList.add("ct-" + priority);
    t.querySelector(".ct-status").textContent = text || "clear";
  }
  function setSelected(id) {
    if (!wheels[id]) return;
    CORNERS.forEach(k => tags[k].classList.toggle("selected", k === id));
    selRing.position.x = wheels[id].pos.x; selRing.position.z = wheels[id].pos.z; selRing.visible = true;
  }

  window.Car3D = { init, setActive, impact, reset, setCornerStatus, setSelected };
})();
