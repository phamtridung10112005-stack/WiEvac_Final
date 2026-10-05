/* VFXEngine — khói billboard, lửa additive, sương mù, còi báo */
(function () {
  "use strict";
  const WEV = (window.WEV = window.WEV || {});
  const THREE = window.THREE;

  function radialTex(stops) {
    const c = document.createElement("canvas");
    c.width = 128;
    c.height = 128;
    const g = c.getContext("2d");
    const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    for (let i = 0; i < stops.length; i++) grd.addColorStop(stops[i][0], stops[i][1]);
    g.fillStyle = grd;
    g.fillRect(0, 0, 128, 128);
    const t = new THREE.CanvasTexture(c);
    t.needsUpdate = true;
    return t;
  }

  class VFXEngine {
    constructor(scene, building) {
      this.scene = scene;
      this.b = building;
      this.smokeN = 420;
      this.flameN = 48;
      this.time = 0;
      this.smoke = this._points(this.smokeN, radialTex([
        [0, "rgba(90,90,95,0.55)"],
        [0.35, "rgba(70,70,75,0.28)"],
        [1, "rgba(40,40,45,0)"]
      ]), false);
      this.flame = this._points(this.flameN, radialTex([
        [0, "rgba(255,236,180,1)"],
        [0.35, "rgba(255,140,20,0.9)"],
        [1, "rgba(180,20,0,0)"]
      ]), true);
      this.parts = [];
      for (let i = 0; i < this.smokeN; i++) this.parts.push(this._reset(i, true));
      this.flames = [];
      for (let i = 0; i < this.flameN; i++) this.flames.push({ life: Math.random(), x: 0, y: 0, z: 0 });
      this.light = new THREE.PointLight(0xff6a1a, 0, 16, 2);
      this.light.castShadow = false;
      scene.add(this.light);
      this.fogDensity = 0.012;
      this.siren = null;
      this.muted = false;
      this.audioCtx = null;
    }

    _points(n, map, additive) {
      const geo = new THREE.BufferGeometry();
      const pos = new Float32Array(n * 3);
      const size = new Float32Array(n);
      const alpha = new Float32Array(n);
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      geo.setAttribute("size", new THREE.BufferAttribute(size, 1));
      geo.setAttribute("alpha", new THREE.BufferAttribute(alpha, 1));
      const mat = new THREE.ShaderMaterial({
        uniforms: { map: { value: map } },
        vertexShader: "attribute float size; attribute float alpha; varying float vAlpha; void main(){ vAlpha = alpha; vec4 mv = modelViewMatrix * vec4(position,1.0); gl_PointSize = size * (280.0 / max(1.0, -mv.z)); gl_Position = projectionMatrix * mv; }",
        fragmentShader: "uniform sampler2D map; varying float vAlpha; void main(){ vec4 tex = texture2D(map, gl_PointCoord); if (tex.a * vAlpha < 0.03) discard; gl_FragColor = vec4(tex.rgb, tex.a * vAlpha); }",
        transparent: true,
        depthWrite: false,
        blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending
      });
      const pts = new THREE.Points(geo, mat);
      pts.frustumCulled = false;
      this.scene.add(pts);
      return { geo: geo, pos: pos, size: size, alpha: alpha, pts: pts };
    }

    _reset(i, randomLife) {
      const f = this.b.fire;
      return {
        x: f.x + (Math.random() - 0.5) * 1.4,
        y: 0.4 + Math.random() * 0.4,
        z: f.z + (Math.random() - 0.5) * 1.2,
        vy: 0.7 + Math.random() * 0.6,
        drift: -0.55 - Math.random() * 0.45,
        life: randomLife ? Math.random() : 0,
        max: 6.5 + Math.random() * 4.5,
        floor: f.floor
      };
    }

    update(dt, player, running) {
      this.time += dt;
      const smoke = this.smoke;
      const off = this.b.offset(this.b.fire.floor);
      const ceiling = 3.35;
      for (let i = 0; i < this.smokeN; i++) {
        const p = this.parts[i];
        if (running) {
          p.life += dt;
          if (p.y < ceiling) p.y += p.vy * dt;
          else {
            p.y = ceiling + Math.sin(this.time * 2 + i) * 0.08;
            p.x += p.drift * dt;
            p.z += Math.sin(this.time + i) * 0.15 * dt;
          }
          if (p.life > p.max || p.x < this.b.fire.x - 28) {
            this.parts[i] = this._reset(i, false);
          }
        }
        const t = p.life / p.max;
        const size = 2.0 + t * 4.2;
        smoke.pos[i * 3] = p.x;
        smoke.pos[i * 3 + 1] = off + p.y;
        smoke.pos[i * 3 + 2] = p.z;
        smoke.size[i] = running ? size : 0;
        smoke.alpha[i] = running ? (1 - t) * 0.85 : 0;
      }
      smoke.geo.attributes.position.needsUpdate = true;
      smoke.geo.attributes.size.needsUpdate = true;
      smoke.geo.attributes.alpha.needsUpdate = true;

      const fl = this.flame;
      for (let i = 0; i < this.flameN; i++) {
        const u = (this.time * 3 + i) % 1;
        const ang = i * 1.7;
        fl.pos[i * 3] = this.b.fire.x + Math.cos(ang) * (0.2 + u * 0.45);
        fl.pos[i * 3 + 1] = off + 0.25 + u * 1.6;
        fl.pos[i * 3 + 2] = this.b.fire.z + Math.sin(ang) * (0.2 + u * 0.35);
        fl.size[i] = running ? (1.1 - u) * 2.4 : 0;
        fl.alpha[i] = running ? (1 - u) : 0;
      }
      fl.geo.attributes.position.needsUpdate = true;
      fl.geo.attributes.size.needsUpdate = true;
      fl.geo.attributes.alpha.needsUpdate = true;

      const flicker = 8 + Math.sin(this.time * 17) * 3 + Math.sin(this.time * 33) * 2;
      this.light.intensity = running ? flicker : 0;
      this.light.position.set(this.b.fire.x, off + 1.4, this.b.fire.z);

      let local = 0;
      if (running && player && player.floor === this.b.fire.floor) {
        for (let i = 0; i < this.smokeN; i += 4) {
          const dx = player.x - this.parts[i].x;
          const dz = player.z - this.parts[i].z;
          const d = dx * dx + dz * dz;
          if (d < 16) local += (16 - d) / 16;
        }
      }
      this.fogDensity = 0.008 + Math.min(0.06, local * 0.004);
      if (this.scene.fog) this.scene.fog.density = this.fogDensity;
    }

    startSiren() {
      if (this.muted) return;
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!this.audioCtx) this.audioCtx = new AC();
      if (this.audioCtx.state === "suspended") this.audioCtx.resume();
      this.stopSiren();
      const ctx = this.audioCtx;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sawtooth";
      osc.frequency.value = 620;
      gain.gain.value = 0.04;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      this.siren = { osc: osc, gain: gain };
      const self = this;
      let up = true;
      this._sirenTimer = setInterval(function () {
        if (!self.siren || self.muted) return;
        up = !up;
        const now = ctx.currentTime;
        osc.frequency.cancelScheduledValues(now);
        osc.frequency.linearRampToValueAtTime(up ? 1180 : 560, now + 0.45);
      }, 500);
    }

    stopSiren() {
      if (this._sirenTimer) clearInterval(this._sirenTimer);
      this._sirenTimer = null;
      if (this.siren) {
        try { this.siren.osc.stop(); } catch (e) { /* already */ }
        this.siren = null;
      }
    }

    setMuted(m) {
      this.muted = m;
      if (m) this.stopSiren();
    }

    speak(text) {
      if (this.muted || !window.speechSynthesis) return;
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "vi-VN";
      u.rate = 1.02;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(u);
    }
  }

  WEV.VFXEngine = VFXEngine;
})();
