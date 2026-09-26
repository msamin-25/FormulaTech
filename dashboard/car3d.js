/* ImpactIQ — procedural F1 car in 3D (Three.js r128, vendored locally).
 * Exposes window.Car3D: init(containerId), highlight(uprightId, colorHex, priority), reset().
 * The four exposed wheels ARE the four uprights (front_left/right, rear_left/right).
 * No external assets — the whole car is built from primitives so it runs offline. */
(function () {
  "use strict";

  const CARBON = 0x141a2b, CARBON2 = 0x1c2540, TRIM = 0x5b8cff, RED = 0xff5470;
  const MATTE = 0x0c0f18, TIRE = 0x0c0c10, RIM = 0xc9d2e6, SILVER = 0x9aa6c4;

  let renderer, scene, camera, carGroup, ground;
  let wheels = {};          // uprightId -> { group, disc, tire, defaultDiscEmissive }
  let ripples = [];         // active ground ripple rings
  let autoRotate = true, dragging = false, lastX = 0, spin = 0.6, container;

  function mat(color, opts) {
    return new THREE.MeshStandardMaterial(Object.assign(
      { color: color, metalness: 0.35, roughness: 0.5 }, opts || {}));
  }
  function box(w, h, d, color, opts) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat(color, opts));
    m.castShadow = true; m.receiveShadow = true; return m;
  }
  function add(parent, mesh, x, y, z) { mesh.position.set(x, y, z); parent.add(mesh); return mesh; }

  // ---- one wheel = one upright (tire + rim + glowing brake disc) ----
  function makeWheel() {
    const g = new THREE.Group();
    const tireGeo = new THREE.CylinderGeometry(0.62, 0.62, 0.5, 40);
    const tire = new THREE.Mesh(tireGeo, mat(TIRE, { roughness: 0.85, metalness: 0.1 }));
    tire.rotation.z = Math.PI / 2; tire.castShadow = true; g.add(tire);

    const rim = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.34, 0.52, 28),
      mat(RIM, { metalness: 0.9, roughness: 0.25 }));
    rim.rotation.z = Math.PI / 2; g.add(rim);

    // brake disc — this is what lights up when the upright is flagged
    const disc = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.28, 0.54, 24),
      new THREE.MeshStandardMaterial({ color: 0x222833, emissive: 0x000000, emissiveIntensity: 1.0, metalness: 0.6, roughness: 0.4 }));
    disc.rotation.z = Math.PI / 2; g.add(disc);

    // spokes for a wheel look
    for (let i = 0; i < 5; i++) {
      const sp = new THREE.Mesh(new THREE.BoxGeometry(0.53, 0.05, 0.28), mat(SILVER, { metalness: 0.8, roughness: 0.3 }));
      sp.rotation.x = (i / 5) * Math.PI;
      g.add(sp);
    }
    return { group: g, disc: disc, tire: tire };
  }

  function buildCar() {
    const car = new THREE.Group();

    // floor / plank
    add(car, box(1.5, 0.08, 6.8, MATTE, { roughness: 0.7 }), 0, 0.16, -0.1);

    // monocoque tub + engine cover
    add(car, box(1.15, 0.5, 2.2, CARBON2, { metalness: 0.4 }), 0, 0.55, 0.55);
    const cover = box(0.85, 0.55, 2.6, CARBON, { metalness: 0.45 });
    cover.position.set(0, 0.66, -1.25); cover.rotation.x = -0.06; car.add(cover);

    // nose
    add(car, box(0.6, 0.32, 1.7, CARBON2), 0, 0.5, 2.05);
    const noseTip = new THREE.Mesh(new THREE.ConeGeometry(0.22, 0.9, 20), mat(RED, { metalness: 0.3 }));
    noseTip.rotation.x = Math.PI / 2; add(car, noseTip, 0, 0.46, 3.2);

    // front wing (two elements + endplates)
    add(car, box(3.1, 0.06, 0.7, MATTE), 0, 0.24, 3.35);
    add(car, box(2.9, 0.05, 0.35, TRIM, { emissive: 0x16305e, emissiveIntensity: 0.4 }), 0, 0.34, 3.5);
    add(car, box(0.05, 0.34, 0.85, MATTE), -1.52, 0.33, 3.4);
    add(car, box(0.05, 0.34, 0.85, MATTE), 1.52, 0.33, 3.4);

    // sidepods with dark intakes
    for (const sx of [-1, 1]) {
      const pod = box(0.62, 0.5, 1.9, CARBON2, { metalness: 0.4 });
      pod.position.set(sx * 0.82, 0.5, 0.2); pod.rotation.y = sx * 0.03; car.add(pod);
      add(car, box(0.2, 0.34, 0.12, 0x05070c), sx * 0.82, 0.55, 1.16); // intake mouth
      add(car, box(0.66, 0.04, 1.9, TRIM, { emissive: 0x16305e, emissiveIntensity: 0.5 }), sx * 0.82, 0.76, 0.2); // trim stripe
    }

    // cockpit recess + headrest + helmet + halo
    add(car, box(0.6, 0.12, 1.0, 0x05070c), 0, 0.82, 0.9);
    add(car, box(0.7, 0.34, 0.35, CARBON), 0, 0.9, 0.15);           // headrest
    const helmet = new THREE.Mesh(new THREE.SphereGeometry(0.19, 24, 18), mat(RED, { metalness: 0.2, roughness: 0.3 }));
    add(car, helmet, 0, 0.95, 0.75);
    const halo = new THREE.Mesh(new THREE.TorusGeometry(0.46, 0.05, 10, 28), mat(MATTE, { metalness: 0.5 }));
    halo.rotation.x = Math.PI / 2; add(car, halo, 0, 1.02, 0.75);
    add(car, box(0.06, 0.1, 0.5, MATTE), 0, 1.02, 1.1);            // halo front strut

    // airbox intake above driver
    const airbox = box(0.4, 0.42, 0.5, CARBON, { metalness: 0.45 });
    airbox.position.set(0, 1.12, 0.02); airbox.rotation.x = 0.15; car.add(airbox);
    add(car, box(0.24, 0.22, 0.1, 0x05070c), 0, 1.16, 0.28);

    // rear wing (pylon, beam, two elements, endplates)
    add(car, box(0.14, 0.9, 0.3, MATTE), 0, 0.95, -2.85);          // pylon
    add(car, box(1.5, 0.05, 0.3, MATTE), 0, 0.72, -3.0);           // beam wing
    add(car, box(2.4, 0.07, 0.6, MATTE), 0, 1.4, -3.0);            // main plane
    add(car, box(2.3, 0.05, 0.32, TRIM, { emissive: 0x16305e, emissiveIntensity: 0.5 }), 0, 1.62, -3.12); // upper flap
    add(car, box(0.06, 0.72, 0.85, MATTE), -1.2, 1.3, -3.0);
    add(car, box(0.06, 0.72, 0.85, MATTE), 1.2, 1.3, -3.0);

    // wheels = uprights
    const positions = {
      front_left:  [-1.52, 0.62, 2.0],
      front_right: [ 1.52, 0.62, 2.0],
      rear_left:   [-1.55, 0.62, -2.4],
      rear_right:  [ 1.55, 0.62, -2.4],
    };
    for (const id in positions) {
      const w = makeWheel();
      const [x, y, z] = positions[id];
      w.group.position.set(x, y, z);
      car.add(w.group);
      wheels[id] = w;
      // suspension arms toward the tub
      const armX = x > 0 ? x - 0.6 : x + 0.6;
      for (const dz of [-0.25, 0.25]) {
        const arm = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, Math.abs(x) - 0.5, 8), mat(MATTE));
        arm.rotation.z = Math.PI / 2; arm.position.set((x + armX) / 2 + (armX - x) / 2, y, z + dz);
        arm.position.x = (x + (x > 0 ? 0.55 : -0.55)) / 2; car.add(arm);
      }
    }

    return car;
  }

  function makeRipple(pos, colorHex) {
    const geo = new THREE.RingGeometry(0.65, 0.8, 40);
    const m = new THREE.MeshBasicMaterial({ color: colorHex, transparent: true, opacity: 0.85, side: THREE.DoubleSide });
    const ring = new THREE.Mesh(geo, m);
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(pos.x, 0.02, pos.z);
    ring.userData.t = 0;
    scene.add(ring); ripples.push(ring);
  }

  function init(containerId) {
    container = document.getElementById(containerId);
    if (!container || typeof THREE === "undefined") return false;

    scene = new THREE.Scene();
    const w = container.clientWidth, h = container.clientHeight || 300;
    camera = new THREE.PerspectiveCamera(42, w / h, 0.1, 100);
    camera.position.set(5.2, 3.6, 6.4);
    camera.lookAt(0, 0.6, -0.1);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0x8fb0ff, 0x0a0e17, 0.7));
    scene.add(new THREE.AmbientLight(0xffffff, 0.25));
    const key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(6, 10, 6); key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.camera.left = -8; key.shadow.camera.right = 8;
    key.shadow.camera.top = 8; key.shadow.camera.bottom = -8;
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x5b8cff, 0.4);
    fill.position.set(-6, 4, -4); scene.add(fill);

    // soft contact shadow on an invisible ground
    ground = new THREE.Mesh(new THREE.PlaneGeometry(30, 30),
      new THREE.ShadowMaterial({ opacity: 0.35 }));
    ground.rotation.x = -Math.PI / 2; ground.position.y = 0; ground.receiveShadow = true;
    scene.add(ground);

    carGroup = buildCar();
    scene.add(carGroup);

    // pointer drag to rotate (pauses auto-rotate while dragging)
    const dom = renderer.domElement;
    dom.style.cursor = "grab";
    dom.addEventListener("pointerdown", e => { dragging = true; lastX = e.clientX; autoRotate = false; dom.style.cursor = "grabbing"; });
    window.addEventListener("pointerup", () => { dragging = false; dom.style.cursor = "grab"; });
    window.addEventListener("pointermove", e => {
      if (!dragging) return;
      carGroup.rotation.y += (e.clientX - lastX) * 0.01; lastX = e.clientX;
    });

    window.addEventListener("resize", onResize);
    if (window.ResizeObserver) new ResizeObserver(onResize).observe(container);

    animate();
    return true;
  }

  function onResize() {
    if (!renderer || !container) return;
    const w = container.clientWidth, h = container.clientHeight || 300;
    camera.aspect = w / h; camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }

  let last = performance.now();
  function animate() {
    requestAnimationFrame(animate);
    const now = performance.now(), dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (autoRotate) carGroup.rotation.y += spin * dt;

    // pulse any highlighted brake discs
    const pulse = 0.6 + 0.4 * Math.sin(now * 0.006);
    for (const id in wheels) {
      const d = wheels[id].disc;
      if (d.userData.on) d.material.emissiveIntensity = 0.8 + pulse * 1.4;
    }

    // expand + fade ripple rings
    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i];
      r.userData.t += dt;
      const s = 1 + r.userData.t * 3.2;
      r.scale.set(s, s, s);
      r.material.opacity = Math.max(0, 0.85 - r.userData.t * 1.1);
      if (r.material.opacity <= 0.01) { scene.remove(r); ripples.splice(i, 1); }
    }
    renderer.render(scene, camera);
  }

  function reset() {
    for (const id in wheels) {
      const w = wheels[id];
      w.disc.material.emissive.setHex(0x000000);
      w.disc.userData.on = false;
      w.tire.material.emissive.setHex(0x000000);
      w.group.scale.set(1, 1, 1);
    }
  }

  function highlight(uprightId, colorHex, priority) {
    reset();
    const w = wheels[uprightId];
    if (!w) return;
    w.disc.material.emissive.setHex(colorHex);
    w.disc.userData.on = true;
    w.tire.material.emissive.setHex(colorHex);
    w.tire.material.emissiveIntensity = 0.25;
    const s = priority === "HIGH" ? 1.12 : 1.0;
    w.group.scale.set(s, s, s);
    makeRipple(w.group.position, colorHex);
  }

  window.Car3D = { init, highlight, reset };
})();
