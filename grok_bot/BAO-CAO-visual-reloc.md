# Báo cáo visual relocalization đa phiên — WiEvac

**Thiết bị mục tiêu:** Samsung Galaxy S20 FE (ARCore-supported; nhiều SKU Snapdragon 865 / một số Exynos 990) · Android 13 · ARCore 1.56  
**Mục tiêu sản phẩm:** Sau khi mở lại app, camera nhìn cảnh đã khảo sát → trong **~1–2 s** khôi phục tầng / hành lang–phòng / `(x,y)` / heading trên **bản đồ CŨ** → theo dõi bằng **ARCore VIO phiên MỚI** → thỉnh thoảng re-localize để sửa drift. Túi quần / tối = **không có vị trí** (OK).  
**Ngày tổng hợp:** 2026-10-01 (Asia/Saigon)  
**Nguồn duy nhất:** `01-papers-arcore.md`, `02-repos.md`, `03-mvp-pipeline.md` (không thêm paper/repo ngoài ba file này).

---

## Quy tắc trung thực (Honesty rules)

| Quy tắc | Áp dụng |
|---------|---------|
| Tách **claim paper** khỏi **code sẵn dùng trên Android** | Có code ≠ chạy real-time trên S20 FE |
| Mọi số chưa đo trên S20 FE / chưa đọc PDF gốc | Gắn **chưa xác minh** |
| Không bịa paper, URL, stars, license | Chỉ giữ URL đã có trong 3 nguồn |
| ARCore **không** lưu/load feature map phiên trên phone | Issue [#94](https://github.com/google-ar/arcore-android-sdk/issues/94); Jetpack XR `Anchor.persist` = headset/glasses, **không** phải path S20 FE |
| **Không** đề xuất QR / ArUco / AprilTag / poster Augmented Images làm giải pháp **hạng 1** | Chỉ ghi nhận ngắn như phương án **đã loại** cho dự án này (ràng buộc user) |
| **Không** dùng Wi‑Fi RSSI làm vị trí chính | Chỉ ghi chú papers đếm số AP; user đã fail với 1 dual-band router |
| Không barometer / UWB làm primary | Theo ràng buộc thiết bị |

---

## 1. Hiện trạng (VPR, pose, SE2 align, Augmented Images, Cloud/Geospatial, multi-session SLAM)

### 1.1 Image retrieval / VPR

| Phương pháp | Vai trò | Indoor / mobile | Code | TFLite/ONNX SD865 |
|-------------|--------|-----------------|------|-------------------|
| **ORB + DBoW2** | BoW binary; loop/reloc ORB-SLAM | Place ID (keyframe/room), không phải cm-pose | https://github.com/dorian3d/DBoW2 | Không cần NN; **ABANDONED** (push 2021-11) theo `02-repos` |
| **FBoW** | BoW nhanh hơn DBoW2 | Tương tự | https://github.com/rmsalinas/fbow | **No license** + abandoned → skip OSS |
| **DBoW3** | Tiến hóa DBoW2 | NDK | https://github.com/rmsalinas/DBow3 | BSD-like custom; usefulness 2–3 |
| **NetVLAD** | Global descriptor baseline | HF-Net / ReLoc-PDR / hloc | https://github.com/uzh-rpg/netvlad_tf_open | Không TFLite chính thức; repo **ABANDONED** |
| **CosPlace** | Descriptor gọn (ResNet-18 512) | Outdoor SF-XL; export DIY | https://github.com/gmberton/CosPlace | **Không** TFLite/ONNX chính thức; latency SD865 **chưa xác minh** |
| **MixVPR** | R@1 cao outdoor | Outdoor | https://github.com/amaralibey/MixVPR | **No license** → reject; nặng phone |
| **EigenPlaces** | Viewpoint-robust | Có checkpoint indoor-oriented trong eval | https://github.com/gmberton/EigenPlaces | Không TFLite chính thức |
| **AnyLoc** | DINOv2 + VLAD OOD/indoor | Paper có indoor | https://github.com/AnyLoc/AnyLoc | Quá nặng 1–2 s on-device; **ABANDONED** |
| **MegaLoc** | VPR mới từ tác giả CosPlace | Research | https://github.com/gmberton/MegaLoc | Không drop-in Android |
| **MobileNet** backbone | Phân loại / encoder | Qualcomm AI Hub TFLite/ONNX | https://huggingface.co/qualcomm/MobileNet-v3-Large | Runtime mobile **có**; **không** phải head VPR sẵn |
| **HF-Net** | Global NetVLAD-like + local SuperPoint-like; thiết kế mobile | Outdoor benches; indoor transfer **chưa xác minh** | https://github.com/ethz-asl/hfnet | TF SavedModel; ONNX cộng đồng; ms phone **chưa xác minh** |

**Xếp thực tế cho S20 FE (từ papers):** (1) ORB + FBoW/DBoW2/brute-force DB — rủi ro thấp nhất; (2) HF-Net / MobileNetVLAD — cần convert NDK/TFLite; (3) CosPlace R18 nếu export ONNX/LiteRT; (4) MixVPR / EigenPlaces R50 / AnyLoc — research, không turnkey 1–2 s.

### 1.2 Pose sau khi retrieve

| Phương pháp | Cần gì | Lỗi indoor (claim) | Ghi chú |
|-------------|--------|--------------------|---------|
| **Nearest keyframe** | DB ảnh có pose | Room / ~1 m nếu DB dày; Yang IBVW APE **0.39 m** vs ORB **1.05 m** (33×3 m) | Khớp mục tiêu 1–2 s room/corridor |
| Homography / essential | Match 2D–2D | Heading + vị trí thô; scale ambiguous | Yếu cho indoor 3D |
| **PnP + 3D map** | 2D–3D (SfM/SLAM) | InLoc: **38.9% @0.25 m/10°**, **56.5% @0.5 m**, **69.9% @1.0 m** | Nặng phone; MobileARLoc: HLoc multi-second / GB |
| APR (PoseNet-style) | CNN theo scene | MobileARLoc indoor median ~**0.15–1.2 m** sau VIO | Train per-building; iPhone ~80 ms ONNX; Android **chưa xác minh** |

### 1.3 Align NEW ARCore → OLD map

| Transform | Khi nào | Tham chiếu |
|-----------|---------|------------|
| **SE(2)** `(x,y,θ)` | Bản đồ 2D floor-plan; gravity đã align | Tự nhiên cho topology WiEvac |
| **Sim(2)** | Thêm scale giữa phiên | Umeyama; VI-SLAM2tag |
| **SE(3) / Sim(3)** | Multi-floor 3D / monocular scale-free | ORB-SLAM3 Atlas |

**Công thức (khái niệm):**  
1) Reloc cho \(T_{\text{old}\leftarrow\text{cam}}\).  
2) ARCore hiện tại \(T_{\text{new}\leftarrow\text{cam}}\).  
3) \(T_{\text{old}\leftarrow\text{new}} = T_{\text{old}\leftarrow\text{cam}}\, T_{\text{new}\leftarrow\text{cam}}^{-1}\) (hoặc hạn chế SE2).  
4) Mọi pose ARCore sau đó map qua \(T_{\text{old}\leftarrow\text{new}}\).  
5) Mỗi lần re-recognize thành công → **refresh** transform (drift + ACS jump).

