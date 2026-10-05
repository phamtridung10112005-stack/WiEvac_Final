/* WiEvacEngine — lưới A*, RF-CSI tắc nghẽn, vạch dẫn & LED */
(function () {
  "use strict";
  const WEV = (window.WEV = window.WEV || {});
  const THREE = window.THREE;
  const G = function () { return WEV.GEO; };

  class WiEvacEngine {
    constructor(scene, building) {
      this.scene = scene;
      this.b = building;
      this.seBlocked = false;
      this.closed = {};
      this.loads = {};
      this.swOpen = false;
      this.next = null;
      this.mode = "east";
      this.guide = [];
      this.routeLen = 0;
      this.line = null;
      this._geom = null;
      this.recompute();
    }

    recompute() {
      const b = this.b;
      const geo = G();
      const floors = geo.LEVELS;
      const size = geo.GW * geo.GH;
      const dist = [];
      const next = [];
      for (let f = 0; f < floors; f++) {
        dist.push(new Float32Array(size));
        next.push(new Int32Array(size));
        dist[f].fill(1e9);
        next[f].fill(-1);
      }
      const heap = [];
      const push = (cost, f, i) => {
        heap.push({ cost: cost, f: f, i: i });
        let k = heap.length - 1;
        while (k > 0) {
          const p = (k - 1) >> 1;
          if (heap[p].cost <= heap[k].cost) break;
          const tmp = heap[p]; heap[p] = heap[k]; heap[k] = tmp;
          k = p;
        }
      };
      const pop = () => {
        const top = heap[0];
        const last = heap.pop();
        if (heap.length && last) {
          heap[0] = last;
          let k = 0;
          for (;;) {
            const l = k * 2 + 1;
            const r = l + 1;
            let m = k;
            if (l < heap.length && heap[l].cost < heap[m].cost) m = l;
            if (r < heap.length && heap[r].cost < heap[m].cost) m = r;
            if (m === k) break;
            const tmp = heap[k]; heap[k] = heap[m]; heap[m] = tmp;
            k = m;
          }
        }
        return top;
      };

      const ixA = Math.max(0, Math.min(geo.GW - 1, Math.floor(b.assembly.x - geo.OX)));
      const izA = Math.max(0, Math.min(geo.GH - 1, Math.floor(b.assembly.z - geo.OZ)));
      for (let dz = -2; dz <= 2; dz++) {
        for (let dx = -2; dx <= 2; dx++) {
          const x = ixA + dx;
          const z = izA + dz;
          if (x < 0 || z < 0 || x >= geo.GW || z >= geo.GH) continue;
          const ai = geo.idx(x, z);
          if (!b.grid[0][ai]) continue;
          dist[0][ai] = 0;
          push(0, 0, ai);
        }
      }
      const mouths = b.mouths || [];
      for (let m = 0; m < mouths.length; m++) {
        const mouth = mouths[m];
        if (this.closed[mouth.id]) continue;
        const gx = Math.max(0, Math.min(geo.GW - 1, Math.floor(mouth.gx - geo.OX)));
        const gz = Math.max(0, Math.min(geo.GH - 1, Math.floor(mouth.gz - geo.OZ)));
        const gi = geo.idx(gx, gz);
        const floors = mouth.floors || {};
        for (const key in floors) {
          const f = +key;
          if (f <= 0 || !b.grid[f] || !b.grid[f][gi]) continue;
          dist[f][gi] = 0;
          push(0, f, gi);
        }
      }

      const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1]];
      while (heap.length) {
        const cur = pop();
        if (!cur || cur.cost > dist[cur.f][cur.i] + 0.01) continue;
        const ix = cur.i % geo.GW;
        const iz = (cur.i / geo.GW) | 0;
        for (let d = 0; d < 4; d++) {
          const nx = ix + dirs[d][0];
          const nz = iz + dirs[d][1];
          if (!b.canStep(cur.f, ix, iz, nx, nz)) continue;
          const ni = geo.idx(nx, nz);
          let step = 1;
          if (this.seBlocked && cur.f === b.fire.floor) {
            const wx = geo.cellX(nx) + 0.5;
            const wz = geo.cellZ(nz) + 0.5;
            if (Math.hypot(wx - b.fire.x, wz - b.fire.z) < 7) step = 6;
          }
          const nd = cur.cost + step;
          if (nd < dist[cur.f][ni]) {
            dist[cur.f][ni] = nd;
            next[cur.f][ni] = cur.i;
            push(nd, cur.f, ni);
          }
        }
        for (let p = 0; p < b.portals.length; p++) {
          const portal = b.portals[p];
          if (this.closed[portal.id] || portal.id === "EX") continue;
          if (portal.down !== cur.f) continue;
          const pi = geo.idx(portal.ix, portal.iz);
          if (Math.abs(ix - portal.ix) + Math.abs(iz - portal.iz) > 3) continue;
          const extra = portal.id === "SE" ? 5 : 12;
          const nd = cur.cost + extra;
          if (nd < dist[portal.up][pi]) {
            dist[portal.up][pi] = nd;
            next[portal.up][pi] = pi;
            push(nd, portal.up, pi);
          }
        }
      }

      this.dist = dist;
      this.next = next;
      this.mode = this.seBlocked ? "west" : "east";
    }

    _linkDown(dist, next) {
      const b = this.b;
      const geo = G();
      const heap = [];
      const push = (cost, f, i) => {
        heap.push({ cost: cost, f: f, i: i });
        let k = heap.length - 1;
        while (k > 0) {
          const p = (k - 1) >> 1;
          if (heap[p].cost <= heap[k].cost) break;
          const tmp = heap[p]; heap[p] = heap[k]; heap[k] = tmp;
          k = p;
        }
      };
      for (let f = 0; f < geo.LEVELS; f++) {
        for (let i = 0; i < dist[f].length; i++) if (dist[f][i] < 1e8) push(dist[f][i], f, i);
      }
      const pop = () => {
        const top = heap[0];
        const last = heap.pop();
        if (heap.length && last) {
          heap[0] = last;
          let k = 0;
          for (;;) {
            const l = k * 2 + 1;
            const r = l + 1;
            let m = k;
            if (l < heap.length && heap[l].cost < heap[m].cost) m = l;
            if (r < heap.length && heap[r].cost < heap[m].cost) m = r;
            if (m === k) break;
            const tmp = heap[k]; heap[k] = heap[m]; heap[m] = tmp;
            k = m;
          }
        }
        return top;
      };
      const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1]];
      let guard = 0;
      while (heap.length && guard < 80000) {
        guard++;
        const cur = pop();
        if (!cur || cur.cost > dist[cur.f][cur.i] + 0.05) continue;
        const ix = cur.i % geo.GW;
        const iz = (cur.i / geo.GW) | 0;
        for (let d = 0; d < 4; d++) {
          const nx = ix + dirs[d][0];
          const nz = iz + dirs[d][1];
          if (!b.canStep(cur.f, ix, iz, nx, nz)) continue;
          const ni = geo.idx(nx, nz);
          const nd = cur.cost + 1;
          if (nd < dist[cur.f][ni]) {
            dist[cur.f][ni] = nd;
            next[cur.f][ni] = cur.i;
            push(nd, cur.f, ni);
          }
        }
        for (let p = 0; p < b.portals.length; p++) {
          const portal = b.portals[p];
          if (portal.id === "SE") continue;
          if (portal.up !== cur.f) continue;
          if (Math.abs(ix - portal.ix) + Math.abs(iz - portal.iz) > 3) continue;
          const pi = geo.idx(portal.ix, portal.iz);
          const nd = cur.cost + portal.cost;
          if (nd < dist[portal.down][pi]) {
            dist[portal.down][pi] = nd;
            next[portal.up][pi] = pi;
            next[portal.down][pi] = cur.i;
            push(nd, portal.down, pi);
          }
        }
      }
    }

    setJam(blocked) {
      if (blocked === this.seBlocked && !blocked) return;
      this.seBlocked = blocked;
      this.swOpen = blocked;
      if (blocked) this.closed.SE = true;
      else this.closed.SE = false;
      this.recompute();
    }

    closeStair(id) {
      if (!id || this.closed[id]) return false;
      this.closed[id] = true;
      this.seBlocked = true;
      this.recompute();
      return true;
    }

    nearestOpen(floor, x, z) {
      const mouths = this.b.mouths || [];
      let best = null;
      let bestD = 1e9;
      for (let i = 0; i < mouths.length; i++) {
        const m = mouths[i];
        if (this.closed[m.id]) continue;
        if (floor > 0 && !(m.floors && m.floors[floor])) continue;
        const d = Math.hypot(m.x - x, m.z - z);
        if (d < bestD) { bestD = d; best = m; }
      }
      return best;
    }

    emptiest(loads) {
      const mouths = this.b.mouths || [];
      let best = null;
      let n = 1e9;
      for (let i = 0; i < mouths.length; i++) {
        if (this.closed[mouths[i].id]) continue;
        const c = loads[mouths[i].id] || 0;
        if (c < n) { n = c; best = mouths[i]; }
      }
      return best;
    }

    stepFor(agent) {
      const geo = G();
      const cell = this.b.nearestWalk(agent.floor, agent.x, agent.z);
      const i = geo.idx(cell.ix, cell.iz);
      const n = this.next[agent.floor][i];
      if (n < 0) return null;
      if (n === i) {
        return { x: geo.cellX(cell.ix) + 0.5, z: geo.cellZ(cell.iz) + 0.5, floor: agent.floor };
      }
      const nx = n % geo.GW;
      const nz = (n / geo.GW) | 0;
      return { x: geo.cellX(nx) + 0.5, z: geo.cellZ(nz) + 0.5, floor: agent.floor };
    }

    guideFrom(floor, x, z) {
      const geo = G();
      const pts = [];
      let cell = this.b.nearestWalk(floor, x, z);
      let f = floor;
      let guard = 0;
      let prev = -1;
      while (guard++ < 500) {
        const i = geo.idx(cell.ix, cell.iz);
        pts.push(new THREE.Vector3(geo.cellX(cell.ix) + 0.5, 0.38, geo.cellZ(cell.iz) + 0.5));
        const n = this.next[f][i];
        if (n < 0 || n === prev) break;
        if (n === i) {
          let dropped = false;
          for (let p = 0; p < this.b.portals.length; p++) {
            const portal = this.b.portals[p];
            if (portal.up !== f) continue;
            if (this.closed[portal.id]) continue;
            if (Math.abs(cell.ix - portal.ix) + Math.abs(cell.iz - portal.iz) > 2) continue;
            f = portal.down;
            cell = { ix: portal.ix, iz: portal.iz };
            dropped = true;
            break;
          }
          if (!dropped) break;
          continue;
        }
        prev = i;
        const nx = n % geo.GW;
        const nz = (n / geo.GW) | 0;
        const wx = geo.cellX(nx) + 0.5;
        const wz = geo.cellZ(nz) + 0.5;
        let dropped = false;
        for (let p = 0; p < this.b.portals.length; p++) {
          const portal = this.b.portals[p];
          if (portal.up === f && Math.abs(wx - portal.x) < 1.6 && Math.abs(wz - portal.z) < 1.6) {
            if (this.closed[portal.id]) continue;
            f = portal.down;
            cell = { ix: portal.ix, iz: portal.iz };
            dropped = true;
            break;
          }
        }
        if (!dropped) cell = { ix: nx, iz: nz };
        if (f === 0 && Math.hypot(geo.cellX(cell.ix) - this.b.assembly.x, geo.cellZ(cell.iz) - this.b.assembly.z) < 6) {
          pts.push(new THREE.Vector3(this.b.assembly.x, 0.38, this.b.assembly.z));
          break;
        }
      }
      let len = 0;
      for (let k = 1; k < pts.length; k++) len += pts[k].distanceTo(pts[k - 1]);
      this.guide = pts;
      this.routeLen = len;
      return { pts: pts, len: len, floor: f };
    }

    updateLeds(time) {
      const geo = G();
      const leds = this.b.leds;
      const loads = this.loads || {};
      const dt = this._ledT == null ? 0.016 : Math.min(0.05, Math.max(0, time - this._ledT));
      this._ledT = time;
      for (let i = 0; i < leds.length; i++) {
        const led = leds[i];
        const i0 = geo.idx(led.ix, led.iz);
        const n = this.next && this.next[led.floor] ? this.next[led.floor][i0] : -1;
        let want = led._yaw;
        if (n >= 0) {
          const nx = n % geo.GW;
          const nz = (n / geo.GW) | 0;
          const dx = nx - led.ix;
          const dz = nz - led.iz;
          if (dx !== 0 || dz !== 0) {
            let local = Math.atan2(dx, dz) - (led.face || 0);
            while (local > Math.PI) local -= Math.PI * 2;
            while (local < -Math.PI) local += Math.PI * 2;
            want = Math.round(local / (Math.PI / 2)) * (Math.PI / 2);
          }
        }
        if (want == null) want = 0;
        if (led._pending !== want) { led._pending = want; led._hold = 0; }
        else led._hold = (led._hold || 0) + dt;
        if (led._yaw == null || led._hold >= 0.4) led._yaw = want;
        if (led.arrow) led.arrow.rotation.z = Math.PI / 2 - led._yaw;
        let sid = null;
        let sd = 1e9;
        const mouths = this.b.mouths || [];
        for (let m = 0; m < mouths.length; m++) {
          const d = Math.hypot(mouths[m].x - led.x, mouths[m].z - led.z);
          if (mouths[m].floors && mouths[m].floors[led.floor] && d < sd) {
            sd = d;
            sid = mouths[m].id;
          }
        }
        const load = sid ? (loads[sid] || 0) : 0;
        if (led.housing) {
          const hot = load >= 26;
          const warm = load >= 14;
          led.housing.material.emissive.setHex(hot ? 0xc2410c : warm ? 0xca8a04 : 0x15803d);
          led.housing.material.color.setHex(hot ? 0x7c2d12 : 0x052e16);
          led.housing.visible = true;
        }
      }
    }

    drawTrail(floor, offsetFn) {
      const pts = this.guide;
      if (!pts || pts.length < 2) {
        if (this.line) this.line.visible = false;
        return;
      }
      const y = offsetFn(floor);
      const arr = new Float32Array(pts.length * 3);
      for (let i = 0; i < pts.length; i++) {
        arr[i * 3] = pts[i].x;
        arr[i * 3 + 1] = y + 0.42;
        arr[i * 3 + 2] = pts[i].z;
      }
      if (!this.line) {
        this._geom = new THREE.BufferGeometry();
        this._geom.setAttribute("position", new THREE.BufferAttribute(arr, 3));
        this.line = new THREE.Line(this._geom, new THREE.LineBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.95 }));
        this.line.frustumCulled = false;
        this.scene.add(this.line);
      } else {
        this.line.visible = true;
        this._geom.setAttribute("position", new THREE.BufferAttribute(arr, 3));
        this._geom.computeBoundingSphere();
      }
      this._placeChevrons(pts, y);
    }

    _chevronGeo() {
      if (this._chev) return this._chev;
      const s = new THREE.Shape();
      s.moveTo(0, 0.7);
      s.lineTo(0.38, 0.05);
      s.lineTo(0.14, 0.05);
      s.lineTo(0.14, -0.55);
      s.lineTo(-0.14, -0.55);
      s.lineTo(-0.14, 0.05);
      s.lineTo(-0.38, 0.05);
      s.closePath();
      this._chev = new THREE.ShapeGeometry(s);
      return this._chev;
    }

    _placeChevrons(pts, y) {
      if (!this.chevrons) {
        this.chevrons = [];
        const mat = new THREE.MeshBasicMaterial({ color: 0x4ade80, side: THREE.DoubleSide });
        for (let i = 0; i < 24; i++) {
          const m = new THREE.Mesh(this._chevronGeo(), mat);
          m.rotation.x = -Math.PI / 2;
          m.visible = false;
          this.scene.add(m);
          this.chevrons.push(m);
        }
      }
      let n = 0;
      for (let i = 4; i < pts.length - 1 && n < this.chevrons.length; i += 8) {
        const a = pts[i];
        const b = pts[Math.min(pts.length - 1, i + 1)];
        const m = this.chevrons[n++];
        m.visible = true;
        m.position.set(a.x, y + 0.08, a.z);
        let ang = Math.atan2(b.x - a.x, b.z - a.z);
        ang = Math.round(ang / (Math.PI / 2)) * (Math.PI / 2);
        m.rotation.y = ang;
      }
      for (let i = n; i < this.chevrons.length; i++) this.chevrons[i].visible = false;
    }
  }

  WEV.WiEvacEngine = WiEvacEngine;
})();
