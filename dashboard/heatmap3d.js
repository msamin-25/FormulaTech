/* ImpactIQ — real FEA stress field viewer (Three.js r128, vendored).
 * Renders Person 1's SOLIDWORKS node cloud coloured by stress magnitude
 * (blue = low, red = high, like a SOLIDWORKS plot), with the peak node marked.
 * window.Heatmap3D.init(containerId, jsonUrl) -> Promise<meta>. */
(function () {
  "use strict";

  // SOLIDWORKS-style colour ramp: blue -> cyan -> green -> yellow -> red.
  function ramp(t) {
    t = Math.max(0, Math.min(1, t));
    const stops = [
      [0.0, [0.15, 0.30, 0.95]],
      [0.25, [0.0, 0.80, 0.90]],
      [0.5, [0.10, 0.85, 0.20]],
      [0.75, [0.98, 0.85, 0.10]],
      [1.0, [0.95, 0.16, 0.22]],
    ];
    for (let i = 1; i < stops.length; i++) {
      if (t <= stops[i][0]) {
        const [a, ca] = stops[i - 1], [b, cb] = stops[i];
        const f = (t - a) / (b - a || 1);
        return [ca[0] + (cb[0] - ca[0]) * f, ca[1] + (cb[1] - ca[1]) * f, ca[2] + (cb[2] - ca[2]) * f];
      }
    }
    return stops[stops.length - 1][1];
  }

  let renderer, scene, camera, group, container, ripple;
  let autoRotate = true, dragging = false, lastX = 0, lastY = 0;

  function onResize() {
    if (!renderer || !container) return;
    const w = container.clientWidth, h = container.clientHeight || 320;
    camera.aspect = w / h; camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }

  let last = performance.now();
  function animate() {
    requestAnimationFrame(animate);
    const now = performance.now(), dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (autoRotate && group) group.rotation.y += 0.35 * dt;
    if (ripple) {
      const s = 1 + (0.4 + 0.4 * Math.sin(now * 0.004));
      ripple.scale.set(s, s, s);
      ripple.material.opacity = 0.5 + 0.4 * Math.sin(now * 0.004);
    }
    renderer.render(scene, camera);
  }

  function init(containerId, jsonUrl) {
    container = document.getElementById(containerId);
    if (!container || typeof THREE === "undefined") return Promise.reject("no container/THREE");

    scene = new THREE.Scene();
    const w = container.clientWidth, h = container.clientHeight || 320;
    camera = new THREE.PerspectiveCamera(45, w / h, 0.01, 100);
    camera.position.set(2.4, 1.7, 2.6);
    camera.lookAt(0, 0, 0);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    const dom = renderer.domElement;
    dom.style.cursor = "grab";
    dom.addEventListener("pointerdown", e => { dragging = true; lastX = e.clientX; lastY = e.clientY; autoRotate = false; dom.style.cursor = "grabbing"; });
    window.addEventListener("pointerup", () => { dragging = false; dom.style.cursor = "grab"; });
    window.addEventListener("pointermove", e => {
      if (!dragging || !group) return;
      group.rotation.y += (e.clientX - lastX) * 0.01;
      group.rotation.x += (e.clientY - lastY) * 0.01;
      lastX = e.clientX; lastY = e.clientY;
    });
    dom.addEventListener("wheel", e => {
      e.preventDefault();
      const s = 1 + Math.sign(e.deltaY) * 0.08;
      camera.position.multiplyScalar(s);
    }, { passive: false });

    window.addEventListener("resize", onResize);
    if (window.ResizeObserver) new ResizeObserver(onResize).observe(container);

    animate();

    return fetch(jsonUrl, { cache: "no-store" })
      .then(r => { if (!r.ok) throw new Error("no field json"); return r.json(); })
      .then(data => {
        group = new THREE.Group();

        const pos = data.positions, st = data.stress, count = st.length;
        const positions = new Float32Array(pos);
        const colors = new Float32Array(count * 3);
        for (let i = 0; i < count; i++) {
          const [r, g, b] = ramp(st[i]);
          colors[i * 3] = r; colors[i * 3 + 1] = g; colors[i * 3 + 2] = b;
        }
        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
        geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
        const mat = new THREE.PointsMaterial({ size: 0.02, vertexColors: true, sizeAttenuation: true });
        group.add(new THREE.Points(geo, mat));

        // peak marker + pulsing ring
        if (data.peak_pos) {
          const marker = new THREE.Mesh(new THREE.SphereGeometry(0.035, 16, 12),
            new THREE.MeshBasicMaterial({ color: 0xffffff }));
          marker.position.fromArray(data.peak_pos); group.add(marker);
          ripple = new THREE.Mesh(new THREE.RingGeometry(0.05, 0.075, 32),
            new THREE.MeshBasicMaterial({ color: 0xff3040, transparent: true, opacity: 0.7, side: THREE.DoubleSide }));
          ripple.position.fromArray(data.peak_pos);
          ripple.lookAt(camera.position);
          group.add(ripple);
        }

        scene.add(group);
        return data.meta || {};
      });
  }

  window.Heatmap3D = { init, ramp };
})();
