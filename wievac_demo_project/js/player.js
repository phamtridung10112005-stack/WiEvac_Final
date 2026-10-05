/* PlayerController — WASD, sprint, chuột 360°, vai thứ 3 / FPS */
(function () {
  "use strict";
  const WEV = (window.WEV = window.WEV || {});
  const THREE = window.THREE;

  class PlayerController {
    constructor(camera, dom) {
      this.camera = camera;
      this.dom = dom;
      this.keys = {};
      this.yaw = 0.4;
      this.pitch = 0.42;
      this.dist = 5.2;
      this.fps = false;
      this.radius = 0.34;
      this.walk = 1.85;
      this.sprint = 3.3;
      this.floor = 2;
      this.x = 28;
      this.z = -24;
      this.y = 0.3;
      this.vx = 0;
      this.vz = 0;
      this.bob = 0;
      this.pointer = false;
      this.dragging = false;
      this.lastX = 0;
      this.lastY = 0;
      this.godDrag = false;
      this.speed = 0;
      this._bind();
    }

    _bind() {
      const keys = this.keys;
      window.addEventListener("keydown", function (e) {
        keys[e.code] = true;
        if (["Space", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].indexOf(e.code) >= 0) e.preventDefault();
      });
      window.addEventListener("keyup", function (e) {
        keys[e.code] = false;
      });
      const self = this;
      this.dom.addEventListener("click", function () {
        if (WEV.view && WEV.view.follow && !self.fps) {
          self.dom.requestPointerLock && self.dom.requestPointerLock();
        }
      });
      document.addEventListener("pointerlockchange", function () {
        self.pointer = document.pointerLockElement === self.dom;
      });
      document.addEventListener("mousemove", function (e) {
        if (!WEV.view || !WEV.view.follow) return;
        if (self.pointer) {
          self.yaw -= e.movementX * 0.0022;
          self.pitch -= e.movementY * 0.0018;
          self.pitch = Math.max(-0.35, Math.min(1.15, self.pitch));
        } else if (self.dragging) {
          const dx = e.clientX - self.lastX;
          const dy = e.clientY - self.lastY;
          self.yaw -= dx * 0.005;
          self.pitch -= dy * 0.004;
          self.pitch = Math.max(-0.2, Math.min(1.2, self.pitch));
          self.lastX = e.clientX;
          self.lastY = e.clientY;
        }
      });
      this.dom.addEventListener("mousedown", function (e) {
        if (!WEV.view || !WEV.view.follow) return;
        if (e.button === 0 || e.button === 2) {
          self.dragging = true;
          self.lastX = e.clientX;
          self.lastY = e.clientY;
        }
      });
      window.addEventListener("mouseup", function () {
        self.dragging = false;
      });
      this.dom.addEventListener("contextmenu", function (e) {
        e.preventDefault();
      });
      window.addEventListener("keydown", function (e) {
        if (e.code === "KeyV" && WEV.view && WEV.view.follow) {
          self.fps = !self.fps;
          if (self.fps) self.dom.requestPointerLock && self.dom.requestPointerLock();
        }
        if (e.code === "Escape") self.fps = false;
      });
      this.dom.addEventListener("wheel", function (e) {
        if (!WEV.view || !WEV.view.follow) return;
        self.dist = Math.max(2.2, Math.min(14, self.dist + Math.sign(e.deltaY) * 0.45));
      }, { passive: true });
    }

    driveToward(tx, tz, dt, physics, crowd) {
      const dx = tx - this.x;
      const dz = tz - this.z;
      const len = Math.hypot(dx, dz);
      if (len > 0.2) {
        const target = Math.atan2(-dx / len, -dz / len);
        let turn = target - this.yaw;
        while (turn > Math.PI) turn -= Math.PI * 2;
        while (turn < -Math.PI) turn += Math.PI * 2;
        this.yaw += turn * Math.min(1, dt * 5);
      }
      const step = Math.min(len, this.sprint * dt);
      const mx = len > 0.001 ? (dx / len) * step : 0;
      const mz = len > 0.001 ? (dz / len) * step : 0;
      const slid = physics.move(this.floor, this.x, this.z, this.radius, mx, mz);
      this.x = slid.x;
      this.z = slid.z;
      const keepX = this.x;
      const keepZ = this.z;
      if (crowd) crowd.separateOne(this, 0.36);
      if (physics.overlaps(this.floor, this.x, this.z, this.radius * 0.9)) {
        this.x = keepX;
        this.z = keepZ;
      }
      this._snapFloor(physics, dt);
      this.speed = step / Math.max(dt, 0.001);
      this.bob += dt * this.speed * 2.4;
    }

    _snapFloor(physics, dt) {
      const stand = physics.sampleY(this.floor, this.x, this.z);
      if (this.floor > 0 && stand < -3.45) {
        this.floor -= 1;
        this.y = 0.3;
        return;
      }
      this.y += (stand - this.y) * Math.min(1, dt * 14);
    }

    update(dt, physics, crowd) {
      const sprint = !!(this.keys.ShiftLeft || this.keys.ShiftRight);
      const speed = sprint ? this.sprint : this.walk;
      let ix = 0;
      let iz = 0;
      if (this.keys.KeyW || this.keys.ArrowUp) iz -= 1;
      if (this.keys.KeyS || this.keys.ArrowDown) iz += 1;
      if (this.keys.KeyA || this.keys.ArrowLeft) ix -= 1;
      if (this.keys.KeyD || this.keys.ArrowRight) ix += 1;
      const len = Math.hypot(ix, iz);
      if (len > 0) {
        ix /= len;
        iz /= len;
      }
      const sin = Math.sin(this.yaw);
      const cos = Math.cos(this.yaw);
      const wx = (ix * cos + iz * sin) * speed;
      const wz = (-ix * sin + iz * cos) * speed;
      const dx = wx * dt;
      const dz = wz * dt;
      const slid = physics.move(this.floor, this.x, this.z, this.radius, dx, dz);
      this.x = slid.x;
      this.z = slid.z;
      if (crowd) crowd.separateOne(this, 0.36);
      this._snapFloor(physics, dt);
      this.speed = Math.hypot(wx, wz);
      this.bob += dt * this.speed * 2.4;
      this._checkStairSwitch(physics);
    }

    _checkStairSwitch(physics) {
      const y = physics.sampleY(this.floor, this.x, this.z);
      if (this.floor > 0 && y < -3.45) {
        this.floor -= 1;
        this.y = 0.3;
      }
    }

    applyCamera(offsetY, physics) {
      const cam = this.camera;
      const bob = Math.sin(this.bob) * (this.speed > 0.35 ? 0.012 : 0);
      const baseY = offsetY(this.floor) + this.y;
      if (!this._cam) this._cam = new THREE.Vector3();
      if (!this._look) this._look = new THREE.Vector3();
      const snap = !this._camReady;
      this._camReady = true;
      if (this.fps) {
        const eye = baseY + 1.62 + bob;
        const look = 1.4;
        const tx = this.x - Math.sin(this.yaw) * 0.08;
        const tz = this.z - Math.cos(this.yaw) * 0.08;
        const next = new THREE.Vector3(tx, eye, tz);
        if (snap) this._cam.copy(next);
        else this._cam.lerp(next, 0.35);
        cam.position.copy(this._cam);
        cam.lookAt(
          this.x - Math.sin(this.yaw) * look,
          eye - Math.sin(this.pitch) * 0.12,
          this.z - Math.cos(this.yaw) * look
        );
        return;
      }
      const backMax = this.dist;
      let back = 1.6;
      if (physics) {
        for (let d = 1.6; d <= backMax; d += 0.4) {
          const px = this.x + Math.sin(this.yaw) * d;
          const pz = this.z + Math.cos(this.yaw) * d;
          if (physics.overlaps(this.floor, px, pz, 0.2)) break;
          back = d;
        }
      } else back = backMax;
      if (this._backSm == null) this._backSm = back;
      this._backSm += (back - this._backSm) * 0.12;
      const height = 1.45 + this.pitch * 0.7;
      const px = this.x + Math.sin(this.yaw) * this._backSm;
      const pz = this.z + Math.cos(this.yaw) * this._backSm;
      const py = baseY + height + bob;
      const nextPos = new THREE.Vector3(px, py, pz);
      const nextLook = new THREE.Vector3(this.x, baseY + 1.35, this.z);
      if (snap) {
        this._cam.copy(nextPos);
        this._look.copy(nextLook);
      } else {
        this._cam.lerp(nextPos, 0.18);
        this._look.lerp(nextLook, 0.22);
      }
      cam.position.copy(this._cam);
      cam.lookAt(this._look);
    }
  }

  WEV.PlayerController = PlayerController;
})();
