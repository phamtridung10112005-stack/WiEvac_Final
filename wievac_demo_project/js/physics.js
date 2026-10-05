/* PhysicsEngine — Spatial hash AABB, trượt tường, sàn cầu thang */
(function () {
  "use strict";
  const WEV = (window.WEV = window.WEV || {});

  class PhysicsEngine {
    constructor(cell) {
      this.cell = cell || 4;
      this.buckets = new Map();
      this.stairs = [];
      this.lawn = { minX: -36, maxX: 40, minZ: 29.15, maxZ: 56 };
      this.shell = { minX: -49.55, maxX: 49.55, minZ: -29.55, maxZ: 29.55 };
    }

    _key(ix, iz) {
      return ix + "," + iz;
    }

    addBox(floor, minX, maxX, minZ, maxZ) {
      if (maxX - minX < 0.02 || maxZ - minZ < 0.02) return;
      const box = { floor: floor, minX: minX, maxX: maxX, minZ: minZ, maxZ: maxZ };
      const c = this.cell;
      const ix0 = Math.floor(minX / c);
      const ix1 = Math.floor(maxX / c);
      const iz0 = Math.floor(minZ / c);
      const iz1 = Math.floor(maxZ / c);
      for (let iz = iz0; iz <= iz1; iz++) {
        for (let ix = ix0; ix <= ix1; ix++) {
          const k = this._key(ix, iz);
          let arr = this.buckets.get(k);
          if (!arr) {
            arr = [];
            this.buckets.set(k, arr);
          }
          arr.push(box);
        }
      }
    }

    addStair(stair) {
      this.stairs.push(stair);
    }

    _candidates(floor, x, z, r) {
      const c = this.cell;
      const ix0 = Math.floor((x - r) / c);
      const ix1 = Math.floor((x + r) / c);
      const iz0 = Math.floor((z - r) / c);
      const iz1 = Math.floor((z + r) / c);
      const out = [];
      const seen = new Set();
      for (let iz = iz0; iz <= iz1; iz++) {
        for (let ix = ix0; ix <= ix1; ix++) {
          const arr = this.buckets.get(this._key(ix, iz));
          if (!arr) continue;
          for (let i = 0; i < arr.length; i++) {
            const b = arr[i];
            if (b.floor !== floor || seen.has(b)) continue;
            seen.add(b);
            out.push(b);
          }
        }
      }
      return out;
    }

    overlaps(floor, x, z, r) {
      if (floor === 0 && x >= this.lawn.minX && x <= this.lawn.maxX && z >= this.lawn.minZ && z <= this.lawn.maxZ) {
        return false;
      }
      const s = this.shell;
      if (x - r < s.minX || x + r > s.maxX || z - r < s.minZ || z + r > s.maxZ) {
        if (!(floor === 0 && z >= s.maxZ - 0.2 && x > 4.2 && x < 12.2)) return true;
      }
      const list = this._candidates(floor, x, z, r);
      for (let i = 0; i < list.length; i++) {
        const b = list[i];
        const cx = Math.max(b.minX, Math.min(x, b.maxX));
        const cz = Math.max(b.minZ, Math.min(z, b.maxZ));
        const dx = x - cx;
        const dz = z - cz;
        if (dx * dx + dz * dz < r * r) return true;
      }
      return false;
    }

    /* Trượt theo từng trục: đâm chéo thì giữ thành phần tiếp tuyến. */
    move(floor, x, z, r, dx, dz) {
      let nx = x;
      let nz = z;
      if (dx !== 0) {
        if (!this.overlaps(floor, x + dx, z, r)) nx = x + dx;
        else {
          const step = Math.sign(dx) * 0.05;
          const n = Math.min(8, Math.floor(Math.abs(dx) / 0.05));
          for (let i = 0; i < n; i++) {
            if (this.overlaps(floor, nx + step, z, r)) break;
            nx += step;
          }
        }
      }
      if (dz !== 0) {
        if (!this.overlaps(floor, nx, z + dz, r)) nz = z + dz;
        else {
          const step = Math.sign(dz) * 0.05;
          const n = Math.min(8, Math.floor(Math.abs(dz) / 0.05));
          for (let i = 0; i < n; i++) {
            if (this.overlaps(floor, nx, nz + step, r)) break;
            nz += step;
          }
        }
      }
      return { x: nx, z: nz };
    }

    sampleY(floor, x, z) {
      let best = null;
      let bestD = 1e9;
      for (let i = 0; i < this.stairs.length; i++) {
        const s = this.stairs[i];
        if (s.floor !== floor) continue;
        const y = s.sample(x, z);
        if (y == null) continue;
        const dx = x - (s.x0 + s.x1) * 0.5;
        const dz = z - (s.z0 + s.z1) * 0.5;
        const d = dx * dx + dz * dz;
        if (d < bestD) {
          bestD = d;
          best = y;
        }
      }
      if (best != null) return best;
      if (floor === 0 && z > 29.2) {
        const t = Math.min(1, (z - 29.2) / 1.4);
        return 0.3 * (1 - t);
      }
      return 0.3;
    }
  }

  WEV.PhysicsEngine = PhysicsEngine;
})();
