/* WiEvac — vòng lặp chính: tòa nhà, vật lý, người chơi, đám đông, CSI, khói */
(function () {
  "use strict";
  window.onerror = function (msg, src, line, col, err) {
    window.__bootErr = String(msg) + " @ " + line + ":" + col + " " + (err && err.stack || "");
  };
  if (!window.THREE || !window.WEV) {
    document.body.innerHTML = "<p style='color:#fff;padding:24px'>Không tải được Three.js hoặc module mô phỏng.</p>";
    return;
  }
  const THREE = window.THREE;
  const WEV = window.WEV;

  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  if (THREE.SRGBColorSpace) renderer.outputColorSpace = THREE.SRGBColorSpace;
  document.body.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x9ec9e8);
  scene.fog = new THREE.Fog(0xc5d8e8, 80, 260);
  const camera = new THREE.PerspectiveCamera(58, window.innerWidth / window.innerHeight, 0.08, 500);

  scene.add(new THREE.HemisphereLight(0xfff7ed, 0x6b8f71, 0.52));
  scene.add(new THREE.AmbientLight(0xfff4e5, 0.22));
  const sun = new THREE.DirectionalLight(0xfff3dd, 1.15);
  sun.position.set(40, 80, 24);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  sun.shadow.camera.left = -80;
  sun.shadow.camera.right = 80;
  sun.shadow.camera.top = 80;
  sun.shadow.camera.bottom = -80;
  scene.add(sun);

  let physics;
  let building;
  try {
    physics = new WEV.PhysicsEngine(4);
    building = new WEV.BuildingEngine(scene, physics);
    window.__boot = "building-ok";
  } catch (err) {
    window.__bootErr = String(err && err.stack || err);
    console.error(err);
    return;
  }
  const player = new WEV.PlayerController(camera, renderer.domElement);
  player.floor = building.spawn.floor;
  player.x = building.spawn.x;
  player.z = building.spawn.z;
  player.y = 0.3;
  window.__boot = "player-ok";

  const you = WEV.makeAvatar({ shirt: 0xf59e0b, hair: 0x3f2a1d, skin: 0xf0c7a4, pants: 0x1e293b });
  scene.add(you);

  const crowd = new WEV.CrowdEngine(scene, building, 400);
  crowd.focusEnt = player;
  window.__boot = "crowd-ok " + crowd.agents.length;
  const wievac = new WEV.WiEvacEngine(scene, building);
  window.__boot = "wievac-ok";
  const vfx = new WEV.VFXEngine(scene, building);
  window.__boot = "vfx-ok";

  WEV.view = { follow: false, mode: "exploded" };
  WEV.drive = "auto";
  building.layoutMode = "exploded";
  building.applyLayout();

  const god = { yaw: 0.55, pitch: 0.62, dist: 150, tx: 0, ty: 18, tz: 0, drag: false, lx: 0, ly: 0 };
  const clock = new THREE.Clock();
  const sim = { running: false, time: 0, dist: 0, scale: 1, saidJam: false, saidExit: false, px: player.x, pz: player.z };

  const $ = function (id) { return document.getElementById(id); };
  const phase = $("phase-badge");
  const status = $("status");
  const subtitle = $("subtitle");
  const subtitleText = $("subtitle-text");

  function say(text, alert) {
    subtitleText.textContent = text;
    subtitle.classList.add("show");
    subtitle.classList.toggle("alert-voice", !!alert);
    vfx.speak(text);
  }

  function setPhase(text, cls) {
    phase.textContent = text;
    phase.className = "phase-badge" + (cls ? " " + cls : "");
  }

  function frameGod() {
    const cp = Math.cos(god.pitch);
    camera.position.set(
      god.tx + Math.sin(god.yaw) * god.dist * cp,
      god.ty + Math.sin(god.pitch) * god.dist,
      god.tz + Math.cos(god.yaw) * god.dist * cp
    );
    camera.lookAt(god.tx, god.ty, god.tz);
  }

  renderer.domElement.addEventListener("mousedown", function (e) {
    if (WEV.view.follow) return;
    god.drag = true;
    god.lx = e.clientX;
    god.ly = e.clientY;
  });
  window.addEventListener("mouseup", function () { god.drag = false; });
  window.addEventListener("mousemove", function (e) {
    if (!god.drag || WEV.view.follow) return;
    god.yaw -= (e.clientX - god.lx) * 0.005;
    god.pitch = Math.max(0.15, Math.min(1.15, god.pitch - (e.clientY - god.ly) * 0.004));
    god.lx = e.clientX;
    god.ly = e.clientY;
  });
  renderer.domElement.addEventListener("wheel", function (e) {
    if (WEV.view.follow) return;
    god.dist = Math.max(30, Math.min(220, god.dist + Math.sign(e.deltaY) * 6));
  }, { passive: true });

  function setFollow(on) {
    WEV.view.follow = on;
    $("btn-god").classList.toggle("active", !on);
    $("btn-follow").classList.toggle("active", on);
    if (on) {
      building.layoutMode = "stacked";
      building.focus = -1;
      WEV.view.mode = "stacked";
    } else if (building.focus < 0) {
      building.layoutMode = "exploded";
    }
    building.applyLayout();
    $("hint").style.display = on ? "block" : "none";
  }

  function setFloor(mode) {
    document.querySelectorAll(".floor-nav button").forEach(function (b) { b.classList.remove("active"); });
    if (mode === "all") {
      $("btn-view-all").classList.add("active");
      building.focus = -1;
      building.layoutMode = WEV.view.follow ? "stacked" : "exploded";
      god.dist = 150;
      god.ty = 22;
      god.tx = 0;
      god.tz = 0;
    } else {
      const f = mode;
      $("btn-view-t" + (f + 1)).classList.add("active");
      building.focus = f;
      building.layoutMode = "single";
      god.dist = 70;
      god.ty = 8;
      god.pitch = 0.85;
    }
    building.applyLayout();
  }

  function setDrive(mode) {
    WEV.drive = mode;
    $("btn-auto").classList.toggle("active", mode === "auto");
    $("btn-manual").classList.toggle("active", mode === "manual");
    $("hint").textContent = mode === "auto"
      ? "Tự động: nhân vật tự đi theo biển chỉ dẫn. Bấm «Tự điều khiển» để dùng WASD."
      : "Tự điều khiển: click chuột để xoay nhìn · W A S D · Shift chạy · V góc nhìn thứ nhất";
  }
  $("btn-auto").onclick = function () { setDrive("auto"); };
  $("btn-manual").onclick = function () { setDrive("manual"); setFollow(true); };
  $("btn-god").onclick = function () { setFollow(false); };
  $("btn-follow").onclick = function () { setFollow(true); };
  $("btn-view-all").onclick = function () { setFollow(false); setFloor("all"); };
  $("btn-view-t1").onclick = function () { setFloor(0); };
  $("btn-view-t2").onclick = function () { setFloor(1); };
  $("btn-view-t3").onclick = function () { setFloor(2); };
  $("btn-view-t4").onclick = function () { setFloor(3); };
  $("btn-view-t5").onclick = function () { setFloor(4); };
  [["btn-spd-1", 1], ["btn-spd-15", 1.5], ["btn-spd-2", 2]].forEach(function (pair) {
    $(pair[0]).onclick = function () {
      sim.scale = pair[1];
      document.querySelectorAll(".btn-spd").forEach(function (b) { b.classList.remove("active"); });
      $(pair[0]).classList.add("active");
    };
  });

  $("btn-start").onclick = function () {
    if (sim.running) return;
    sim.running = true;
    setDrive("auto");
    setFollow(true);
    setPhase("Báo cháy — tới cầu thang gần nhất", "alert");
    status.textContent = "Đi theo vạch sáng tới cầu thang gần bạn. Cầu thang đông sẽ được điều phối.";
    say("Chú ý, có sự cố cháy tại cánh Đông tầng ba. Di chuyển theo đèn chỉ dẫn tới cầu thang gần nhất.");
    vfx.startSiren();
    $("btn-start").disabled = true;
  };
  $("btn-reset").onclick = function () { location.reload(); };
  $("btn-mute").onclick = function () {
    vfx.setMuted(!vfx.muted);
    $("btn-mute").textContent = vfx.muted ? "Bật loa" : "Tắt loa";
    if (!vfx.muted && sim.running) vfx.startSiren();
  };

  window.addEventListener("resize", function () {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  let guideAcc = 0;
  let fpsSmoothed = 60;

  function tick() {
    try {
    const raw = Math.min(0.033, clock.getDelta());
    const dt = raw * (sim.running ? sim.scale : 1);
    fpsSmoothed = fpsSmoothed * 0.9 + (1 / Math.max(raw, 0.001)) * 0.1;

    if (WEV.drive === "auto" && sim.running) {
      const pts = wievac.guide;
      let tx = player.x;
      let tz = player.z;
      if (pts && pts.length) {
        let bestI = 0;
        let best = 1e9;
        for (let k = 0; k < pts.length; k++) {
          const d = Math.hypot(pts[k].x - player.x, pts[k].z - player.z);
          if (d < best) { best = d; bestI = k; }
        }
        const ahead = pts[Math.min(pts.length - 1, bestI + 2)];
        tx = ahead.x;
        tz = ahead.z;
      }
      player.driveToward(tx, tz, raw, physics, crowd);
    } else if (WEV.view.follow) player.update(raw, physics, crowd);
    const stepped = Math.hypot(player.x - sim.px, player.z - sim.pz);
    if (stepped < 2) sim.dist += stepped;
    sim.px = player.x;
    sim.pz = player.z;
    if (sim.running) sim.time += raw;

    const loads = crowd.loads || { SE: 0, SW: 0, NE: 0, NW: 0 };
    wievac.loads = loads;
    if (sim.running && !sim.saidJam && player.floor > 0) {
      const heading = wievac.nearestOpen(player.floor, player.x, player.z);
      if (heading && sim.time > 6 && (loads[heading.id] || 0) >= 34) {
        const empty = wievac.emptiest(loads);
        wievac.closeStair(heading.id);
        sim.saidJam = true;
        const dest = empty && empty.id !== heading.id ? empty.name : "cầu thang còn trống";
        setPhase("RF-CSI: " + heading.name + " quá tải", "reroute");
        status.textContent = "Đám đông kẹt " + heading.name + ". Biển chỉ sang " + dest + ".";
        say(heading.name + " đang quá tải. WiEvac điều phối sang " + dest + ".", true);
      }
    }

    crowd.update(dt, physics, function (agent) {
      if (!sim.running) return null;
      return wievac.stepFor(agent);
    }, sim.running);

    guideAcc += raw;
    if (guideAcc > 0.35) {
      guideAcc = 0;
      wievac.guideFrom(player.floor, player.x, player.z);
    }
    wievac.drawTrail(player.floor, building.offset.bind(building));
    sim.ledT = (sim.ledT || 0) + raw;
    wievac.updateLeds(sim.ledT);
    vfx.update(dt, player, sim.running);

    const asm = building.assembly;
    if (player.floor === 0 && Math.hypot(player.x - asm.x, player.z - asm.z) < asm.r && !sim.saidExit) {
      sim.saidExit = true;
      setPhase("Đã tới điểm tập kết an toàn", "success");
      status.textContent = "Bạn đã ra khỏi tòa nhà và vào vùng tập kết.";
      say("Bạn đã đến điểm tập kết an toàn. Giữ nguyên vị trí và điểm danh.");
    }

    you.position.set(player.x, building.offset(player.floor) + player.y, player.z);
    you.rotation.y = player.yaw + Math.PI;
    const walker = you.userData.walker;
    const limbs = you.userData.limbs;
    if (walker) {
      walker.setMove(player.speed > 2.4 ? "run" : player.speed > 0.25 ? "walk" : "idle");
      walker.mixer.update(raw);
    } else if (limbs) {
      limbs.phase += raw * player.speed * 2.4;
      const swing = Math.sin(limbs.phase) * Math.min(0.65, player.speed * 0.22);
      limbs.legL.rotation.x = swing;
      limbs.legR.rotation.x = -swing;
      limbs.armL.rotation.x = -swing * 0.85;
      limbs.armR.rotation.x = swing * 0.85;
      limbs.shoeL.rotation.x = swing;
      limbs.shoeR.rotation.x = -swing;
    }
    you.visible = !(WEV.view.follow && player.fps) && (building.layoutMode !== "single" || building.focus === player.floor);

    if (WEV.view.follow) player.applyCamera(building.offset.bind(building), physics);
    else frameGod();

    const c2 = crowd.counts();
    $("dist").textContent = Math.round(sim.dist) + " m";
    $("pop").textContent = String(c2.inside);
    $("out").textContent = String(c2.out);
    const sec = Math.floor(sim.time);
    $("clock").textContent = String(Math.floor(sec / 60)).padStart(2, "0") + ":" + String(sec % 60).padStart(2, "0");

    renderer.render(scene, camera);
    window.__frames = (window.__frames || 0) + 1;
    requestAnimationFrame(tick);
    } catch (err) {
      window.__bootErr = String(err && err.stack || err);
    }
  }

  setPhase("Giai đoạn: Sẵn sàng", "");
  say("Hệ thống WiEvac sẵn sàng. Năm tầng đã tách lớp. Bấm bắt đầu để sơ tán từ Lab CSI.");
  window.__W = { building: building, player: player, physics: physics, wievac: wievac, crowd: crowd };
  const leg1 = wievac.guideFrom(player.floor, player.x, player.z);
  wievac.setJam(true);
  const leg2 = wievac.guideFrom(2, building.jamZone.minX + 2, building.jamZone.minZ + 2);
  wievac.setJam(false);
  wievac.guideFrom(player.floor, player.x, player.z);
  const designed = Math.round(leg1.len + leg2.len);
  $("total-dist").textContent = "/ " + designed + " m lộ trình";
  window.__route = { leg1: Math.round(leg1.len), leg2: Math.round(leg2.len), designed: designed, rooms: building.roomList.length, spawn: building.spawn };
  console.log("WiEvac", window.__route);
  requestAnimationFrame(tick);
  if (THREE.GLTFLoader) {
    new THREE.GLTFLoader().load("assets/worker.glb", function (gltf) {
      WEV.preparePerson(gltf);
      const walker = WEV.makeWalker();
      you.clear();
      you.add(walker.root);
      you.userData.walker = walker;
      you.userData.limbs = null;
      crowd.attachWalkers();
      window.__boot = "person-ok";
    }, undefined, function (err) {
      window.__bootErr = String(err && err.message || err);
      console.error(err);
    });
  }
})();
