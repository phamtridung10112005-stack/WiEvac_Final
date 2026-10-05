/* Nhân vật CC0: Quaternius "Worker" (Tomás Laulhé), https://poly.pizza/m/Yg2bQZO6Hj
   Bộ xương clone theo cách SkeletonUtils của three.js r160. */
(function () {
  "use strict";
  const WEV = (window.WEV = window.WEV || {});
  const THREE = window.THREE;

  function cloneSkinned(source) {
    const sourceLookup = new Map();
    const cloneLookup = new Map();
    const copy = source.clone();
    function parallel(a, b) {
      sourceLookup.set(b, a);
      cloneLookup.set(a, b);
      for (let i = 0; i < a.children.length; i++) parallel(a.children[i], b.children[i]);
    }
    parallel(source, copy);
    copy.traverse(function (node) {
      if (!node.isSkinnedMesh) return;
      const sourceMesh = sourceLookup.get(node);
      const bones = sourceMesh.skeleton.bones;
      node.skeleton = sourceMesh.skeleton.clone();
      node.bindMatrix.copy(sourceMesh.bindMatrix);
      node.skeleton.bones = bones.map(function (bone) { return cloneLookup.get(bone); });
      node.bind(node.skeleton, node.bindMatrix);
      node.frustumCulled = false;
      node.castShadow = false;
    });
    return copy;
  }

  function clipNamed(gltf, suffix) {
    const list = gltf.animations || [];
    for (let i = 0; i < list.length; i++) {
      if (list[i].name.endsWith(suffix)) return list[i];
    }
    return null;
  }

  WEV.preparePerson = function (gltf) {
    gltf.scene.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(gltf.scene);
    const height = Math.max(0.01, box.max.y - box.min.y);
    WEV.person = { gltf: gltf, scale: 1.7 / height, foot: box.min.y };
  };

  WEV.makeWalker = function () {
    const info = WEV.person;
    const gltf = info.gltf;
    const model = cloneSkinned(gltf.scene);
    model.scale.setScalar(info.scale);
    model.position.y = -info.foot * info.scale;
    const root = new THREE.Group();
    root.add(model);
    const mixer = new THREE.AnimationMixer(model);
    const idleClip = clipNamed(gltf, "|Idle_Neutral") || clipNamed(gltf, "|Idle") || gltf.animations[0];
    const walkClip = clipNamed(gltf, "|Walk") || idleClip;
    const runClip = clipNamed(gltf, "|Run") || walkClip;
    const idle = mixer.clipAction(idleClip);
    const walk = mixer.clipAction(walkClip);
    const run = mixer.clipAction(runClip);
    idle.play();
    const walker = {
      root: root,
      mixer: mixer,
      mode: "idle",
      actions: { idle: idle, walk: walk, run: run },
      setMove: function (mode) {
        if (!this.actions[mode] || this.mode === mode) return;
        this.actions[this.mode].fadeOut(0.12);
        this.actions[mode].reset().fadeIn(0.12).play();
        this.mode = mode;
      }
    };
    return walker;
  };
})();
