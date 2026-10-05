/* BuildingEngine — tòa nhà 100×60 m, 5 tầng, phòng, buồng thang, cột 8 m */
(function () {
  "use strict";
  const WEV = (window.WEV = window.WEV || {});
  const THREE = window.THREE;

  const OX = -50, OZ = -30, CS = 1, GW = 100, GH = 90;
  const LEVELS = 5;
  const FH = 4.2;
  const SLAB = 0.3;
  const WALL_H = 3.55;
  const OUT = 0, COR = 1, STAIR = 2, ROOM = 3;

  function idx(ix, iz) { return iz * GW + ix; }
  function cellX(ix) { return OX + ix * CS; }
  function cellZ(iz) { return OZ + iz * CS; }

  function stamp(grid, x0, x1, z0, z1, type) {
    const ix0 = Math.max(0, Math.floor(x0 - OX));
    const ix1 = Math.min(GW, Math.ceil(x1 - OX));
    const iz0 = Math.max(0, Math.floor(z0 - OZ));
    const iz1 = Math.min(GH, Math.ceil(z1 - OZ));
    for (let iz = iz0; iz < iz1; iz++) {
      for (let ix = ix0; ix < ix1; ix++) grid[idx(ix, iz)] = type;
    }
  }

  function inRect(x, z, r) {
    return x >= r.x0 && x < r.x1 && z >= r.z0 && z < r.z1;
  }

  const CORES = [
    { id: "NW", name: "Thang Tây-Bắc", x0: -49.2, x1: -42.6, z0: -29.2, z1: -20.05, door: "south", links: [[4, 3], [3, 2]] },
    { id: "NE", name: "Thang Đông-Bắc", x0: 42.6, x1: 49.2, z0: -29.2, z1: -20.05, door: "south", links: [[4, 3], [3, 2]] },
    { id: "SW", name: "Thang Tây", x0: -49.2, x1: -42.6, z0: 20.15, z1: 29.2, door: "north", links: [[2, 1], [1, 0]] },
    { id: "SE", name: "Thang Đông", x0: 42.6, x1: 49.2, z0: 20.15, z1: 29.2, door: "north", links: [[4, 3], [3, 2]] },
    { id: "EX", name: "Thang thoát hiểm ngoài", x0: 16.2, x1: 22.4, z0: 29.25, z1: 36.2, door: "north", links: [[0, -1]], outdoor: true }
  ];

  function corridors() {
    return [
      [-42, 42, -20, -17],
      [-42, 42, 17.2, 20.2],
      [-42, -39, -20, 20.2],
      [-42, 42, -2.2, 0.8],
      [-24.4, -21.8, -17, -2.2],
      [-16.4, -13.8, 0.8, 17.2],
      [10.2, 12.6, -17, -7.5],
      [36.2, 38.6, 6.2, 17.2],
      [-48.6, -42, 3.6, 6.6],
      [5.2, 11.4, 20.2, 29.45]
    ];
  }

  function authoredSpecs() {
    return [
      { x0: -38.6, x1: -26, z0: -29.1, z1: -20.05, door: "s", name: "P.302", sub: "Lab CSI", kind: "lab" },
      { x0: -25.4, x1: -12, z0: -29.1, z1: -20.15, door: "s", name: "VP.201", sub: "Văn phòng khoa", kind: "office" },
      { x0: -11.4, x1: 2, z0: -29.1, z1: -20.15, door: "s", name: "P.204", sub: "Phòng học", kind: "class" },
      { x0: 2.6, x1: 16, z0: -29.1, z1: -20.15, door: "s", name: "P.208", sub: "Phòng học", kind: "class" },
      { x0: 16.6, x1: 30, z0: -29.1, z1: -20.15, door: "s", name: "H.210", sub: "Phòng họp", kind: "meet" },
      { x0: 30.6, x1: 41.6, z0: -29.1, z1: -20.15, door: "s", name: "P.214", sub: "Lab Viễn thông", kind: "lab" },
      { x0: -38.4, x1: -25.2, z0: -16.85, z1: -2.25, door: "s", name: "P.220", sub: "Phòng học", kind: "class" },
      { x0: -21.2, x1: -8, z0: -16.7, z1: -2.5, door: "n", name: "P.224", sub: "Phòng tự học", kind: "study" },
      { x0: -7.4, x1: 9.6, z0: -16.7, z1: -2.5, door: "n", name: "P.230", sub: "Lab Vi điều khiển", kind: "lab" },
      { x0: 13.2, x1: 28, z0: -16.7, z1: -2.5, door: "n", name: "WC", sub: "Vệ sinh", kind: "wc" },
      { x0: 28.6, x1: 41.6, z0: -16.7, z1: -2.5, door: "s", name: "KT.240", sub: "Phòng điện PCCC", kind: "util" },
      { x0: -38.4, x1: -17, z0: 1.15, z1: 16.85, door: "e", name: "TV.1", sub: "Thư viện số", kind: "library" },
      { x0: -12.6, x1: 22, z0: 1.15, z1: 17.15, door: "s", name: "A101", sub: "Hội trường Lớn", kind: "auditorium" },
      { x0: 22.6, x1: 35.6, z0: 1.15, z1: 8.2, door: "n", name: "WC-N", sub: "Vệ sinh Nam", kind: "wc" },
      { x0: 22.6, x1: 35.6, z0: 8.8, z1: 16.85, door: "s", name: "WC-Nữ", sub: "Vệ sinh Nữ", kind: "wc" },
      { x0: 39.2, x1: 48.6, z0: 1.15, z1: 16.85, door: "w", name: "SV.1", sub: "Phòng Server", kind: "lab" },
      { x0: -38.6, x1: -24, z0: 20.25, z1: 28.9, door: "n", name: "P.101", sub: "Phòng học", kind: "class" },
      { x0: -23.4, x1: -6, z0: 20.4, z1: 28.9, door: "n", name: "P.105", sub: "Phòng học", kind: "class" },
      { x0: 12, x1: 26, z0: 20.4, z1: 28.9, door: "n", name: "KT.110", sub: "Kho thiết bị", kind: "util" },
      { x0: 26.6, x1: 41.6, z0: 20.4, z1: 28.9, door: "n", name: "P.118", sub: "Phòng học", kind: "class" }
    ];
  }

  function buildUStair(core) {
    const alongZ = core.door === "north" || core.door === "south";
    const span = alongZ ? (core.z1 - core.z0) : (core.x1 - core.x0);
    const cross = alongZ ? (core.x1 - core.x0) : (core.z1 - core.z0);
    const entry = 1.35;
    const far = 1.2;
    const run = Math.max(2.2, span - entry - far);
    const yTop = 0.3;
    const yMid = 0.3 - 2.1;
    const yBot = 0.3 - 4.2;
    const gap = 0.42;
    const flightW = (cross - gap) * 0.5 - 0.08;

    function map(along, crossPos) {
      if (alongZ) {
        const z = core.door === "south" ? core.z1 - along : core.z0 + along;
        return { x: core.x0 + crossPos, z: z };
      }
      const x = core.door === "east" ? core.x1 - along : core.x0 + along;
      return { x: x, z: core.z0 + crossPos };
    }

    return {
      core: core,
      entry: entry,
      far: far,
      run: run,
      yTop: yTop,
      yMid: yMid,
      yBot: yBot,
      flightW: flightW,
      gap: gap,
      cross: cross,
      alongZ: alongZ,
      map: map,
      x0: core.x0,
      x1: core.x1,
      z0: core.z0,
      z1: core.z1,
      sample: function (x, z) {
        if (x < core.x0 + 0.12 || x > core.x1 - 0.12 || z < core.z0 + 0.12 || z > core.z1 - 0.12) return null;
        let along;
        let crossPos;
        if (alongZ) {
          along = core.door === "south" ? core.z1 - z : z - core.z0;
          crossPos = x - core.x0;
        } else {
          along = core.door === "east" ? core.x1 - x : x - core.x0;
          crossPos = z - core.z0;
        }
        if (along < 0 || along > span) return null;
        if (along >= entry + run) return yMid;
        const west = crossPos <= flightW + 0.12;
        const east = crossPos >= cross - flightW - 0.12;
        if (along <= entry) {
          if (east) return yBot;
          if (west) return yTop;
          return null;
        }
        const t = (along - entry) / run;
        if (west) return yTop + (yMid - yTop) * t;
        if (east) return yMid + (yBot - yMid) * (1 - t);
        return null;
      }
    };
  }

  function makeLabelTexture(text, sub) {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 128;
    const g = c.getContext("2d");
    g.fillStyle = "rgba(15,23,42,0.88)";
    g.fillRect(0, 0, 512, 128);
    g.strokeStyle = "#38bdf8";
    g.lineWidth = 6;
    g.strokeRect(4, 4, 504, 120);
    g.fillStyle = "#f8fafc";
    g.font = "700 42px Segoe UI, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(text, 256, sub ? 48 : 64);
    if (sub) {
      g.font = "600 28px Segoe UI, sans-serif";
      g.fillStyle = "#7dd3fc";
      g.fillText(sub, 256, 92);
    }
    const tex = new THREE.CanvasTexture(c);
    tex.anisotropy = 4;
    return tex;
  }

  function canvasTex(w, h, paint) {
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    paint(c.getContext("2d"), w, h);
    const tex = new THREE.CanvasTexture(c);
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    tex.anisotropy = 4;
    return tex;
  }

  function tileTexture() {
    return canvasTex(256, 256, function (g) {
      g.fillStyle = "#cfc6b8";
      g.fillRect(0, 0, 256, 256);
      const n = 4;
      const s = 256 / n;
      for (let y = 0; y < n; y++) {
        for (let x = 0; x < n; x++) {
          const alt = (x + y) % 2 === 0;
          g.fillStyle = alt ? "#e7e0d4" : "#d9d0c2";
          g.fillRect(x * s + 2, y * s + 2, s - 4, s - 4);
        }
      }
    });
  }

  function woodTexture() {
    return canvasTex(128, 256, function (g) {
      g.fillStyle = "#a56a3a";
      g.fillRect(0, 0, 128, 256);
      for (let i = 0; i < 18; i++) {
        g.strokeStyle = i % 2 ? "rgba(90,48,22,0.35)" : "rgba(255,220,170,0.18)";
        g.lineWidth = 2 + (i % 3);
        g.beginPath();
        g.moveTo(0, 8 + i * 14);
        g.lineTo(128, 12 + i * 14);
        g.stroke();
      }
    });
  }

  function concreteTexture() {
    return canvasTex(128, 128, function (g) {
      g.fillStyle = "#b9b3a8";
      g.fillRect(0, 0, 128, 128);
      for (let i = 0; i < 80; i++) {
        g.fillStyle = i % 2 ? "rgba(255,255,255,0.15)" : "rgba(80,70,60,0.12)";
        g.fillRect((i * 37) % 128, (i * 19) % 128, 3, 2);
      }
    });
  }

  function grassTexture() {
    return canvasTex(128, 128, function (g) {
      g.fillStyle = "#3f7d32";
      g.fillRect(0, 0, 128, 128);
      for (let i = 0; i < 160; i++) {
        g.fillStyle = i % 3 ? "#4c9440" : "#2f6828";
        g.fillRect((i * 53) % 128, (i * 29) % 128, 2, 3);
      }
    });
  }

  function facadeTexture() {
    return canvasTex(128, 128, function (g) {
      g.fillStyle = "#efe6d6";
      g.fillRect(0, 0, 128, 128);
      g.fillStyle = "#e4d3b8";
      for (let y = 0; y < 128; y += 16) g.fillRect(0, y, 128, 2);
    });
  }

  class BuildingEngine {
    constructor(scene, physics) {
      this.scene = scene;
      this.physics = physics;
      this.floors = [];
      this.rooms = [];
      this.portals = [];
      this.leds = [];
      this.grid = [];
      this.roomId = [];
      this.door = [];
      this.types = null;
      this.spawn = { floor: 2, x: 30, z: -24 };
      this.jamZone = { floor: 2, minX: 36.5, maxX: 46.5, minZ: 14.5, maxZ: 21.2 };
      this.fire = { floor: 2, x: 44.2, z: 18.6 };
      this.assembly = { x: 8.5, z: 40, r: 8 };
      this.southWalls = [];
      this.layoutMode = "exploded";
      this.focus = -1;
      this.follow = false;
      this._build();
    }

    offset(floor) {
      if (this.layoutMode === "exploded") return floor * 10;
      if (this.layoutMode === "single") return 0;
      return floor * FH;
    }

    applyLayout() {
      for (let f = 0; f < LEVELS; f++) {
        const show = this.layoutMode === "single" ? f === this.focus : true;
        this.floors[f].visible = show;
        this.floors[f].position.y = this.offset(f);
        const south = this.southWalls[f];
        if (south) south.visible = this.layoutMode !== "exploded";
      }
      const rigs = this.stairRigs || [];
      for (let i = 0; i < rigs.length; i++) {
        const rig = rigs[i];
        let gap = rig.span;
        if (this.layoutMode !== "single" && rig.floor > 0) {
          gap = this.offset(rig.floor) - this.offset(rig.floor - 1);
        }
        rig.rig.scale.y = gap / rig.span;
        rig.rig.visible = this.floors[rig.floor].visible;
      }
    }

    _build() {
      const grid = new Uint8Array(GW * GH);
      for (let iz = 0; iz < GH; iz++) {
        for (let ix = 0; ix < GW; ix++) {
          const x = cellX(ix);
          const z = cellZ(iz);
          if (x > -49.45 && x < 49.45 && z > -29.45 && z < 29.45) grid[idx(ix, iz)] = ROOM;
        }
      }
      const cors = corridors();
      for (let i = 0; i < cors.length; i++) stamp(grid, cors[i][0], cors[i][1], cors[i][2], cors[i][3], COR);
      for (let i = 0; i < CORES.length; i++) {
        if (CORES[i].outdoor) continue;
        stamp(grid, CORES[i].x0, CORES[i].x1, CORES[i].z0, CORES[i].z1, STAIR);
      }
      this.types = grid;
      const rooms = this._extractRooms(grid);
      this._claimAuditorium(grid, rooms);
      this.roomList = rooms;
      this._markDoors(grid, rooms);

      const mats = this._materials();
      this.mats = mats;
      const dummy = new THREE.Object3D();

      for (let f = 0; f < LEVELS; f++) {
        const g = new THREE.Group();
        g.name = "floor-" + (f + 1);
        this.scene.add(g);
        this.floors.push(g);
        this.grid[f] = new Uint8Array(GW * GH);
        this.roomId[f] = new Int16Array(GW * GH);
        this.roomId[f].fill(-1);
        this.door[f] = new Uint8Array(GW * GH);
        this._fillFloorGrid(f, grid, rooms);
        this._buildSlab(f, g, mats);
        this._buildWalls(f, g, mats, dummy, rooms);
        this._buildFinish(f, g, mats);
        this._buildColumns(f, g, mats, dummy);
        this._buildRoomsVisual(f, g, mats, rooms);
        this._buildStairs(f, g, mats);
        this._buildLeds(f, g, mats);
        this._floorSign(f, g);
      }
      this._ground(mats);
      this._portals();
      this._pickSpawn(rooms);
      this.applyLayout();
    }

    _materials() {
      const tile = tileTexture();
      const wood = woodTexture();
      const concrete = concreteTexture();
      concrete.repeat.set(22, 14);
      const grass = grassTexture();
      grass.repeat.set(26, 18);
      const facade = facadeTexture();
      facade.repeat.set(10, 3);
      this._tileTex = tile;
      this._woodTex = wood;
      this._roomTex = wood;
      return {
        slab: new THREE.MeshStandardMaterial({ map: concrete, color: 0xd6d3d1, roughness: 0.92 }),
        corridor: new THREE.MeshStandardMaterial({ map: tile, color: 0xffffff, roughness: 0.78 }),
        roomFloor: new THREE.MeshStandardMaterial({ map: wood, color: 0xffffff, roughness: 0.72 }),
        wall: new THREE.MeshStandardMaterial({ color: 0xf6f1e7, roughness: 0.9 }),
        wallS: new THREE.MeshStandardMaterial({ color: 0xf3e6d4, roughness: 0.88 }),
        brick: new THREE.MeshStandardMaterial({ map: facade, color: 0xffffff, roughness: 0.9 }),
        col: new THREE.MeshStandardMaterial({ color: 0xe7e5e4, roughness: 0.75 }),
        glass: new THREE.MeshStandardMaterial({ color: 0xbae6fd, transparent: true, opacity: 0.35, roughness: 0.08, metalness: 0.05 }),
        wood: new THREE.MeshStandardMaterial({ map: wood, color: 0xffffff, roughness: 0.62 }),
        lab: new THREE.MeshStandardMaterial({ color: 0x334155, roughness: 0.55, metalness: 0.15 }),
        rail: new THREE.MeshStandardMaterial({ color: 0xe5e7eb, metalness: 0.65, roughness: 0.28 }),
        step: new THREE.MeshStandardMaterial({ map: concrete, color: 0xdedad3, roughness: 0.86 }),
        nosing: new THREE.MeshStandardMaterial({ color: 0xf5c542, roughness: 0.55 }),
        door: new THREE.MeshStandardMaterial({ map: wood, color: 0xffffff, roughness: 0.55 }),
        desk: new THREE.MeshStandardMaterial({ map: wood, color: 0xf5f5f4, roughness: 0.62 }),
        grass: new THREE.MeshStandardMaterial({ map: grass, color: 0xffffff, roughness: 1 }),
        led: new THREE.MeshStandardMaterial({ color: 0x052e16, emissive: 0x166534, emissiveIntensity: 0.7 }),
        ledArrow: new THREE.MeshStandardMaterial({ color: 0xecfdf5, emissive: 0xbbf7d0, emissiveIntensity: 0.9 }),
        csi: new THREE.MeshStandardMaterial({ color: 0x052e16, emissive: 0x00d084, emissiveIntensity: 0.8 }),
        safe: new THREE.MeshStandardMaterial({ color: 0x22c55e, emissive: 0x16a34a, emissiveIntensity: 0.35 }),
        ceiling: new THREE.MeshStandardMaterial({ color: 0xf5f5f4, roughness: 0.95 }),
        lamp: new THREE.MeshStandardMaterial({ color: 0xfffbeb, emissive: 0xfff3c4, emissiveIntensity: 1.1 }),
        skirt: new THREE.MeshStandardMaterial({ color: 0x6b5e52, roughness: 0.85 }),
        frame: new THREE.MeshStandardMaterial({ color: 0xfafaf9, roughness: 0.45 }),
        seat: new THREE.MeshStandardMaterial({ color: 0x7f1d1d, roughness: 0.7 }),
        board: new THREE.MeshStandardMaterial({ color: 0x14532d, roughness: 0.85 }),
        path: new THREE.MeshStandardMaterial({ color: 0xd6d3d1, roughness: 0.9 })
      };
    }

    _tiled(tex, w, h) {
      const map = tex.clone();
      map.needsUpdate = true;
      map.wrapS = map.wrapT = THREE.RepeatWrapping;
      map.repeat.set(Math.max(0.5, w), Math.max(0.5, h));
      return map;
    }

    _extractRooms(grid) {
      const used = new Uint8Array(GW * GH);
      const rooms = [];
      const specs = authoredSpecs();
      for (let s = 0; s < specs.length; s++) {
        const spec = specs[s];
        const ix0 = Math.max(0, Math.floor(spec.x0 - OX));
        const ix1 = Math.min(GW, Math.floor(spec.x1 - OX));
        const iz0 = Math.max(0, Math.floor(spec.z0 - OZ));
        const iz1 = Math.min(GH, Math.floor(spec.z1 - OZ));
        let count = 0;
        for (let iz = iz0; iz < iz1; iz++) {
          for (let ix = ix0; ix < ix1; ix++) {
            if (grid[idx(ix, iz)] !== ROOM || used[idx(ix, iz)]) continue;
            used[idx(ix, iz)] = 1;
            count++;
          }
        }
        if (count < 12) continue;
        const room = {
          ix: ix0, iz: iz0, w: Math.max(1, ix1 - ix0), h: Math.max(1, iz1 - iz0),
          x0: cellX(ix0), z0: cellZ(iz0), x1: cellX(ix1), z1: cellZ(iz1),
          area: count, kind: spec.kind, fixed: spec.kind === "auditorium",
          doorSide: spec.door, labelName: spec.name, labelSub: spec.sub
        };
        rooms.push(room);
      }
      for (let iz = 0; iz < GH; iz++) {
        for (let ix = 0; ix < GW; ix++) {
          if (grid[idx(ix, iz)] === ROOM && !used[idx(ix, iz)]) grid[idx(ix, iz)] = COR;
        }
      }
      return rooms;
    }

    _claimAuditorium() { return; }
    _claimAuditoriumUnused(grid, rooms) {
      const x0 = -6, x1 = 20, z0 = 3.2, z1 = 13.4;
      const ix0 = Math.max(0, Math.floor(x0 - OX));
      const ix1 = Math.min(GW, Math.floor(x1 - OX));
      const iz0 = Math.max(0, Math.floor(z0 - OZ));
      const iz1 = Math.min(GH, Math.floor(z1 - OZ));
      let count = 0;
      for (let iz = iz0; iz < iz1; iz++) {
        for (let ix = ix0; ix < ix1; ix++) if (grid[idx(ix, iz)] === ROOM) count++;
      }
      if (count < 40) return;
      const aud = {
        ix: ix0, iz: iz0, w: ix1 - ix0, h: iz1 - iz0,
        x0: cellX(ix0), z0: cellZ(iz0), x1: cellX(ix1), z1: cellZ(iz1),
        area: count, kind: "auditorium", fixed: true
      };
      for (let i = rooms.length - 1; i >= 0; i--) {
        const r = rooms[i];
        if (r.x1 <= aud.x0 || r.x0 >= aud.x1 || r.z1 <= aud.z0 || r.z0 >= aud.z1) continue;
        rooms.splice(i, 1);
      }
      rooms.unshift(aud);
    }

    _markDoors(grid, rooms) {
      for (let i = 0; i < rooms.length; i++) {
        const r = rooms[i];
        let best = null;
        const edges = [
          { side: "n", len: r.x1 - r.x0, x: (r.x0 + r.x1) / 2, z: r.z0 },
          { side: "s", len: r.x1 - r.x0, x: (r.x0 + r.x1) / 2, z: r.z1 - 0.05 },
          { side: "w", len: r.z1 - r.z0, x: r.x0, z: (r.z0 + r.z1) / 2 },
          { side: "e", len: r.z1 - r.z0, x: r.x1 - 0.05, z: (r.z0 + r.z1) / 2 }
        ];
        for (let e = 0; e < edges.length; e++) {
          const ed = edges[e];
          const ix = Math.max(0, Math.min(GW - 1, Math.floor(ed.x - OX)));
          const iz = Math.max(0, Math.min(GH - 1, Math.floor(ed.z - OZ)));
          let touch = 0;
          if (ed.side === "n" && iz > 0 && grid[idx(ix, iz - 1)] === COR) touch = ed.len;
          if (ed.side === "s" && iz < GH - 1 && grid[idx(ix, Math.min(GH - 1, iz + 1))] === COR) touch = ed.len;
          if (ed.side === "w" && ix > 0 && grid[idx(ix - 1, iz)] === COR) touch = ed.len;
          if (ed.side === "e" && ix < GW - 1 && grid[idx(ix + 1, iz)] === COR) touch = ed.len;
          if (touch > 0 && (!best || touch > best.touch)) best = { side: ed.side, x: ed.x, z: ed.z, touch: touch };
        }
        if (r.doorSide) {
          const cx = (r.x0 + r.x1) / 2;
          const cz = (r.z0 + r.z1) / 2;
          if (r.doorSide === "n") r.door = { side: "n", x: cx, z: r.z0, touch: 4 };
          else if (r.doorSide === "s") r.door = { side: "s", x: cx, z: r.z1 - 0.05, touch: 4 };
          else if (r.doorSide === "w") r.door = { side: "w", x: r.x0, z: cz, touch: 4 };
          else r.door = { side: "e", x: r.x1 - 0.05, z: cz, touch: 4 };
        } else r.door = best || { side: "s", x: (r.x0 + r.x1) / 2, z: r.z1 - 0.2, touch: 2 };
      }
      for (let c = 0; c < CORES.length; c++) {
        const core = CORES[c];
        if (core.outdoor) {
          core.doorCell = { x: (core.x0 + core.x1) / 2, z: 29.1 };
          continue;
        }
        if (core.door === "south") core.doorCell = { x: (core.x0 + core.x1) / 2, z: core.z1 - 0.4 };
        else if (core.door === "north") core.doorCell = { x: (core.x0 + core.x1) / 2, z: core.z0 + 0.4 };
        else core.doorCell = { x: (core.x0 + core.x1) / 2, z: (core.z0 + core.z1) / 2 };
      }
    }

    _nameRoom(floor, room, n) {
      const cx = (room.x0 + room.x1) / 2;
      const cz = (room.z0 + room.z1) / 2;
      const num = (floor + 1) * 100 + n;
      if (room.labelName) {
        const kind = room.kind || "class";
        if (kind === "auditorium" && floor !== 2) {
          if (floor <= 1) return { name: "TV." + (floor + 1), sub: "Thư viện số", kind: "library" };
          return { name: "TH." + (floor + 1), sub: "Phòng tự học", kind: "study" };
        }
        if (room.labelName === "P.302" && floor !== 2) {
          return { name: "P." + num, sub: "Phòng học", kind: "class" };
        }
        return { name: room.labelName, sub: room.labelSub, kind: kind };
      }
      if (room.kind === "auditorium") {
        if (floor === 2) return { name: "A101", sub: "Hội trường Lớn", kind: "auditorium" };
        if (floor <= 1) return { name: "TV." + (floor + 1), sub: "Thư viện số", kind: "library" };
        return { name: "TH." + (floor + 1), sub: "Phòng tự học", kind: "study" };
      }
      if (floor === 2 && cx < -18 && cz < -16) return { name: "P.302", sub: "Lab CSI", kind: "lab" };
      if (room.area < 48 && cx > 24 && cz > -8 && cz < 12) {
        return n % 2 === 0
          ? { name: "WC-N" + (floor + 1), sub: "Vệ sinh Nam", kind: "wc" }
          : { name: "WC-Nữ" + (floor + 1), sub: "Vệ sinh Nữ", kind: "wc" };
      }
      if (cx > 30 && cz < -8) return { name: "P." + num, sub: n % 2 ? "Lab Viễn thông" : "Lab Vi điều khiển", kind: "lab" };
      if (cx > 28 && cz > 12) return { name: "KT." + num, sub: n % 2 ? "Phòng điện PCCC" : "Kho thiết bị", kind: "util" };
      if (cz < -18) return { name: "VP." + num, sub: "Văn phòng khoa", kind: "office" };
      if (cx < -20 && cz > 8) return { name: "H." + num, sub: "Phòng họp", kind: "meet" };
      if (cx > -8 && cx < 18 && cz > 0 && cz < 16 && floor !== 2) return { name: "SV." + num, sub: "Server / Điều khiển", kind: "lab" };
      return { name: "P." + num, sub: "Phòng học", kind: "class" };
    }

    _fillFloorGrid(floor, grid) {
      const walk = this.grid[floor];
      const rid = this.roomId[floor];
      const door = this.door[floor];
      for (let i = 0; i < grid.length; i++) {
        if (grid[i] === COR || grid[i] === STAIR) walk[i] = 1;
      }
      const rooms = this.roomList;
      for (let r = 0; r < rooms.length; r++) {
        const room = rooms[r];
        const meta = this._nameRoom(floor, room, r + 1);
        for (let iz = room.iz; iz < room.iz + room.h && iz < GH; iz++) {
          for (let ix = room.ix; ix < room.ix + room.w && ix < GW; ix++) {
            if (this.types[idx(ix, iz)] !== ROOM && !(room.fixed && this.types[idx(ix, iz)] === ROOM)) {
              if (this.types[idx(ix, iz)] !== ROOM) continue;
            }
            if (this.types[idx(ix, iz)] !== ROOM) continue;
            walk[idx(ix, iz)] = 1;
            rid[idx(ix, iz)] = r;
          }
        }
        if (room.door) {
          const ix = Math.max(0, Math.min(GW - 1, Math.floor(room.door.x - OX)));
          const iz = Math.max(0, Math.min(GH - 1, Math.floor(room.door.z - OZ)));
          door[idx(ix, iz)] = 1;
          walk[idx(ix, iz)] = 1;
          const nbs = [[1, 0], [-1, 0], [0, 1], [0, -1]];
          for (let k = 0; k < 4; k++) {
            const nx = ix + nbs[k][0];
            const nz = iz + nbs[k][1];
            if (nx < 0 || nz < 0 || nx >= GW || nz >= GH) continue;
            if (this.types[idx(nx, nz)] === COR) door[idx(nx, nz)] = 1;
          }
        }
      }
      for (let c = 0; c < CORES.length; c++) {
        const core = CORES[c];
        if (core.outdoor) continue;
        const dc = core.doorCell;
        const ix = Math.max(0, Math.min(GW - 1, Math.floor(dc.x - OX)));
        const iz = Math.max(0, Math.min(GH - 1, Math.floor(dc.z - OZ)));
        door[idx(ix, iz)] = 1;
        const nbs = [[1, 0], [-1, 0], [0, 1], [0, -1], [0, 0]];
        for (let k = 0; k < nbs.length; k++) {
          const nx = Math.max(0, Math.min(GW - 1, ix + nbs[k][0]));
          const nz = Math.max(0, Math.min(GH - 1, iz + nbs[k][1]));
          door[idx(nx, nz)] = 1;
          if (this.types[idx(nx, nz)] === COR || this.types[idx(nx, nz)] === STAIR) walk[idx(nx, nz)] = 1;
        }
      }
      if (floor === 0) {
        stamp(walk, 2, 28, 29.2, 54, 1);
      }
    }

    _buildSlab(floor, group, mats) {
      const shape = new THREE.Shape();
      shape.moveTo(-50, -30);
      shape.lineTo(50, -30);
      shape.lineTo(50, 30);
      shape.lineTo(-50, 30);
      shape.closePath();
      for (let c = 0; c < CORES.length; c++) {
        const core = CORES[c];
        if (core.outdoor) continue;
        const hole = new THREE.Path();
        const x0 = core.x0 + 0.2;
        const x1 = core.x1 - 0.2;
        const z0 = core.z0 + 0.2;
        const z1 = core.z1 - 0.2;
        hole.moveTo(x0, z0);
        hole.lineTo(x1, z0);
        hole.lineTo(x1, z1);
        hole.lineTo(x0, z1);
        hole.closePath();
        shape.holes.push(hole);
      }
      const geo = new THREE.ExtrudeGeometry(shape, { depth: SLAB, bevelEnabled: false });
      geo.rotateX(Math.PI / 2);
      geo.translate(0, SLAB, 0);
      const slab = new THREE.Mesh(geo, mats.slab);
      slab.receiveShadow = true;
      group.add(slab);
      const cors = corridors();
      for (let i = 0; i < cors.length; i++) {
        const c = cors[i];
        const w = c[1] - c[0];
        const d = c[3] - c[2];
        if (w < 0.4 || d < 0.4) continue;
        const mat = new THREE.MeshStandardMaterial({
          map: this._tiled(this._tileTex, w / 0.85, d / 0.85),
          roughness: 0.78
        });
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, 0.04, d), mat);
        mesh.position.set((c[0] + c[1]) / 2, SLAB + 0.03, (c[2] + c[3]) / 2);
        mesh.receiveShadow = true;
        group.add(mesh);
      }
      const rooms = this.roomList || [];
      for (let i = 0; i < rooms.length; i++) {
        const r = rooms[i];
        const w = Math.max(0.4, r.x1 - r.x0 - 0.3);
        const d = Math.max(0.4, r.z1 - r.z0 - 0.3);
        const mat = new THREE.MeshStandardMaterial({
          map: this._tiled(this._roomTex, w / 1.4, d / 1.4),
          color: 0xf3e6d0,
          roughness: 0.74
        });
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, 0.035, d), mat);
        mesh.position.set((r.x0 + r.x1) / 2, SLAB + 0.025, (r.z0 + r.z1) / 2);
        mesh.receiveShadow = true;
        group.add(mesh);
      }
      void floor;
    }

    _wallSign(group, x, z, side, tex) {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(1.45, 0.36), new THREE.MeshBasicMaterial({ map: tex, transparent: true }));
      m.position.set(x, SLAB + 2.42, z);
      if (side === "n") { m.position.z -= 0.18; m.rotation.y = Math.PI; }
      else if (side === "s") m.position.z += 0.18;
      else if (side === "w") { m.position.x -= 0.18; m.rotation.y = Math.PI / 2; }
      else { m.position.x += 0.18; m.rotation.y = -Math.PI / 2; }
      group.add(m);
    }

    _addWallBox(floor, group, mat, x0, x1, z0, z1, southList) {
      const h = WALL_H;
      const y0 = SLAB;
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(Math.max(0.08, x1 - x0), h, Math.max(0.08, z1 - z0)), mat);
      mesh.position.set((x0 + x1) / 2, y0 + h / 2, (z0 + z1) / 2);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      group.add(mesh);
      this.physics.addBox(floor, Math.min(x0, x1), Math.max(x0, x1), Math.min(z0, z1), Math.max(z0, z1));
      if (southList && z0 > 28.4) southList.push(mesh);
      return mesh;
    }

    _wallWithDoor(floor, group, mat, x0, z0, x1, z1, doorX, doorZ, doorW, southList) {
      const horiz = Math.abs(z1 - z0) < Math.abs(x1 - x0);
      if (horiz) {
        const zA = Math.min(z0, z1);
        const zB = zA + 0.2;
        const xa = Math.min(x0, x1);
        const xb = Math.max(x0, x1);
        const dc = doorX == null ? null : Math.max(xa + 0.8, Math.min(xb - 0.8, doorX));
        if (dc == null) {
          this._addWallBox(floor, group, mat, xa, xb, zA, zB, southList);
          return;
        }
        const g0 = dc - doorW * 0.5;
        const g1 = dc + doorW * 0.5;
        if (g0 - xa > 0.15) this._addWallBox(floor, group, mat, xa, g0, zA, zB, southList);
        if (xb - g1 > 0.15) this._addWallBox(floor, group, mat, g1, xb, zA, zB, southList);
        this._doorFrame(group, dc, (zA + zB) / 2, true);
      } else {
        const xA = Math.min(x0, x1);
        const xB = xA + 0.2;
        const za = Math.min(z0, z1);
        const zb = Math.max(z0, z1);
        const dc = doorZ == null ? null : Math.max(za + 0.8, Math.min(zb - 0.8, doorZ));
        if (dc == null) {
          this._addWallBox(floor, group, mat, xA, xB, za, zb, southList);
          return;
        }
        const g0 = dc - doorW * 0.5;
        const g1 = dc + doorW * 0.5;
        if (g0 - za > 0.15) this._addWallBox(floor, group, mat, xA, xB, za, g0, southList);
        if (zb - g1 > 0.15) this._addWallBox(floor, group, mat, xA, xB, g1, zb, southList);
        this._doorFrame(group, (xA + xB) / 2, dc, false);
      }
    }

    _windowUnit(group, mats, x, y, z, faceX) {
      const glass = new THREE.Mesh(
        new THREE.BoxGeometry(faceX ? 0.05 : 1.85, 1.2, faceX ? 1.85 : 0.05),
        mats.glass
      );
      glass.position.set(x, y, z);
      group.add(glass);
      const add = (w, h, d, ox, oy, oz) => {
        const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mats.frame);
        m.position.set(x + ox, y + oy, z + oz);
        group.add(m);
      };
      if (faceX) {
        add(0.07, 0.07, 2.05, 0, 0.68, 0);
        add(0.07, 0.07, 2.05, 0, -0.68, 0);
        add(0.07, 1.4, 0.07, 0, 0, 1.0);
        add(0.07, 1.4, 0.07, 0, 0, -1.0);
      } else {
        add(2.05, 0.07, 0.07, 0, 0.68, 0);
        add(2.05, 0.07, 0.07, 0, -0.68, 0);
        add(0.07, 1.4, 0.07, 1.0, 0, 0);
        add(0.07, 1.4, 0.07, -1.0, 0, 0);
      }
    }

    _windows(floor, group, mats) {
      const y = SLAB + 1.75;
      for (let x = -44; x <= 44; x += 6) this._windowUnit(group, mats, x, y, -29.9, false);
      for (let z = -22; z <= 22; z += 6) {
        this._windowUnit(group, mats, -49.9, y, z, true);
        this._windowUnit(group, mats, 49.9, y, z, true);
      }
      if (floor === LEVELS - 1) {
        const roof = new THREE.Mesh(new THREE.BoxGeometry(101.2, 0.22, 61.2), mats.slab);
        roof.position.set(0, SLAB + WALL_H + 0.12, 0);
        group.add(roof);
        const rimH = 0.55;
        const rims = [
          [-50.4, 50.4, -30.3, -29.7],
          [-50.4, 50.4, 29.7, 30.3],
          [-50.4, -49.6, -30, 30],
          [49.6, 50.4, -30, 30]
        ];
        for (let i = 0; i < rims.length; i++) {
          const r = rims[i];
          const box = new THREE.Mesh(new THREE.BoxGeometry(r[1] - r[0], rimH, r[3] - r[2]), mats.brick);
          box.position.set((r[0] + r[1]) / 2, SLAB + WALL_H + rimH / 2, (r[2] + r[3]) / 2);
          group.add(box);
        }
      }
      if (floor === 0) {
        const canopy = new THREE.Mesh(new THREE.BoxGeometry(7.2, 0.12, 2.6), mats.slab);
        canopy.position.set(8.3, SLAB + 3.15, 31.1);
        group.add(canopy);
        for (let s = -1; s <= 1; s += 2) {
          const post = new THREE.Mesh(new THREE.BoxGeometry(0.18, 3.1, 0.18), mats.col);
          post.position.set(8.3 + s * 3.2, SLAB + 1.55, 32.1);
          group.add(post);
        }
      }
    }

    _doorFrame(group, x, z, horiz) {
      const y = SLAB + 1.12;
      const jamb = this.mats.frame;
      const hinge = new THREE.Group();
      hinge.position.set(horiz ? x - 0.52 : x, y, horiz ? z : z - 0.52);
      hinge.rotation.y = horiz ? 1.25 : -1.25;
      const leaf = new THREE.Mesh(new THREE.BoxGeometry(horiz ? 0.98 : 0.06, 2.1, horiz ? 0.06 : 0.98), this.mats.door);
      leaf.position.set(horiz ? 0.49 : 0, 0, horiz ? 0 : 0.49);
      const glass = new THREE.Mesh(new THREE.BoxGeometry(horiz ? 0.4 : 0.02, 0.48, horiz ? 0.02 : 0.4), this.mats.glass);
      glass.position.set(horiz ? 0.5 : 0.04, 0.62, horiz ? 0.04 : 0.5);
      hinge.add(leaf, glass);
      if (horiz) {
        const left = new THREE.Mesh(new THREE.BoxGeometry(0.08, 2.25, 0.14), jamb);
        left.position.set(x - 0.62, y, z);
        const right = new THREE.Mesh(new THREE.BoxGeometry(0.08, 2.25, 0.14), jamb);
        right.position.set(x + 0.62, y, z);
        const lintel = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.1, 0.16), jamb);
        lintel.position.set(x, SLAB + 2.22, z);
        group.add(hinge, left, right, lintel);
      } else {
        const a = new THREE.Mesh(new THREE.BoxGeometry(0.14, 2.25, 0.08), jamb);
        a.position.set(x, y, z - 0.62);
        const b = new THREE.Mesh(new THREE.BoxGeometry(0.14, 2.25, 0.08), jamb);
        b.position.set(x, y, z + 0.62);
        const lintel = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.1, 1.4), jamb);
        lintel.position.set(x, SLAB + 2.22, z);
        group.add(hinge, a, b, lintel);
      }
    }

    _buildFinish(floor, group, mats) {
      const cors = corridors();
      let lamps = 0;
      for (let i = 0; i < cors.length; i++) {
        const c = cors[i];
        const w = c[1] - c[0];
        const d = c[3] - c[2];
        if (w < 1.2 || d < 1.2) continue;
        const ceil = new THREE.Mesh(new THREE.BoxGeometry(w, 0.05, d), mats.ceiling);
        ceil.position.set((c[0] + c[1]) / 2, 3.22, (c[2] + c[3]) / 2);
        group.add(ceil);
        const alongX = w >= d;
        const span = alongX ? w : d;
        const n = Math.max(1, Math.round(span / 5.5));
        for (let k = 0; k < n; k++) {
          const t = (k + 0.5) / n;
          const lx = alongX ? c[0] + t * w : (c[0] + c[1]) / 2;
          const lz = alongX ? (c[2] + c[3]) / 2 : c[2] + t * d;
          const light = new THREE.Mesh(
            new THREE.BoxGeometry(alongX ? 1.4 : 0.28, 0.04, alongX ? 0.28 : 1.4),
            mats.lamp
          );
          light.position.set(lx, 3.16, lz);
          group.add(light);
        }
        if (lamps < 2 && span > 14) {
          const pl = new THREE.PointLight(0xfff1d0, 0.65, 26, 2);
          pl.position.set((c[0] + c[1]) / 2, 2.7, (c[2] + c[3]) / 2);
          group.add(pl);
          lamps++;
        }
        const skirtH = 0.18;
        if (alongX) {
          const s1 = new THREE.Mesh(new THREE.BoxGeometry(w, skirtH, 0.05), mats.skirt);
          s1.position.set((c[0] + c[1]) / 2, SLAB + skirtH / 2, c[2]);
          const s2 = new THREE.Mesh(new THREE.BoxGeometry(w, skirtH, 0.05), mats.skirt);
          s2.position.set((c[0] + c[1]) / 2, SLAB + skirtH / 2, c[3]);
          group.add(s1, s2);
        }
      }
      void floor;
    }

    _buildWalls(floor, group, mats, dummy, rooms) {
      const south = [];
      this.southWalls[floor] = new THREE.Group();
      group.add(this.southWalls[floor]);
      const ext = [
        [-50, -49.7, -30, 30],
        [49.7, 50, -30, 30],
        [-50, 50, -30, -29.7]
      ];
      for (let i = 0; i < ext.length; i++) {
        this._addWallBox(floor, group, mats.brick, ext[i][0], ext[i][1], ext[i][2], ext[i][3], null);
      }
      if (floor === 0) {
        this._wallWithDoor(floor, group, mats.brick, -50, 29.7, 5.2, 29.95, null, null, 0, null);
        this._wallWithDoor(floor, group, mats.brick, 11.4, 29.7, 50, 29.95, null, null, 0, null);
      } else {
        this._addWallBox(floor, group, mats.brick, -50, 50, 29.7, 30, null);
      }
      this._windows(floor, group, mats);

      for (let i = 0; i < rooms.length; i++) {
        const r = rooms[i];
        const meta = this._nameRoom(floor, r, i + 1);
        const d = r.door;
        const dw = 1.45;
        const doorOn = function (side) { return d && d.side === side; };
        const cx = (r.x0 + r.x1) / 2;
        const cz = (r.z0 + r.z1) / 2;
        const pull = 0.06;
        const nZ = r.z0 + (cz > r.z0 ? pull : -pull);
        const sZ = r.z1 + (cz < r.z1 ? -pull : pull);
        const wX = r.x0 + (cx > r.x0 ? pull : -pull);
        const eX = r.x1 + (cx < r.x1 ? -pull : pull);
        this._wallWithDoor(floor, group, mats.wall, r.x0, nZ, r.x1, nZ, doorOn("n") ? d.x : null, null, dw);
        this._wallWithDoor(floor, group, mats.wall, r.x0, sZ, r.x1, sZ, doorOn("s") ? d.x : null, null, dw);
        this._wallWithDoor(floor, group, mats.wall, wX, r.z0, wX, r.z1, null, doorOn("w") ? d.z : null, dw);
        this._wallWithDoor(floor, group, mats.wall, eX, r.z0, eX, r.z1, null, doorOn("e") ? d.z : null, dw);
        if (d) this._wallSign(group, d.x, d.z, d.side, makeLabelTexture(meta.name, meta.sub));
      }

      for (let c = 0; c < CORES.length; c++) {
        const core = CORES[c];
        if (core.outdoor) continue;
        const t = 0.22;
        const dc = core.doorCell;
        if (core.door === "south") {
          this._wallWithDoor(floor, group, mats.wallS, core.x0, core.z1, core.x1, core.z1, dc.x, null, 1.3);
          this._addWallBox(floor, group, mats.wallS, core.x0, core.x0 + t, core.z0, core.z1);
          this._addWallBox(floor, group, mats.wallS, core.x1 - t, core.x1, core.z0, core.z1);
          this._addWallBox(floor, group, mats.wallS, core.x0, core.x1, core.z0, core.z0 + t);
        } else {
          this._wallWithDoor(floor, group, mats.wallS, core.x0, core.z0, core.x1, core.z0, dc.x, null, 1.3);
          this._addWallBox(floor, group, mats.wallS, core.x0, core.x0 + t, core.z0, core.z1);
          this._addWallBox(floor, group, mats.wallS, core.x1 - t, core.x1, core.z0, core.z1);
          this._addWallBox(floor, group, mats.wallS, core.x0, core.x1, core.z1 - t, core.z1);
        }
        const side = core.door === "north" ? "n" : "s";
        this._wallSign(group, dc.x, dc.z, side, makeLabelTexture(core.name, "Cầu thang"));
      }
      void dummy;
      void south;
    }

    _buildColumns(floor, group, mats) {
      const positions = [];
      for (let x = -48; x <= 48; x += 8) {
        for (let z = -24; z <= 24; z += 8) {
          const ix = Math.floor(x - OX);
          const iz = Math.floor(z - OZ);
          if (ix < 0 || iz < 0 || ix >= GW || iz >= GH) continue;
          const t = this.types[idx(ix, iz)];
          if (t === COR || t === STAIR || t === OUT) continue;
          let edge = false;
          for (let dz = -1; dz <= 1 && !edge; dz++) {
            for (let dx = -1; dx <= 1; dx++) {
              const nx = ix + dx;
              const nz = iz + dz;
              if (nx < 0 || nz < 0 || nx >= GW || nz >= GH) { edge = true; break; }
              const nt = this.types[idx(nx, nz)];
              if (nt === COR || nt === STAIR || nt === OUT) { edge = true; break; }
            }
          }
          if (edge) continue;
          if (this.door[floor][idx(ix, iz)]) continue;
          positions.push({ x: x, z: z });
          this.physics.addBox(floor, x - 0.22, x + 0.22, z - 0.22, z + 0.22);
        }
      }
      if (!positions.length) return;
      const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(0.42, WALL_H, 0.42), mats.col, positions.length);
      mesh.castShadow = true;
      const d = new THREE.Object3D();
      for (let i = 0; i < positions.length; i++) {
        d.position.set(positions[i].x, SLAB + WALL_H / 2, positions[i].z);
        d.scale.set(1, 1, 1);
        d.rotation.set(0, 0, 0);
        d.updateMatrix();
        mesh.setMatrixAt(i, d.matrix);
      }
      group.add(mesh);
    }

    _buildRoomsVisual(floor, group, mats, rooms) {
      const desks = [];
      const seats = [];
      for (let i = 0; i < rooms.length; i++) {
        const r = rooms[i];
        const meta = this._nameRoom(floor, r, i + 1);
        if (meta.kind === "auditorium" && floor === 2) {
          for (let row = 0; row < 8; row++) {
            const z = r.z0 + 1.6 + row * 1.05;
            const y = SLAB + 0.18 + row * 0.12;
            for (let s = 0; s < 14; s++) {
              const x = r.x0 + 1.4 + s * 1.15;
              if (x > r.x1 - 1.2) break;
              if (Math.abs(x - (r.x0 + r.x1) / 2) < 0.7) continue;
              seats.push({ x: x, y: y, z: z });
            }
          }
          const podium = new THREE.Mesh(new THREE.BoxGeometry(4.5, 0.35, 1.4), mats.wood);
          podium.position.set((r.x0 + r.x1) / 2, SLAB + 0.2, r.z0 + 0.9);
          group.add(podium);
          this.physics.addBox(floor, (r.x0 + r.x1) / 2 - 2.2, (r.x0 + r.x1) / 2 + 2.2, r.z0 + 0.3, r.z0 + 1.6);
          continue;
        }
        if (meta.kind === "wc" || meta.kind === "util") continue;
        const door = r.door || { side: "s" };
        const cx = (r.x0 + r.x1) / 2;
        const cz = (r.z0 + r.z1) / 2;
        let bx = cx;
        let bz = r.z0 + 0.35;
        let face = 0;
        if (door.side === "n") { bz = r.z1 - 0.35; face = Math.PI; }
        else if (door.side === "s") { bz = r.z0 + 0.35; face = 0; }
        else if (door.side === "w") { bx = r.x1 - 0.35; bz = cz; face = -Math.PI / 2; }
        else { bx = r.x0 + 0.35; bz = cz; face = Math.PI / 2; }
        const board = new THREE.Mesh(new THREE.BoxGeometry(door.side === "w" || door.side === "e" ? 0.06 : 2.4, 1.05, door.side === "w" || door.side === "e" ? 2.4 : 0.06), mats.board);
        board.position.set(bx, SLAB + 1.7, bz);
        group.add(board);
        const teach = new THREE.Mesh(new THREE.BoxGeometry(1.3, 0.75, 0.6), mats.desk);
        const tx = cx + (bx - cx) * 0.35;
        const tz = cz + (bz - cz) * 0.35;
        teach.position.set(tx, SLAB + 0.4, tz);
        group.add(teach);
        this.physics.addBox(floor, tx - 0.65, tx + 0.65, tz - 0.3, tz + 0.3);
        const aisle = cx;
        for (let z = r.z0 + 1.8; z < r.z1 - 1.4; z += 1.7) {
          for (let x = r.x0 + 1.3; x < r.x1 - 1.1; x += 1.8) {
            if (Math.abs(x - aisle) < 0.7) continue;
            if (Math.hypot(x - tx, z - tz) < 1.3) continue;
            if (Math.hypot(x - bx, z - bz) < 1.2) continue;
            desks.push({ x: x, z: z, rot: face });
            this.physics.addBox(floor, x - 0.4, x + 0.4, z - 0.28, z + 0.28);
          }
        }
        void face;
      }
      if (desks.length) {
        const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1.05, 0.74, 0.55), mats.desk, desks.length);
        const d = new THREE.Object3D();
        for (let i = 0; i < desks.length; i++) {
          d.position.set(desks[i].x, SLAB + 0.38, desks[i].z);
          d.rotation.set(0, desks[i].rot || 0, 0);
          d.scale.set(1, 1, 1);
          d.updateMatrix();
          mesh.setMatrixAt(i, d.matrix);
        }
        group.add(mesh);
      }
      if (seats.length) {
        const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(0.48, 0.42, 0.48), mats.seat, seats.length);
        const d = new THREE.Object3D();
        for (let i = 0; i < seats.length; i++) {
          d.position.set(seats[i].x, seats[i].y, seats[i].z);
          d.rotation.set(0, 0, 0);
          d.scale.set(1, 1, 1);
          d.updateMatrix();
          mesh.setMatrixAt(i, d.matrix);
        }
        group.add(mesh);
      }
    }

    _buildStairs(floor, group, mats) {
      if (!this.stairRigs) this.stairRigs = [];
      for (let c = 0; c < CORES.length; c++) {
        const core = CORES[c];
        if (core.outdoor) continue;
        const goesDown = core.links.some(function (L) { return L[0] === floor; });
        if (!goesDown) continue;
        const spec = buildUStair(core);
        spec.floor = floor;
        this.physics.addStair(spec);
        const rig = new THREE.Group();
        rig.position.y = spec.yTop;
        this._stairMesh(rig, mats, spec);
        this._stairRails(floor, rig, mats, core, spec);
        group.add(rig);
        this.stairRigs.push({ rig: rig, floor: floor, span: spec.yTop - spec.yBot });
      }
      if (floor === 0) {
        for (let i = 0; i < 4; i++) {
          const z = 29.4 + i * 0.35;
          const step = new THREE.Mesh(new THREE.BoxGeometry(5.6, 0.12, 0.35), mats.step);
          step.position.set(8.3, 0.3 - i * 0.08, z);
          group.add(step);
        }
      }
    }

    _stairBox(spec, along, crossPos, yTop, rise, tread) {
      const p = spec.map(along, crossPos);
      const wide = spec.alongZ;
      const box = new THREE.Mesh(
        new THREE.BoxGeometry(wide ? spec.flightW : tread, rise, wide ? tread : spec.flightW),
        this.mats.step
      );
      box.position.set(p.x, yTop - rise * 0.5, p.z);
      const noseAlong = spec.core.door === "south" || spec.core.door === "east" ? along - tread * 0.45 : along + tread * 0.45;
      const np = spec.map(noseAlong, crossPos);
      const nose = new THREE.Mesh(
        new THREE.BoxGeometry(wide ? spec.flightW : 0.05, 0.02, wide ? 0.05 : spec.flightW),
        this.mats.nosing
      );
      nose.position.set(np.x, yTop + 0.012, np.z);
      return [box, nose];
    }

    _stairMesh(group, mats, spec) {
      const steps = 13;
      const base = spec.yTop;
      const yTop = 0;
      const yMid = spec.yMid - base;
      const yBot = spec.yBot - base;
      const rise = (yTop - yMid) / steps;
      const tread = spec.run / steps + 0.02;
      for (let side = 0; side < 2; side++) {
        const crossPos = side === 0 ? spec.flightW * 0.5 : spec.cross - spec.flightW * 0.5;
        for (let i = 0; i < steps; i++) {
          const t1 = (i + 1) / steps;
          const along = side === 0
            ? spec.entry + (i + 0.5) / steps * spec.run
            : spec.entry + (1 - (i + 0.5) / steps) * spec.run;
          const y = side === 0 ? yTop + (yMid - yTop) * t1 : yMid + (yBot - yMid) * t1;
          const parts = this._stairBox(spec, along, crossPos, y, rise, tread);
          group.add(parts[0], parts[1]);
        }
      }
      const core = spec.core;
      const farAlong = spec.entry + spec.run + spec.far * 0.5;
      const mid = spec.map(farAlong, spec.cross * 0.5);
      const landW = Math.max(0.4, core.x1 - core.x0 - 0.4);
      const landD = Math.max(0.7, spec.far - 0.1);
      const land = new THREE.Mesh(new THREE.BoxGeometry(spec.alongZ ? landW : landD, 0.16, spec.alongZ ? landD : landW), mats.step);
      land.position.set(spec.alongZ ? (core.x0 + core.x1) / 2 : mid.x, yMid, spec.alongZ ? mid.z : (core.z0 + core.z1) / 2);
      group.add(land);
    }

    _railBetween(group, mats, ax, ay, az, bx, by, bz) {
      const dy = by - ay;
      const dz = bz - az;
      const len = Math.hypot(dy, dz);
      if (len < 0.2) return;
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.05, len), mats.rail);
      mesh.position.set((ax + bx) / 2, (ay + by) / 2, (az + bz) / 2);
      mesh.rotation.x = Math.atan2(-dy, dz);
      group.add(mesh);
    }

    _stairRails(floor, group, mats, core, spec) {
      const p0 = spec.map(spec.entry + 0.25, spec.cross * 0.5);
      const p1 = spec.map(spec.entry + spec.run - 0.2, spec.cross * 0.5);
      const xx0 = core.x0 + spec.flightW + 0.08;
      const xx1 = core.x0 + spec.cross - spec.flightW - 0.08;
      this.physics.addBox(floor, Math.min(xx0, xx1), Math.max(xx0, xx1), Math.min(p0.z, p1.z), Math.max(p0.z, p1.z));
      const hand = 0.9;
      const base = spec.yTop;
      const flights = [
        { side: 0, y0: spec.yTop - base, y1: spec.yMid - base },
        { side: 1, y0: spec.yMid - base, y1: spec.yBot - base }
      ];
      for (let f = 0; f < flights.length; f++) {
        const side = flights[f].side;
        const inner = side === 0 ? spec.flightW - 0.05 : spec.cross - spec.flightW + 0.05;
        const outer = side === 0 ? 0.2 : spec.cross - 0.2;
        for (let e = 0; e < 2; e++) {
          const crossPos = e === 0 ? inner : outer;
          const a = spec.map(spec.entry, crossPos);
          const b = spec.map(spec.entry + spec.run, crossPos);
          const y0 = flights[f].y0 + hand;
          const y1 = flights[f].y1 + hand;
          if (side === 0) this._railBetween(group, mats, a.x, y0, a.z, b.x, y1, b.z);
          else this._railBetween(group, mats, b.x, y0, b.z, a.x, y1, a.z);
        }
      }
    }

    _buildLeds(floor, group, mats) {
      const spots = [
        { x: -40.5, z: -18.5, face: 0 },
        { x: -23, z: -18.5, face: 0 },
        { x: 11.4, z: -12, face: Math.PI / 2 },
        { x: -40.5, z: -0.7, face: Math.PI / 2 },
        { x: -15, z: 8, face: 0 },
        { x: 37.4, z: 12, face: Math.PI / 2 },
        { x: -20, z: 18.7, face: 0 },
        { x: 8, z: 18.7, face: 0 },
        { x: 40, z: 18.7, face: 0 }
      ];
      const tex = makeLabelTexture("LỐI THOÁT", "");
      for (let i = 0; i < spots.length; i++) {
        const s = spots[i];
        const ix = Math.max(0, Math.min(GW - 1, Math.floor(s.x - OX)));
        const iz = Math.max(0, Math.min(GH - 1, Math.floor(s.z - OZ)));
        const g = new THREE.Group();
        g.position.set(s.x, 0, s.z);
        g.rotation.y = s.face;
        const housingMat = mats.led.clone();
        const housing = new THREE.Mesh(new THREE.BoxGeometry(1.35, 0.42, 0.07), housingMat);
        housing.position.y = 2.55;
        g.add(housing);
        const faceMat = new THREE.MeshBasicMaterial({ map: tex, transparent: true });
        const face = new THREE.Mesh(new THREE.PlaneGeometry(1.2, 0.34), faceMat);
        face.position.set(0, 2.55, 0.045);
        g.add(face);
        const arrow = new THREE.Group();
        arrow.position.set(0.28, 2.55, 0.06);
        const am = mats.ledArrow.clone();
        const a1 = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.045, 0.02), am);
        a1.position.set(0.02, 0.07, 0);
        a1.rotation.z = 0.55;
        const a2 = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.045, 0.02), am);
        a2.position.set(0.02, -0.07, 0);
        a2.rotation.z = -0.55;
        arrow.add(a1, a2);
        g.add(arrow);
        group.add(g);
        this.leds.push({
          floor: floor, x: s.x, z: s.z, ix: ix, iz: iz,
          housing: housing, rig: g, arrow: arrow, face: s.face, phase: i
        });
      }
    }

    _floorSign(floor, group) {
      const tex = makeLabelTexture("TẦNG " + (floor + 1), floor === 2 ? "Lab CSI" : "WiEvac");
      const sign = new THREE.Mesh(new THREE.PlaneGeometry(2.2, 0.55), new THREE.MeshBasicMaterial({ map: tex, transparent: true }));
      sign.position.set(-36, SLAB + 2.4, -19.9);
      group.add(sign);
    }

    _ground(mats) {
      const yard = new THREE.Shape();
      yard.moveTo(-90, -40);
      yard.lineTo(90, -40);
      yard.lineTo(90, 90);
      yard.lineTo(-90, 90);
      yard.closePath();
      const cut = new THREE.Path();
      cut.moveTo(-51, -31);
      cut.lineTo(-51, 31);
      cut.lineTo(51, 31);
      cut.lineTo(51, -31);
      cut.closePath();
      yard.holes.push(cut);
      const yardGeo = new THREE.ExtrudeGeometry(yard, { depth: 0.04, bevelEnabled: false });
      yardGeo.rotateX(Math.PI / 2);
      yardGeo.translate(0, 0.02, 0);
      const lawn = new THREE.Mesh(yardGeo, mats.grass);
      lawn.receiveShadow = true;
      this.scene.add(lawn);
      const path = new THREE.Mesh(new THREE.BoxGeometry(6, 0.04, 18), mats.path);
      path.position.set(this.assembly.x, 0.02, 34);
      this.scene.add(path);
      const ring = new THREE.Mesh(new THREE.RingGeometry(6.2, 8.4, 48), mats.safe);
      ring.rotation.x = -Math.PI / 2;
      ring.position.set(this.assembly.x, 0.04, this.assembly.z);
      this.scene.add(ring);
      const label = new THREE.Mesh(new THREE.PlaneGeometry(4.2, 1.05), new THREE.MeshBasicMaterial({ map: makeLabelTexture("ĐIỂM TẬP KẾT", "An toàn"), transparent: true, side: THREE.DoubleSide }));
      label.position.set(this.assembly.x, 2.2, this.assembly.z + 6);
      this.scene.add(label);
      const canopyMat = new THREE.MeshStandardMaterial({ color: 0x166534, roughness: 0.85 });
      const trunkMat = new THREE.MeshStandardMaterial({ color: 0x6b4423, roughness: 0.9 });
      const trees = new THREE.InstancedMesh(new THREE.SphereGeometry(1.15, 8, 6), canopyMat, 18);
      const trunks = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.12, 0.16, 1.3, 6), trunkMat, 18);
      const d = new THREE.Object3D();
      for (let i = 0; i < 18; i++) {
        const x = i < 9 ? -58 + (i % 9) * 3.2 : 32 + (i % 9) * 3.2;
        const z = 42 + Math.floor(i / 9) * 5;
        d.position.set(x, 2.15, z);
        d.rotation.set(0, 0, 0);
        d.scale.set(1, 1, 1);
        d.updateMatrix();
        trees.setMatrixAt(i, d.matrix);
        d.position.set(x, 0.7, z);
        d.updateMatrix();
        trunks.setMatrixAt(i, d.matrix);
      }
      this.scene.add(trees);
      this.scene.add(trunks);
    }

    stairGoal(core) {
      const x = core.x0 + (core.x1 - core.x0) * 0.78;
      let z = (core.z0 + core.z1) / 2;
      if (core.door === "south") z = core.z1 - 0.9;
      else if (core.door === "north") z = core.z0 + 0.9;
      return { x: x, z: z };
    }

    _portals() {
      this.mouths = [];
      for (let c = 0; c < CORES.length; c++) {
        const core = CORES[c];
        for (let i = 0; i < core.links.length; i++) {
          const up = core.links[i][0];
          const down = core.links[i][1];
          if (up < 0 || down < 0) continue;
          const dc = core.doorCell;
          const ix = Math.max(0, Math.min(GW - 1, Math.floor(dc.x - OX)));
          const iz = Math.max(0, Math.min(GH - 1, Math.floor(dc.z - OZ)));
          this.portals.push({
            id: core.id,
            name: core.name,
            up: up,
            down: down,
            ix: ix,
            iz: iz,
            x: dc.x,
            z: dc.z,
            door: core.door,
            cost: core.id === "SE" ? 6 : 14
          });
        }
        if (!core.outdoor && core.doorCell) {
          const floors = {};
          for (let i = 0; i < core.links.length; i++) {
            if (core.links[i][0] >= 0) floors[core.links[i][0]] = 1;
            if (core.links[i][1] >= 0) floors[core.links[i][1]] = 1;
          }
          const goal = this.stairGoal(core);
          this.mouths.push({
            id: core.id,
            name: core.name,
            x: core.doorCell.x,
            z: core.doorCell.z,
            gx: goal.x,
            gz: goal.z,
            floors: floors
          });
        }
      }
    }

    _pickSpawn(rooms) {
      let best = null;
      for (let i = 0; i < rooms.length; i++) {
        const meta = this._nameRoom(2, rooms[i], i + 1);
        if (meta.sub === "Lab CSI") {
          best = rooms[i];
          break;
        }
      }
      if (!best) {
        for (let i = 0; i < rooms.length; i++) {
          const cx = (rooms[i].x0 + rooms[i].x1) / 2;
          const cz = (rooms[i].z0 + rooms[i].z1) / 2;
          if (cx > 20 && cz < -16) { best = rooms[i]; break; }
        }
      }
      if (best) {
        let sx = (best.x0 + best.x1) / 2;
        let sz = (best.z0 + best.z1) / 2;
        for (let t = 0; t < 24 && this.physics.overlaps(2, sx, sz, 0.45); t++) {
          sx += 0.7;
          if (sx > best.x1 - 1) { sx = best.x0 + 1.2; sz += 0.7; }
        }
        this.spawn.x = sx;
        this.spawn.z = sz;
        this.spawn.floor = 2;
        this.spawn.room = best;
      }
    }

    canStep(floor, ix, iz, nx, nz) {
      if (nx < 0 || nz < 0 || nx >= GW || nz >= GH) return false;
      const walk = this.grid[floor];
      if (!walk[idx(nx, nz)] || !walk[idx(ix, iz)]) return false;
      const a = this.types[idx(ix, iz)];
      const b = this.types[idx(nx, nz)];
      const ra = this.roomId[floor][idx(ix, iz)];
      const rb = this.roomId[floor][idx(nx, nz)];
      const da = this.door[floor][idx(ix, iz)];
      const db = this.door[floor][idx(nx, nz)];
      if (ra >= 0 && rb >= 0 && ra !== rb) return false;
      if ((a === ROOM && b === COR) || (a === COR && b === ROOM)) return !!(da || db);
      if ((a === STAIR && b === COR) || (a === COR && b === STAIR)) return !!(da || db);
      if ((a === ROOM && b === STAIR) || (a === STAIR && b === ROOM)) return false;
      return true;
    }

    nearestWalk(floor, x, z) {
      let ix = Math.max(0, Math.min(GW - 1, Math.floor(x - OX)));
      let iz = Math.max(0, Math.min(GH - 1, Math.floor(z - OZ)));
      if (this.grid[floor][idx(ix, iz)]) return { ix: ix, iz: iz };
      for (let rad = 1; rad < 12; rad++) {
        for (let dz = -rad; dz <= rad; dz++) {
          for (let dx = -rad; dx <= rad; dx++) {
            const nx = ix + dx;
            const nz = iz + dz;
            if (nx < 0 || nz < 0 || nx >= GW || nz >= GH) continue;
            if (this.grid[floor][idx(nx, nz)]) return { ix: nx, iz: nz };
          }
        }
      }
      return { ix: ix, iz: iz };
    }

    static get GW() { return GW; }
    static get GH() { return GH; }
    static get OX() { return OX; }
    static get OZ() { return OZ; }
    static get CS() { return CS; }
    static get LEVELS() { return LEVELS; }
    static get CORES() { return CORES; }
    static get FH() { return FH; }
  }

  WEV.BuildingEngine = BuildingEngine;
  WEV.GEO = { OX: OX, OZ: OZ, CS: CS, GW: GW, GH: GH, LEVELS: LEVELS, FH: FH, idx: idx, cellX: cellX, cellZ: cellZ };
})();
