/* CrowdEngine — Social Force, tách người, diễn hoạt chi */
(function () {
  "use strict";
  const WEV = (window.WEV = window.WEV || {});
  const THREE = window.THREE;

  function hangCyl(r0, r1, h, seg) {
    const g = new THREE.CylinderGeometry(r0, r1, h, seg || 6);
    g.translate(0, -h / 2, 0);
    return g;
  }

  WEV.makeAvatar = function (opts) {
    opts = opts || {};
    const skin = new THREE.MeshStandardMaterial({ color: opts.skin || 0xf0c7a4, roughness: 0.62 });
    const hair = new THREE.MeshStandardMaterial({ color: opts.hair || 0x3f2a1d, roughness: 0.8 });
    const shirt = new THREE.MeshStandardMaterial({ color: opts.shirt || 0xf59e0b, roughness: 0.58 });
    const pants = new THREE.MeshStandardMaterial({ color: opts.pants || 0x1e293b, roughness: 0.78 });
    const shoeM = new THREE.MeshStandardMaterial({ color: 0x1c1917, roughness: 0.7 });
    const eyeM = new THREE.MeshStandardMaterial({ color: 0x1c1917, roughness: 0.4 });
    const g = new THREE.Group();
    const torso = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.17, 0.48, 8), shirt);
    torso.position.y = 1.12;
    torso.castShadow = true;
    const hips = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.15, 0.2, 8), pants);
    hips.position.y = 0.8;
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.125, 12, 10), skin);
    head.position.y = 1.56;
    head.castShadow = true;
    const hairM = new THREE.Mesh(new THREE.SphereGeometry(0.132, 10, 8), hair);
    hairM.position.set(0, 1.64, -0.01);
    hairM.scale.set(1.02, 0.7, 1.05);
    const eyeGeo = new THREE.SphereGeometry(0.016, 6, 6);
    const eyeL = new THREE.Mesh(eyeGeo, eyeM);
    eyeL.position.set(0.042, 1.58, 0.1);
    const eyeR = new THREE.Mesh(eyeGeo, eyeM);
    eyeR.position.set(-0.042, 1.58, 0.1);
    function pivot(geo, mat, x, y) {
      const p = new THREE.Group();
      p.position.set(x, y, 0);
      p.add(new THREE.Mesh(geo, mat));
      g.add(p);
      return p;
    }
    const legGeo = hangCyl(0.055, 0.048, 0.72, 6);
    const armGeo = hangCyl(0.04, 0.034, 0.52, 6);
    const shoeGeo = new THREE.BoxGeometry(0.09, 0.05, 0.16);
    shoeGeo.translate(0, -0.74, 0.03);
    const legL = pivot(legGeo, pants, -0.08, 0.78);
    const legR = pivot(legGeo, pants, 0.08, 0.78);
    const armL = pivot(armGeo, shirt, 0.18, 1.32);
    const armR = pivot(armGeo, shirt, -0.18, 1.32);
    const shoeL = pivot(shoeGeo, shoeM, -0.08, 0.78);
    const shoeR = pivot(shoeGeo, shoeM, 0.08, 0.78);
    g.add(torso, hips, head, hairM, eyeL, eyeR);
    g.userData.limbs = { legL: legL, legR: legR, armL: armL, armR: armR, shoeL: shoeL, shoeR: shoeR, phase: 0 };
    return g;
  };

  class CrowdEngine {
    constructor(scene, building, count) {
      this.scene = scene;
      this.building = building;
      this.agents = [];
      this.cell = 1.4;
      this.buckets = new Map();
      this.dummy = new THREE.Object3D();
      this.jamCount = 0;
      this._buildMeshes(count || 200);
      this._spawn(count || 200);
    }

    _buildMeshes(n) {
      const skin = new THREE.MeshStandardMaterial({ color: 0xf0c7a4, roughness: 0.62 });
      const hair = new THREE.MeshStandardMaterial({ color: 0x1f2937, roughness: 0.8 });
      const pants = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.78 });
      const shirt = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.6 });
      const shoe = new THREE.MeshStandardMaterial({ color: 0x111827, roughness: 0.7 });
      const shoeGeo = new THREE.BoxGeometry(0.09, 0.05, 0.16);
      shoeGeo.translate(0, -0.74, 0.03);
      this.shirt = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.15, 0.17, 0.46, 7), shirt, n);
      this.head = new THREE.InstancedMesh(new THREE.SphereGeometry(0.12, 10, 8), skin, n);
      this.hair = new THREE.InstancedMesh(new THREE.SphereGeometry(0.126, 8, 6), hair, n);
      this.pants = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.14, 0.15, 0.2, 7), pants, n);
      this.legL = new THREE.InstancedMesh(hangCyl(0.052, 0.045, 0.7, 5), pants, n);
      this.legR = new THREE.InstancedMesh(hangCyl(0.052, 0.045, 0.7, 5), pants, n);
      this.armL = new THREE.InstancedMesh(hangCyl(0.038, 0.032, 0.5, 5), shirt, n);
      this.armR = new THREE.InstancedMesh(hangCyl(0.038, 0.032, 0.5, 5), shirt, n);
      this.shoeL = new THREE.InstancedMesh(shoeGeo, shoe, n);
      this.shoeR = new THREE.InstancedMesh(shoeGeo.clone(), shoe, n);
      this.parts = [this.shirt, this.head, this.hair, this.pants, this.legL, this.legR, this.armL, this.armR, this.shoeL, this.shoeR];
      for (let i = 0; i < this.parts.length; i++) {
        this.parts[i].castShadow = i < 2;
        this.parts[i].frustumCulled = false;
        this.scene.add(this.parts[i]);
      }
      this.shirt.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);
      this.armL.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);
      this.armR.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);
      this.head.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);
      this.hair.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);
      this.pants.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);
      this.capacity = n;
      this.shirtColors = ["#2563eb", "#0f766e", "#b45309", "#7c3aed", "#be123c", "#0369a1", "#15803d", "#9a3412"];
      this.skinColors = ["#f3d2b5", "#e0ac7a", "#c68642", "#8d5524"];
      this.hairColors = ["#1c1917", "#3f2a1d", "#78350f", "#111827"];
      this.pantColors = ["#1e293b", "#334155", "#3f3f46", "#1c1917"];
    }

    _spawn(n) {
      const quotas = [
        { floor: 4, n: 70, role: "upper" },
        { floor: 3, n: 70, role: "upper" },
        { floor: 2, n: 110, role: "other" },
        { floor: 1, n: 48, role: "other" },
        { floor: 0, n: 36, role: "other" }
      ];
      const b = this.building;
      let id = 0;
      const push = (floor, x, z, role, hero) => {
        if (id >= this.capacity) return false;
        const cell = b.nearestWalk(floor, x, z);
        const wx = WEV.GEO.cellX(cell.ix) + 0.5;
        const wz = WEV.GEO.cellZ(cell.iz) + 0.5;
        if (Math.abs(wx - x) > 4 || Math.abs(wz - z) > 4) return false;
        if (b.physics && b.physics.overlaps(floor, wx, wz, 0.28)) return false;
        const speedRoll = Math.random();
        let speed = 1.25;
        if (speedRoll > 0.72) speed = 2.2 + Math.random() * 0.6;
        else if (speedRoll > 0.35) speed = 1.55 + Math.random() * 0.35;
        else speed = 1.15 + Math.random() * 0.25;
        this.agents.push({
          id: id, floor: floor, x: wx, z: wz, y: 0.3,
          vx: 0, vz: 0, speed: speed, role: role,
          alive: true, exited: false, phase: Math.random() * 6.28,
          yaw: Math.random() * 6.28, radius: 0.28, stuck: false, panic: 0,
          hero: !!hero
        });
        const shirt = new THREE.Color(role === "party" ? "#38bdf8" : this.shirtColors[id % this.shirtColors.length]);
        const skin = new THREE.Color(this.skinColors[id % this.skinColors.length]);
        const hairC = new THREE.Color(this.hairColors[(id * 3) % this.hairColors.length]);
        const pant = new THREE.Color(this.pantColors[(id * 5) % this.pantColors.length]);
        this.shirt.setColorAt(id, shirt);
        this.armL.setColorAt(id, shirt);
        this.armR.setColorAt(id, shirt);
        this.head.setColorAt(id, skin);
        this.hair.setColorAt(id, hairC);
        this.pants.setColorAt(id, pant);
        id++;
        return true;
      };
      const rooms = b.roomList || [];
      for (let q = 0; q < quotas.length; q++) {
        let placed = 0;
        let guard = 0;
        while (placed < quotas[q].n && guard < 6000) {
          guard++;
          let x;
          let z;
          if (rooms.length && Math.random() < 0.7) {
            const r = rooms[(Math.random() * rooms.length) | 0];
            const door = r.door;
            if (door) {
              x = door.x + (Math.random() - 0.5) * 2.4;
              z = door.z + (Math.random() - 0.5) * 2.2;
            } else {
              x = r.x0 + 1 + Math.random() * Math.max(1, r.x1 - r.x0 - 2);
              z = r.z0 + 1 + Math.random() * Math.max(1, r.z1 - r.z0 - 2);
            }
          } else {
            x = -46 + Math.random() * 92;
            z = -27 + Math.random() * 54;
          }
          if (push(quotas[q].floor, x, z, quotas[q].role)) placed++;
        }
      }
      const mouths = b.mouths || [];
      const clusters = [
        { id: "SE", n: 26, role: "other" },
        { id: "NE", n: 18, role: "upper" },
        { id: "NW", n: 22, role: "upper" }
      ];
      for (let c = 0; c < clusters.length; c++) {
        let mouth = null;
        for (let m = 0; m < mouths.length; m++) if (mouths[m].id === clusters[c].id) mouth = mouths[m];
        if (!mouth) continue;
        const outZ = mouth.id === "SE" || mouth.id === "SW" ? -1.6 : 1.6;
        const floors = [];
        for (const key in mouth.floors) if (+key > 0) floors.push(+key);
        if (!floors.length) floors.push(2);
        for (let i = 0; i < clusters[c].n; i++) {
          const f = floors[i % floors.length];
          push(f, mouth.x + (Math.random() - 0.5) * 2.4, mouth.z + outZ + (Math.random() - 0.5) * 1.4, clusters[c].role, true);
        }
      }
      const sp = b.spawn;
      for (let i = 0; i < 8 && id < this.capacity; i++) {
        push(sp.floor, sp.x + (i - 3.5) * 0.7, sp.z + 1.2, "party", true);
      }
      while (id < Math.min(n, this.capacity)) {
        push(2, 20 + Math.random() * 18, 12 + Math.random() * 6, "other");
      }
      for (let p = 0; p < this.parts.length; p++) {
        this.parts[p].count = id;
        if (this.parts[p].instanceColor) this.parts[p].instanceColor.needsUpdate = true;
      }
    }

    _roomDoor(agent) {
      const G = WEV.GEO;
      const ix = Math.floor(agent.x - G.OX);
      const iz = Math.floor(agent.z - G.OZ);
      if (ix < 0 || iz < 0 || ix >= G.GW || iz >= G.GH) return null;
      const rid = this.building.roomId[agent.floor][G.idx(ix, iz)];
      if (rid < 0) return null;
      const room = this.building.roomList[rid];
      if (!room || !room.door) return null;
      return room.door;
    }

    _hash() {
      this.buckets.clear();
      const c = this.cell;
      for (let i = 0; i < this.agents.length; i++) {
        const a = this.agents[i];
        if (!a.alive) continue;
        const k = Math.floor(a.x / c) + "," + Math.floor(a.z / c) + ":" + a.floor;
        let arr = this.buckets.get(k);
        if (!arr) { arr = []; this.buckets.set(k, arr); }
        arr.push(a);
      }
    }

    _neighbors(a) {
      const c = this.cell;
      const ix = Math.floor(a.x / c);
      const iz = Math.floor(a.z / c);
      const out = [];
      for (let dz = -1; dz <= 1; dz++) {
        for (let dx = -1; dx <= 1; dx++) {
          const arr = this.buckets.get((ix + dx) + "," + (iz + dz) + ":" + a.floor);
          if (arr) for (let i = 0; i < arr.length; i++) if (arr[i] !== a) out.push(arr[i]);
        }
      }
      return out;
    }

    separateOne(ent, radius) {
      this._hash();
      const fake = { x: ent.x, z: ent.z, floor: ent.floor, alive: true };
      const list = this._neighbors(fake);
      for (let i = 0; i < list.length; i++) {
        const o = list[i];
        let dx = ent.x - o.x;
        let dz = ent.z - o.z;
        let d = Math.hypot(dx, dz);
        const min = radius + o.radius;
        if (d < 0.0001) { dx = 0.02; dz = 0.02; d = 0.03; }
        if (d < min) {
          const push = (min - d) * 0.65;
          ent.x += (dx / d) * push;
          ent.z += (dz / d) * push;
        }
      }
    }

    stairLoads() {
      const loads = { SE: 0, SW: 0, NE: 0, NW: 0 };
      const mouths = this.building.mouths || [];
      for (let i = 0; i < this.agents.length; i++) {
        const a = this.agents[i];
        if (!a.alive) continue;
        for (let m = 0; m < mouths.length; m++) {
          const mouth = mouths[m];
          if (mouth.floors && !mouth.floors[a.floor]) continue;
          if (Math.hypot(a.x - mouth.x, a.z - mouth.z) < 4.4) loads[mouth.id]++;
        }
      }
      let jam = 0;
      for (const k in loads) if (loads[k] > jam) jam = loads[k];
      this.jamCount = jam;
      this.loads = loads;
      return loads;
    }

    update(dt, physics, fieldFn, running) {
      this._hash();
      const loads = this.stairLoads();
      const mouths = this.building.mouths || [];

      for (let i = 0; i < this.agents.length; i++) {
        const a = this.agents[i];
        if (!a.alive) continue;
        let desiredX = 0;
        let desiredZ = 0;
        let spd = 0;
        if (running && fieldFn) {
          const door = this._roomDoor(a);
          const atDoor = door && Math.hypot(door.x - a.x, door.z - a.z) < 1.15;
          const step = (!door || atDoor) ? fieldFn(a) : door;
          if (step) {
            const tx = step.x - a.x;
            const tz = step.z - a.z;
            const mag = Math.hypot(tx, tz) || 1;
            desiredX = tx / mag;
            desiredZ = tz / mag;
            spd = a.speed;
          }
          a._aim = step;
        }
        let nearId = null;
        let nearD = 4.4;
        for (let m = 0; m < mouths.length; m++) {
          const mouth = mouths[m];
          if (mouth.floors && !mouth.floors[a.floor]) continue;
          const d = Math.hypot(a.x - mouth.x, a.z - mouth.z);
          if (d < nearD) { nearD = d; nearId = mouth.id; }
        }
        const crowdN = nearId ? loads[nearId] : 0;
        if (nearId && crowdN > 16) {
          spd *= crowdN > 30 ? 0.08 : 0.3;
          a.stuck = crowdN > 30;
        } else a.stuck = false;
        if (a.exited) spd = 0;

        let fx = desiredX * spd;
        let fz = desiredZ * spd;
        const neigh = this._neighbors(a);
        for (let k = 0; k < neigh.length; k++) {
          const o = neigh[k];
          let dx = a.x - o.x;
          let dz = a.z - o.z;
          let d = Math.hypot(dx, dz);
          if (d < 0.001) { dx = 0.01; dz = 0; d = 0.01; }
          const reach = 0.85;
          if (d < reach) {
            const f = (reach - d) * 6.5;
            fx += (dx / d) * f;
            fz += (dz / d) * f;
          }
        }
        const vlen = Math.hypot(fx, fz);
        const cap = Math.max(spd, 0.4) * 1.15;
        if (vlen > cap) { fx = fx / vlen * cap; fz = fz / vlen * cap; }
        a.vx = a.vx * 0.55 + fx * 0.45;
        a.vz = a.vz * 0.55 + fz * 0.45;
        const prevX = a.x;
        const prevZ = a.z;
        const moved = physics.move(a.floor, a.x, a.z, a.radius, a.vx * dt, a.vz * dt);
        a.x = moved.x;
        a.z = moved.z;
        if (!a.anchor) a.anchor = { x: a.x, z: a.z, t: 0 };
        if (Math.hypot(a.x - a.anchor.x, a.z - a.anchor.z) < 0.06) a.anchor.t += dt;
        else { a.anchor.x = a.x; a.anchor.z = a.z; a.anchor.t = 0; }
        if (running && a.anchor.t > 0.6 && a._aim) {
          const dx = a._aim.x - a.x;
          const dz = a._aim.z - a.z;
          const len = Math.hypot(dx, dz) || 1;
          const side = (a.id % 2 === 0 ? 1 : -1);
          const nudge = physics.move(a.floor, a.x, a.z, a.radius, (-dz / len) * 0.35 * side + (dx / len) * 0.2, (dx / len) * 0.35 * side + (dz / len) * 0.2);
          if (Math.hypot(nudge.x - a.x, nudge.z - a.z) > 0.04) {
            a.x = nudge.x;
            a.z = nudge.z;
          }
          a.anchor.t = 0;
          a.anchor.x = a.x;
          a.anchor.z = a.z;
        }
        void prevX;
        void prevZ;
      }

      this._hash();
      for (let iter = 0; iter < 2; iter++) {
        for (let i = 0; i < this.agents.length; i++) {
          const a = this.agents[i];
          if (!a.alive) continue;
          const neigh = this._neighbors(a);
          for (let k = 0; k < neigh.length; k++) {
            const o = neigh[k];
            if (o.id < a.id) continue;
            let dx = a.x - o.x;
            let dz = a.z - o.z;
            let d = Math.hypot(dx, dz);
            const min = a.radius + o.radius;
            if (d < 0.0001) { dx = 0.02; dz = 0.01; d = 0.022; }
            if (d < min) {
              const push = (min - d) * 0.5;
              const ux = dx / d;
              const uz = dz / d;
              a.x += ux * push;
              a.z += uz * push;
            o.x -= ux * push;
            o.z -= uz * push;
            if (physics.overlaps(a.floor, a.x, a.z, a.radius * 0.8)) { a.x -= ux * push; a.z -= uz * push; }
            if (physics.overlaps(o.floor, o.x, o.z, o.radius * 0.8)) { o.x += ux * push; o.z += uz * push; }
            }
          }
        }
      }

      const asm = this.building.assembly;
      for (let i = 0; i < this.agents.length; i++) {
        const a = this.agents[i];
        if (!a.alive || a.exited) continue;
        const y = physics.sampleY(a.floor, a.x, a.z);
        if (a.floor > 0 && y < -3.45) {
          a.floor -= 1;
          a.y = 0.3;
        } else {
          a.y += (y - a.y) * Math.min(1, dt * 10);
        }
        if (a.floor === 0 && Math.hypot(a.x - asm.x, a.z - asm.z) < asm.r) {
          a.exited = true;
          a.alive = false;
        }
        const sp = Math.hypot(a.vx, a.vz);
        if (sp > 0.15) a.yaw = Math.atan2(a.vx, a.vz);
        a.phase += dt * sp * 3.2;
      }
      this._syncWalkers(dt, running);
      this._draw();
    }

    attachWalkers() {
      if (!WEV.makeWalker) return;
      this.walkers = [];
      this._walkerAcc = 1;
      for (let i = 0; i < 56; i++) {
        const walker = WEV.makeWalker();
        walker.agent = null;
        walker.root.visible = false;
        this.scene.add(walker.root);
        this.walkers.push(walker);
      }
      for (let p = 0; p < this.parts.length; p++) this.parts[p].visible = false;
    }

    _retargetWalkers() {
      const player = this.focusEnt;
      const ranked = [];
      for (let i = 0; i < this.agents.length; i++) {
        const a = this.agents[i];
        if (!a.alive) continue;
        let score = i * 0.01;
        if (player) {
          const same = a.floor === player.floor ? 0 : 50;
          score = Math.hypot(a.x - player.x, a.z - player.z) + same;
        }
        ranked.push({ a: a, score: score });
      }
      ranked.sort(function (p, q) { return p.score - q.score; });
      const n = Math.min(this.walkers.length, ranked.length);
      for (let i = 0; i < this.walkers.length; i++) this.walkers[i].agent = i < n ? ranked[i].a : null;
    }

    _syncWalkers(dt, running) {
      if (!this.walkers) return;
      this._walkerAcc += dt;
      if (this._walkerAcc > 0.4) {
        this._walkerAcc = 0;
        this._retargetWalkers();
      }
      const focus = this.building.layoutMode === "single" ? this.building.focus : -1;
      for (let i = 0; i < this.walkers.length; i++) {
        const w = this.walkers[i];
        const a = w.agent;
        if (!a || !a.alive) {
          w.root.visible = false;
          continue;
        }
        w.root.visible = focus < 0 || a.floor === focus;
        w.root.position.set(a.x, this.building.offset(a.floor) + a.y, a.z);
        w.root.rotation.y = a.yaw;
        const sp = Math.hypot(a.vx, a.vz);
        w.setMove(!running || sp < 0.2 ? "idle" : sp > 2.2 ? "run" : "walk");
        w.mixer.update(dt);
      }
    }

    _draw() {
      if (this.walkers) return;
      const off = this.building.offset.bind(this.building);
      const d = this.dummy;
      const focus = this.building.layoutMode === "single" ? this.building.focus : -1;
      for (let i = 0; i < this.agents.length; i++) {
        const a = this.agents[i];
        const hide = !a.alive || a.heroMesh || (focus >= 0 && a.floor !== focus);
        const y = off(a.floor) + a.y;
        const sp = Math.hypot(a.vx, a.vz);
        const swing = hide ? 0 : Math.sin(a.phase) * Math.min(0.7, sp * 0.32);
        const sL = Math.sin(a.yaw);
        const cL = Math.cos(a.yaw);
        const set = (mesh, x, yy, z, rx, sy) => {
          if (hide) d.scale.set(0, 0, 0);
          else d.scale.set(1, sy || 1, 1);
          d.position.set(x, yy, z);
          d.rotation.set(rx, a.yaw, 0);
          d.updateMatrix();
          mesh.setMatrixAt(a.id, d.matrix);
        };
        set(this.shirt, a.x, y + 1.12, a.z, 0);
        set(this.head, a.x, y + 1.56, a.z, 0);
        set(this.hair, a.x, y + 1.64, a.z, 0, 0.72);
        set(this.pants, a.x, y + 0.8, a.z, 0);
        const hip = y + 0.78;
        set(this.legL, a.x - cL * 0.08, hip, a.z + sL * 0.08, swing);
        set(this.legR, a.x + cL * 0.08, hip, a.z - sL * 0.08, -swing);
        set(this.armL, a.x + cL * 0.18, y + 1.32, a.z - sL * 0.18, -swing);
        set(this.armR, a.x - cL * 0.18, y + 1.32, a.z + sL * 0.18, swing);
        set(this.shoeL, a.x - cL * 0.08, hip, a.z + sL * 0.08, swing);
        set(this.shoeR, a.x + cL * 0.08, hip, a.z - sL * 0.08, -swing);
      }
      for (let p = 0; p < this.parts.length; p++) this.parts[p].instanceMatrix.needsUpdate = true;
    }

    counts() {
      let inside = 0;
      let out = 0;
      for (let i = 0; i < this.agents.length; i++) {
        if (this.agents[i].exited) out++;
        else if (this.agents[i].alive) inside++;
      }
      return { inside: inside, out: out, jam: this.jamCount };
    }
  }

  WEV.CrowdEngine = CrowdEngine;
})();