**Bằng chứng liên quan:** MobileARLoc (arXiv:2401.11511) rigid APR↔VIO, indoor mean sau fusion ~**0.73 m / ~5°** (*iPhone/ARKit*, không ARCore). VI-SLAM2tag (arXiv:2207.02668): partition trajectory + local Sim → ~**0.5 m** labeling; ACS updates phá single global transform.

### 1.4 Augmented Images (official)

| Fact | Nguồn |
|------|-------|
| Chỉ **ảnh 2D phẳng** (poster/packaging), không cảnh 3D phòng/hành lang | https://developers.google.com/ar/develop/augmented-images |
| ≥ **25%** khung hình lúc detect; `arcoreimg` score ≥ **75** khuyến nghị | Android AI guide |
| ≤ **1000** ảnh/DB, ≤ **20** track đồng thời; ~**6 KB**/entry; **on-device**, không net | Official |
| Pose = tâm image plane | `AugmentedImage` API |

**Verdict WiEvac:** **Không phù hợp** làm reloc 3D chính từ ảnh khảo sát phòng. Chỉ khả thi nếu dán poster phẳng — **xung đột ràng buộc no-QR/markers** → **loại khỏi Top 5**.

### 1.5 Cloud Anchors / Geospatial / map persistence

| Câu hỏi | Trả lời | Nguồn |
|---------|---------|-------|
| ARCore Session save/load SLAM map trên phone? | **Không** public API | [#94](https://github.com/google-ar/arcore-android-sdk/issues/94); Anchors docs |
| Jetpack XR `Anchor.persist`? | LOCAL cho **XR headset/glasses**, không path S20 FE phone | https://developer.android.com/develop/xr/jetpack-xr-sdk/arcore/anchors |
| Cloud Anchors | Upload visual → Google; resolve cần **internet**; visual discard ≤**24h**; room-scale | https://developers.google.com/ar/develop/cloud-anchors |
| Geospatial / VPS | GPS + Street View VPS; ~**5 m / 5°** khi có VPS; indoor private home **không** fit offline | https://developers.google.com/ar/develop/geospatial |
| Local anchors | Chỉ valid trong **một instance** app | https://developers.google.com/ar/develop/anchors |

### 1.6 Multi-session SLAM trên Android

| Hệ thống | Save + reloc đa phiên? | Android phone? | Ghi chú MVP |
|----------|------------------------|----------------|-------------|
| **RTAB-Map** | **Có** (`.db`, localization, merge) | Có path Android/ARCore trong source; Play fetch 404 **chưa xác minh** | BSD-3; build NDK multi-tuần → **skip 1 tháng** |
| **ORB-SLAM3** | Atlas `SaveAtlas` / `LoadAtlas` | Port cộng đồng; official = desktop | GPL-3 + abandoned cutoff; monocular fragile → **skip** |
| **OpenVINS** | Chủ yếu VIO, không Atlas map | NDK mobile tồn tại | GPL-3; không “survey → cold start reloc” → **skip** |

**Kết luận sản phẩm Kotlin+ARCore:** **keyframe DB app-owned + retrieval + SE2** đơn giản hơn embed full SLAM.

### 1.7 Wi‑Fi fingerprint — chỉ ghi chú số AP (không primary)

| Nguồn | Claim số AP | Link |
|-------|-------------|------|
| Zone FP factory (J. Intell. Manuf. 2025) | **4–6 APs** ≥80% zone | 10.1007/s10845-025-02660-y |
| UTM KNN | **4 APs** ~0.83 m MSE; **6** ~0.78 m | http://article.nadiapub.com/IJFGCN/vol10_no9/3.pdf |
| DIVA thesis | **5–10 APs** thường đủ ~1–2 m | https://www.diva-portal.org/smash/get/diva2:1877127/FULLTEXT01.pdf |
| MDPI Entropy WLAN FP | Gain ~**3–5 APs**, diminishing sau đó | MDPI Entropy 17(12):7859 |

→ Meter-class FP cần **nhiều AP không gian đa dạng**, không phải 1 dual-band. **Không đề xuất lại Wi‑Fi làm reloc chính.**

---

## 2. Repo OSS — bảng license / stars / commit / Android / closed deps / score; abandoned

**Cutoff abandoned:** last push trước 2024-10-01 (`02-repos`). Stars/push: GitHub API 2026-10-01.

### 2.1 Accepted (active + license cho phép)

| # | Repo | License | Stars | Last push | Android? | Closed deps | Score | Fit |
|---|------|---------|------:|-----------|----------|-------------|------:|-----|
| 1 | [opencv/opencv](https://github.com/opencv/opencv) | Apache-2.0 | ~91025 | 2026-10-01 | **Yes** Maven `org.opencv:opencv:4.14.0` | Không | **5** | ORB, BF/FLANN, `findHomography`, `solvePnP` — **core path** |
| 2 | [lessthanoptimal/BoofCV](https://github.com/lessthanoptimal/BoofCV) | Apache-2.0 | ~1199 | 2026-09-05 | **Yes** `boofcv-android:1.4.0` | Không | **4** | Pure Java twin; camera helpers |
| 3 | [google-ar/arcore-android-sdk](https://github.com/google-ar/arcore-android-sdk) | Samples Apache-2.0; **AAR proprietary** | ~5242 | 2026-09-04 | **Yes** | **Yes** `com.google.ar:core` | **4** | VIO tracking + (AI samples — AI **không** Top 5) |
| 4 | [google-ai-edge/LiteRT](https://github.com/google-ai-edge/LiteRT) | Apache-2.0 | ~3461 | 2026-10-01 | **Yes** | Không runtime | **3–4** | Runtime VPR nếu **có** model; không ship model VPR |
| 5 | [cvg/Hierarchical-Localization](https://github.com/cvg/Hierarchical-Localization) | Apache-2.0 | ~4226 | 2025-12-10 | **No** (Python) | Optional heavy nets | **3** | Prototype desktop retrieve→match→pose |
| 6 | [colmap/colmap](https://github.com/colmap/colmap) | BSD-3-Clause | ~12843 | 2026-10-01 | **No** | Optional CUDA | **3** | Offline sparse map / PnP feed |
| 7 | [gmberton/CosPlace](https://github.com/gmberton/CosPlace) | MIT | ~402 | 2026-03-15 | No (export DIY) | Weights ngoài | **3** | Global desc; không TFLite sẵn |
| 8 | [gmberton/MegaLoc](https://github.com/gmberton/MegaLoc) | MIT | ~303 | 2026-08-06 | No | Weights | **3** | VPR SOTA author; không APK |
| 9 | [sceneview/sceneview](https://github.com/sceneview/sceneview) | Apache-2.0 | ~1329 | 2026-10-01 | **Yes** Kotlin | ARCore khi dùng AR | **3** | UI host, không engine reloc |
| 10 | [opencv/opencv_contrib](https://github.com/opencv/opencv_contrib) | Apache-2.0 | ~10209 | 2026-09-30 | Custom build | Không | **2** | Không cần ORB MVP; NDK weeks |
| 11 | [gmberton/EigenPlaces](https://github.com/gmberton/EigenPlaces) | MIT | ~162 | 2026-03-15 | No | Weights | **2** | Research VPR |
| 12 | [gmberton/deep-visual-geo-localization-benchmark](https://github.com/gmberton/deep-visual-geo-localization-benchmark) | MIT | ~260 | 2026-03-15 | No | Datasets | **2** | Benchmark / NetVLAD giáo dục |
| — | [rmsalinas/DBow3](https://github.com/rmsalinas/DBow3) | BSD-like + notify | ~575 | 2026-05-18 | NDK only | OpenCV | **2–3** | BoW; overkill vs BF cho DB nhỏ |

### 2.2 Rejected / abandoned / skip

| Repo | Lý do |
|------|-------|
| [dorian3d/DBoW2](https://github.com/dorian3d/DBoW2) | Push **2021-11-24** → **ABANDONED** |
| [rmsalinas/fbow](https://github.com/rmsalinas/fbow) | **No license** + push 2021-11 → skip |
| [amaralibey/MixVPR](https://github.com/amaralibey/MixVPR) | **No license** + push 2024-06 → skip |
| [AnyLoc/AnyLoc](https://github.com/AnyLoc/AnyLoc) | Push **2024-03-13** → **ABANDONED** |
| [uzh-rpg/netvlad_tf_open](https://github.com/uzh-rpg/netvlad_tf_open) | Push **2021-01-07** → **ABANDONED** |
| [UZ-SLAMLab/ORB_SLAM3](https://github.com/UZ-SLAMLab/ORB_SLAM3) | **ABANDONED** (push 2024-07) + **GPL-3.0** |
| ORB-SLAM Android ports (FangGet, Abonaventure, …) | Pre-2022; GPL; fragile |
| [serizba/salad](https://github.com/serizba/salad) | Active nhưng **GPL-3**; Python research |
| [4ku/Place-recognition-evaluation](https://github.com/4ku/Place-recognition-evaluation) | No license |
| InBrewJ/SiloamSee | Abandoned 2017; no license |
| [rpng/open_vins](https://github.com/rpng/open_vins) | GPL-3; không fit “survey → recognize later” |
| [introlab/rtabmap](https://github.com/introlab/rtabmap) | BSD-3 OK, active (~4019★) — **skip vì độ phức tạp NDK**, không vì license |

### 2.3 Shortlist OSS cho S20 FE ~1 tháng (đã lọc no-marker)

| # | Stack | Vì sao |
|---|-------|--------|
| 1 | OpenCV Android 4.14.0 arm64 | Core classical reloc |
| 2 | BoofCV Android 1.4.0 | Alt 100% Java |
| 3 | LiteRT | Chỉ khi có model VPR nhỏ (stretch) |
| 4 | hloc + COLMAP (laptop) | Verify offline, không ship phone |
| 5 | ARCore SDK (VIO + optional Cloud Anchors) | Tracking + online optional |

---

## 3. Thư viện Android nhẹ (OpenCV, BoofCV, TFLite/LiteRT) + cái KHÔNG nên

### 3.1 Nên dùng

| Thư viện | Version / packaging | License | Size / tip | APIs MVP | Verdict |
|----------|---------------------|---------|------------|----------|---------|
| **OpenCV Android** | `org.opencv:opencv:4.14.0` (ổn định hơn 5.0 cho student) | Apache-2.0 | Fat AAR ~**123 MB** all-ABI; ship `abiFilters "arm64-v8a"` | `ORB.create()`, `DescriptorMatcher` BF/FLANN, `Calib3d.findHomography`, `solvePnP` | **Primary** |
| **BoofCV Android** | `org.boofcv:boofcv-android:1.4.0` (2026-05-25) | Apache-2.0 | Module AAR ~108 KB + JARs; exclude xmlpull/commons-compress theo docs | Detect/describe/associate, geometry, PnP, camera helpers | **Alt Java** |
| **LiteRT** (TFLite successor) | https://github.com/google-ai-edge/LiteRT | Apache-2.0 | Runtime OSS | Chạy embedding đã convert | **Stretch** — runtime only |
| **ARCore** (motion tracking) | `com.google.ar:core` + Play Services AR | Proprietary binary; samples Apache | Đã có nếu app AR | `TrackingState`, camera pose SE2 | **Bắt buộc** cho track mượt sau cold-start |

### 3.2 TFLite / VPR — thực tế 2026-10-01

| Model | Size | Inference SD865 | Notes |
|-------|------|-----------------|-------|
| **Không có** NetVLAD / CosPlace / MixVPR / AnyLoc TFLite drop-in chính thức | — | — | Phải tự export |
| NetVLAD VGG-16 | ~530 MB float; prune+8-bit lit. ~30–65 MB | **Không** số SD865 đáng tin | Repo TF abandoned |
| CosPlace R18/512 | Hàng chục MB (backbone); không TFLite official | **Chưa báo cáo** SD865 | DIY ONNX→LiteRT |
| MobileNetV2 TFLite (proxy, **không** VPR) | ~3–14 MB quant | ~**10–40 ms** class | Chỉ bound latency |

→ Tháng 1: **không đặt cược TFLite VPR**. ORB + DB hàng trăm keyframes đủ corridor/home.

### 3.3 Cái KHÔNG nên (1–2 tháng student / sản phẩm Kotlin)

| Hệ thống | Vì sao không |
|----------|--------------|
| **ORB-SLAM3** + ports Android | GPL-3 copyleft; abandoned; NDK hell; map save/load phone kém ổn |
| **OpenVINS** | GPL-3; VIO liên tục, không multi-session place DB |
| **RTAB-Map** full port | Mạnh multi-session nhưng NDK/RGB-D ops-heavy; overkill vs 2D floorplan |
| **Augmented Images posters / ArUco / AprilTag** làm primary | User **cấm** QR/markers làm đề xuất chính |
| **Geospatial/VPS** primary indoor home | Không Street View trong nhà riêng |
| **Wi‑Fi RSSI** primary | Ràng buộc + 1 AP đã fail |
| **opencv_contrib** custom NDK | Weeks; không cần ORB MVP |
| Embed **hloc** trên phone | Python/desktop; multi-second / GB |

---

## 4. Papers 2019–2026 (link, claim error, code?, Wi‑Fi/marker flag)

| ID | Paper | Year | Link | Claim error (tóm tắt) | Code? | Wi‑Fi / marker |
|----|-------|------|------|------------------------|-------|----------------|
| P1 | **MobileARLoc** | 2024 | https://arxiv.org/abs/2401.11511 | Outdoor MS-T cải thiện ~50% / ~66°; indoor MS-T+vio ~**0.73 m / 4.99°**; ~80 ms iPhone ONNX | MS-T train: https://github.com/yolish/multi-scene-pose-transformer ; app full **chưa xác minh** OSS | Không Wi‑Fi/marker. **ARKit**, không ARCore. Train per-scene |
| P2 | **ReLoc-PDR** | 2023 | https://arxiv.org/abs/2309.01646 | Corridor textureless RMSE **0.21 m** (PDR 5.6 m); outdoor ~**0.56 m** | Không repo “ReLoc-PDR” đơn; dùng hloc/COLMAP/GTSAM | AprilTag **chỉ scale COLMAP** lúc build; online cam+IMU. Reloc thường **server** — on-device **chưa xác minh** |
| P3 | **VI-SLAM2tag** | 2022 | https://arxiv.org/abs/2207.02668 · code https://github.com/laskama · Zenodo 10.5281/zenodo.6801310 | Labeling ~**0.5 m**; WLAN trên label ~**2 m** | **Có** | **Dùng Augmented Image landmarks** — xung đột no-marker production; học ACS jump / align |
| P4 | **Feigl et al.** ARCore/ARKit/HoloLens limits | 2020 | https://www.scitepress.org/Papers/2020/89899/89899.pdf | Reloc mỗi ~60–100 m: ~**6.65 cm/m**; không reloc: tới ~**14.4 cm/m**, ~**17 m** MAE / 120 m (**verify PDF**) | Không | Không Wi‑Fi. Động lực re-localize đa phiên |
| P5 | **ISPRS 2019** smartphone + AR indoor mapping | 2019 | https://isprs-archives.copernicus.org/articles/XLII-2-W17/135/2019/isprs-archives-XLII-2-W17-135-2019.pdf | Local cm-class; traj ~**1%** distance; loop reloc (**PDF timeout — chưa xác minh**) | App-specific | Không infrastructure |
| P6 | **HF-Net** | 2019 | https://arxiv.org/abs/1812.03506 · https://github.com/ethz-asl/hfnet | Outdoor recall@thresholds; thiết kế mobile (~15 ms feat GTX1080 paper) | **Có** TF | Không Wi‑Fi/marker. Indoor transfer **chưa xác minh** |
| P7 | **Yang et al.** IBVW smartphone | 2023 | https://pmc.ncbi.nlm.nih.gov/articles/PMC9964296/ | APE **0.39 m** (IBVW); ORB **1.05 m**; match ~0.8–1 s; corridor 33×3 m | Data on request | Không Wi‑Fi. Mag+accel hướng ảnh — compass indoor **mong manh** |
| P8 | **InLoc** | 2018† | https://arxiv.org/abs/1803.10368 · hloc https://github.com/cvg/Hierarchical-Localization | **38.9% / 56.5% / 69.9%** @ 0.25/0.5/1.0 m +10° | Via hloc | Cần dense 3D — nặng phone |
| P9 | **ORB-SLAM3** | 2020–21 | https://arxiv.org/abs/2007.11898 · https://github.com/UZ-SLAMLab/ORB_SLAM3 | EuRoC desktop | **Có**; Android = community | Không Wi‑Fi. Production phone **partial** |
| P10 | **Map-free reloc** (Niantic) | 2022+ | https://arxiv.org/abs/2210.05494 · https://github.com/nianticlabs/map-free-reloc | Dataset-specific | **Có** | Không Wi‑Fi. S20 latency **chưa xác minh** |
| P11 | CosPlace / MixVPR / EigenPlaces / AnyLoc | 2022–23 | CosPlace https://arxiv.org/abs/2204.02287 · MixVPR https://arxiv.org/abs/2303.02190 · AnyLoc https://arxiv.org/abs/2308.00688 · EigenPlaces GitHub | Retrieval outdoor R@1 cao (vd MixVPR Pitts 94.6%; EigenPlaces Pitts30k ~91.9%) | CosPlace/EigenPlaces/AnyLoc **có**; MixVPR **no license** | Chỉ stage retrieval; mobile xem §1.1 |

† InLoc hơi trước 2019 nhưng vẫn baseline indoor structure trong nguồn.

---

## 5. Pipeline MVP tối thiểu

### 5.1 Survey — lưu gì

**Mục tiêu:** lớp landmark thưa trên topology `(nodes, edges, floors, rooms)`, không mesh dày.

| Khi capture | Lý do |
|-------------|-------|
| **Mọi topology node** (cửa, giao, cầu thang, tâm phòng routing) | Cold-start đúng chỗ quyết định nav |
| **Thêm ~2–3 m** dọc hành lang dài | Tránh tường trắng; spacing = design (**chưa xác minh** tối ưu) |
| Optional 2 heading / node | Recall khi đi ngược |

Bỏ qua khi `TrackingState != TRACKING`.

**Schema sketch:**

```text
VisualLandmark {
  id, nodeId?, edgeId?,
  x, y, floor, roomId?, headingDeg,
  imagePath, imageW, imageH,
  featureBlobPath?,          // ORB descriptors
  // augmentedImageName / cloudAnchorId — optional stretch, không primary
  qualityScore?, capturedAtMs
}
```

| Tham số | MVP | Ghi chú |
|---------|-----|---------|
| Resolution | **640×480** hoặc downscale ~720p | AI docs: ≥300×300; res cao không giúp AI |
| Features ORB | ~500–1000 kp, 32-byte binary | Cost S20 FE **chưa xác minh** |
| Pose convention | **SE2** `(x,y,θ)` / floor | Pitch/roll bỏ cho bản đồ 2D |
| Pose survey | ARCore cam → floor SE2 + snap node topology | Absolute `(x,y)` từ editor / PDR WiEvac sẵn |

### 5.2 Match → SE2 → track → re-localize

**States:**

```text
NO_POSITION → LOCALIZING → TRACKING_MAP ⇄ LOCALIZING (định kỳ / rủi ro)
                 ↑              │
                 └──── lost ────┘ → NO_POSITION
```

**Contract:**

```kotlin
data class RelocResult(
  val landmarkId: String,
  val mapSe2: Se2,       // (x, y, headingRad)
  val confidence: Float, // 0..1
  val method: RelocMethod
)
```

**Accept khi (MVP):**

| Check | Gate |
|-------|------|
| `TrackingState == TRACKING` | Bắt buộc |
| Match tốt | ORB: Lowe + RANSAC inliers; Manual: user tap confidence=1 |
| Hình học | Inliers ≥ **N≈12**, reproj ≤ **ε≈8 px** — **chưa xác minh** S20 FE; tune |
| Floor | landmark.floor khớp expected (không dựa barometer tuyệt đối) |

**Transform NEW → OLD (SE2):**

\[
T_{\text{map}\leftarrow\text{ar}} = T_{\text{map}\leftarrow\text{landmark}} \cdot T_{\text{ar}\leftarrow\text{landmark}}^{-1}
\]

Mỗi frame: \(p_{\text{map}} = \mathrm{SE2}(T_{\text{map}\leftarrow\text{ar}} \cdot T_{\text{ar\_cam}})\).  
Refresh \(T\) mỗi lần re-localize thành công.

| Trigger re-localize | Cadence |
|---------------------|---------|
| Cold start / sau `NO_POSITION` | Liên tục đến accept đầu |
| `TRACKING_MAP` | Mỗi **5–10 s** hoặc gần landmark (**chưa xác minh** period) |
| Cầu thang / đổi tầng | Ngay |
| `INSUFFICIENT_FEATURES` | Tăng tần suất / force LOCALIZING |
| Ra khỏi túi | Coi như cold start |

**Động lực:** Feigl / VI-SLAM2tag — open-loop ARCore có thể tới ~**17 m / 120 m** nếu không reloc (không phải bench S20 FE).

### 5.3 Pocket → `NO_POSITION`

| Vào `NO_POSITION` khi | Hành vi |
|-----------------------|---------|
| `PAUSED` / `STOPPED`; tối / thiếu feature; `Session.pause()` | Ẩn chấm map hoặc “?” — **không** đóng băng pose cũ như live |
| Optional luma thấp N frames (**chưa xác minh**) | Pause turn-by-turn; `position: null` |
| | Chỉ thoát bằng reloc thành công hoặc **manual check-in** |

### 5.4 Storage & latency (ước lượng 2 tầng ~80–120 landmarks)

| Store | ~80–120 landmarks |
|-------|-------------------|
| JPEG 640×480 | ~**3–12 MB** |
| ORB blob ~20–40 KB/lm | ~**2–5 MB** |
| Metadata | ≪ 1 MB |
| **Budget MVP** | **≤ ~20 MB** on-device |

| Method | Latency order | Basis |
|--------|---------------|-------|
| ORB + top-k RANSAC ≤120 KF | **~0.2–1.5 s** plausible | Monulens/ReadWrite **không** S20 FE |
| Manual check-in | 1 tap | — |
| Cloud Anchor resolve | **giây** + RTT + net | Official periodic compare |
| LiteRT VPR (nếu có model) | MobileNet proxy ~10–40 ms + match | Model VPR **chưa có** sẵn |

**UX target:** first fix **&lt; 3 s** (lý tưởng 1–2 s) khi landmark rõ trong khung.

### 5.5 Risks

| Risk | Effect | Mitigation (không dùng marker primary) |
|------|--------|----------------------------------------|
| Hành lang trắng / ít texture | ORB / Cloud fail; `INSUFFICIENT_FEATURES` | Tăng mật độ keyframe; tránh dựa tường trống; manual fallback |
| Ngày / đêm | Descriptor mismatch | Survey 2 chế độ sáng; tối → `NO_POSITION` |
| Đồ đạc dịch | Landmark scene-dependent hỏng | Re-survey; ưu tiên góc kiến trúc ổn định |
| Pocket / session reset | SE2 stale | Hard `NO_POSITION` |
| Phòng giống nhau | Sai landmark ID | Geometric gate + optional 2nd confirm |
| Cloud offline / quota | Resolve fail | Fall back ORB / manual |
| Compass indoor | Hướng sai | **Không** dùng compass làm primary heading |

### 5.6 Sơ đồ pipeline đề xuất

```text
SURVEY (một phiên ARCore):
  walk → keyframes {image, ORB desc, SE2 pose, floor, room, node}
  optional offline laptop: COLMAP/hloc để verify / PnP sau này

COLD START (phiên mới):
  camera cover → NO_POSITION
  else mỗi ~0.5–1 s:
    retrieve top-k (ORB BF hoặc LiteRT embedding nếu có)
    score < τ → no position
    else pose0 = keyframe SE2  (hoặc PnP nếu có 3D)
    T_old←new từ pose0 vs ARCore hiện tại
    report (floor, room, x, y, heading) trên map CŨ

TRACK:
  x_old = T_old←new * x_arcore
  mỗi N m / low texture / 5–10 s → re-retrieve & refresh T
```

---

## 6. Top 5 cho S20 FE trong 1 tháng

**Tiêu chí xếp hạng:** rủi ro kỹ thuật + dependency cho app Kotlin WiEvac sẵn có · ARCore 1.56 · **không** QR/markers/beacon primary · **không** Wi‑Fi RSSI primary · đạt “camera nhận lại → đặt lên map cũ 1–2 s → track ARCore mới”.

Mỗi hạng gắn nhãn: **nhận chỗ lần đầu** / **theo dõi mượt** / **cả hai**.

| Hạng | Giải pháp | Nhãn | Vì sao |
|------|-----------|------|--------|
| **1** | **OpenCV Android ORB + keyframe DB + SE(2) align NEW ARCore → old map** | **cả hai** | Offline, không sticker, Apache-2.0, AAR sẵn; retrieve→nearest KF/homography→\(T_{\text{old}\leftarrow\text{new}}\)→VIO; khớp mục tiêu 1–2 s room-level; rủi ro eng vừa phải nhưng **không** vi phạm no-QR |
| **2** | **Manual check-in tại node đã biết → ARCore VIO** | **theo dõi mượt** (cold-start = **user**) | Ship ngay tuần 1; ít API; sau check-in track mượt; không giải cold-start tự động |
| **3** | **BoofCV Android** (ORB/feature + associate + PnP) thay OpenCV | **cả hai** (cùng kiến trúc DB) | Pure Java, Apache-2.0, camera helpers; alt nếu tránh NDK/OpenCV AAR béo; cùng pipeline SE2 |
| **4** | **LiteRT + VPR nhỏ** (CosPlace/MegaLoc/MobileNetVLAD export) **chỉ nếu** có model | **nhận chỗ lần đầu** (stretch; sau đó vẫn cần SE2+ARCore) | Runtime Apache sẵn; **không** có CosPlace/NetVLAD TFLite drop-in đã xác minh → tháng 1 chỉ prototype nếu tự export; không bet deadline |
| **5** | **Cloud Anchors** (optional online, cần Google) **hoặc** **hloc+COLMAP offline** để verify accuracy | Cloud: **cả hai** khi có net; hloc: **nhận chỗ** trên laptop (không ship phone) | Cloud = persistence official nhưng internet+GCP+room-scale+TTL; hloc = gold-standard kiểm chứng pipeline ý tưởng, không APK 1 tháng |

### One-liners

1. **ORB keyframe DB + SE2 + ARCore VIO** — cả hai (primary đề xuất).  
2. **Manual check-in + ARCore VIO** — theo dõi mượt sau; cold-start do user.  
3. **BoofCV Android** — cả hai (alt classical CV).  
4. **LiteRT VPR** — nhận chỗ (stretch; chưa có CosPlace TFLite sẵn).  
5. **Cloud Anchors (online) / hloc offline verify** — Cloud: cả hai nếu có Google; hloc: kiểm chứng desktop.

### Phương án đã loại khỏi Top 5 (ràng buộc user)

| Loại | Lý do ngắn |
|------|------------|
| **ArUco / AprilTag / QR / beacons** | User cấm làm đề xuất primary; deterministic nhưng cần hạ tầng dán |
| **Augmented Images posters** | Official on-device tốt kỹ thuật nhưng = marker/poster phẳng → chỉ **optional stretch** nếu policy đổi |
| **Wi‑Fi RSSI fingerprint** | Không primary; papers cần 4–10 AP |
| **Geospatial/VPS** | Không VPS trong nhà riêng offline |
| **ORB-SLAM3 / RTAB-Map / OpenVINS full** | License/complexity/NDK ngoài cửa sổ 1 tháng |

**Combo tháng 1 đề xuất:** ship **#2** ngay cho demo routing; song song implement **#1** (ORB DB) cho cold-start thật; giữ **#3** nếu OpenCV AAR gây đau; **#4/#5** chỉ sau khi #1 chạy được trên S20 FE.

---

## Phụ lục — chỉ mục URL chính

| Chủ đề | URL |
|--------|-----|
| Augmented Images | https://developers.google.com/ar/develop/augmented-images |
| Cloud Anchors | https://developers.google.com/ar/develop/cloud-anchors |
| Geospatial | https://developers.google.com/ar/develop/geospatial |
| Anchors (local vs cloud) | https://developers.google.com/ar/develop/anchors |
| Jetpack XR anchors | https://developer.android.com/develop/xr/jetpack-xr-sdk/arcore/anchors |
| ARCore map save #94 | https://github.com/google-ar/arcore-android-sdk/issues/94 |
| TrackingState | https://developers.google.com/ar/reference/java/com/google/ar/core/TrackingState |
| S20 FE supported | https://developers.google.com/ar/devices |
| OpenCV | https://github.com/opencv/opencv |
| BoofCV | https://github.com/lessthanoptimal/BoofCV |
| LiteRT | https://github.com/google-ai-edge/LiteRT |
| hloc | https://github.com/cvg/Hierarchical-Localization |
| COLMAP | https://github.com/colmap/colmap |
| HF-Net | https://github.com/ethz-asl/hfnet |
| MobileARLoc | https://arxiv.org/abs/2401.11511 |
| ReLoc-PDR | https://arxiv.org/abs/2309.01646 |
| VI-SLAM2tag | https://arxiv.org/abs/2207.02668 |
| CosPlace | https://github.com/gmberton/CosPlace |
| Qualcomm MobileNet | https://huggingface.co/qualcomm/MobileNet-v3-Large |

---

*Hết báo cáo. Tổng hợp chỉ từ `01-papers-arcore.md`, `02-repos.md`, `03-mvp-pipeline.md`.*
