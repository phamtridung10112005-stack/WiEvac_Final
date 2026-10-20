# Kịch bản thuyết trình WiEvac (10 phút, kể cả demo)

Deck: https://claude.ai/artifact/JeStEMJ2y2zoN6uug1sBYB (15 slide). Lời nói và câu nối dưới đây **trùng từng chữ** với ghi chú trong deck.

**Tổng thời gian dự kiến: 8:46**, đã tính cả 3 clip và thời gian bấm chuyển slide. Còn khoảng 1:14 dự phòng. **Không nói thêm câu nào ngoài kịch bản.**

**Quy tắc câu nối:** câu nối được nói **trong lúc bấm sang slide sau**. Nói xong câu nối thì slide mới vừa hiện, nên không mất thêm thời gian.

## Bảng thời gian

| # | Slide | Người nói | Thời gian | Mốc cộng dồn |
|---|---|---|---|---|
| 1 | WiEvac | A (bạn) | 16 s | 0:18 |
| 2 | Khi cháy, mọi người dồn về một lối | A (bạn) | 30 s | 0:50 |
| 3 | WiEvac khi tòa nhà có cháy | A (bạn) | 45 s | 1:37 |
| 4 | Hệ thống trả lời 4 câu hỏi | A (bạn) | 25 s | 2:04 |
| 5 | Linh kiện phổ thông, xử lý ngay trên chip | A (bạn) | 25 s | 2:31 |
| 6 | Quét phòng → sơ đồ 2D → mô hình 3D | A (bạn) | 30 s | 3:03 |
| 7 | Bắt node gần nhất, tự chuyển node khi di chuyển | A (bạn) | 30 s | 3:35 |
| 8 | Suy hao do người chắn → % lưu thông | A (bạn) | 35 s | 4:12 |
| 9 | Học sâu trên CSI kém khi sang môi trường mới | A (bạn) | 40 s | 4:54 |
| 10 | Chỉ học lúc trống, vẫn nhận ra người ở nơi mới | A (bạn) | 55 s | 5:51 |
| 11 | AI giảm mạnh các lỗi nguy hiểm | A (bạn) | 25 s | 6:18 |
| 12 | Người bước vào → Bị chặn, đi ra → An toàn | A (bạn) | 35 s | 6:55 |
| 13 | Đông thì đắt, D* Lite tự né trong 1,7 ms | B (bạn điều hướng) | 40 s | 7:37 |
| 14 | Nhìn trước 15 giây, chia tải, chỉ dẫn tại chỗ | B (bạn điều hướng) | 35 s | 8:14 |
| 15 | WiEvac trả lời đủ 4 câu hỏi | A (bạn) | 30 s | 8:46 |

**Mốc kiểm tra khi tập:**
- Hết slide 5: **2:31**.
- Hết slide 12, bàn giao cho B: **6:55**.
- Hết slide 15: **8:46**.

Chậm quá 20 giây ở một mốc thì áp dụng ngay mục "Nếu bị chậm".

## Lời nói từng slide

### Slide 1 · WiEvac

*A (bạn) · 16 giây*

> Kính thưa Hội đồng, nhóm WiEvac, Trường Đại học Công nghệ Kỹ thuật Hưng Yên, xin trình bày hệ thống hỗ trợ sơ tán thông minh bằng sóng Wi-Fi và AI chạy ngay trên chip.

**Câu nối sang slide 2** (nói trong lúc bấm chuyển):

> *Trước hết, vấn đề mà nhóm muốn giải quyết.*

### Slide 2 · Khi cháy, mọi người dồn về một lối

*A (bạn) · 30 giây*

**Thao tác:** Chỉ cụm chấm đỏ ở lối gần, rồi sang lối xa đang trống.

> Khi có cháy, biển báo thoát hiểm cố định luôn chỉ về lối gần nhất, nên mọi người dồn về một lối, trong khi lối xa lại bỏ trống. Camera thấy được đám đông nhưng xâm phạm riêng tư, cần ánh sáng và bị khói che. Cái cần là: không cần camera mà vẫn biết mỗi hành lang còn đi được bao nhiêu, để chỉ đường né chỗ đông.

**Câu nối sang slide 3** (nói trong lúc bấm chuyển):

> *WiEvac làm được điều đó. Mời Hội đồng xem kết quả trước.*

### Slide 3 · WiEvac khi tòa nhà có cháy

*A (bạn) · 45 giây*

**Thao tác:** Bấm phát clip (41 giây). Câu 1 lúc cảnh báo hiện; câu 2 lúc bên trái báo cửa thoát 1 đông; câu 3 khi bên phải ra ngoài. Đợi clip chạy hết rồi mới nói câu nối.

> Khi có cháy, mọi điện thoại cài ứng dụng cùng nhận cảnh báo. Bên trái, người ở tầng 2 được chỉ xuống cầu thang; cửa thoát 1 đang đông nên ứng dụng chuyển sang cửa thoát 2. Bên phải, người ở tầng 1 được chỉ ra ngoài ngay.

**Câu nối sang slide 4** (nói trong lúc bấm chuyển):

> *Vậy hệ thống làm được điều đó bằng cách nào?*

### Slide 4 · Hệ thống trả lời 4 câu hỏi

*A (bạn) · 25 giây*

**Thao tác:** Chỉ lần lượt 4 ô từ trái sang phải.

> Hệ thống trả lời 4 câu hỏi. Trên điện thoại: tòa nhà trông thế nào, và tôi đang ở đâu. Ở hạ tầng tòa nhà, cũng là trọng tâm của đề tài: chỗ nào đông, và đi đường nào.

**Câu nối sang slide 5** (nói trong lúc bấm chuyển):

> *Cả 4 phần chạy trên phần cứng rất phổ thông.*

### Slide 5 · Linh kiện phổ thông, xử lý ngay trên chip

*A (bạn) · 25 giây*

> ESP32-S3 vừa làm cặp phát thu sóng Wi-Fi, vừa phát beacon Bluetooth; firmware C lọc nhiễu, tính công thức và chạy Tiny AI ngay trên chip. Raspberry Pi 5 là trung tâm, chạy offline. Bảng LED và loa nhận lệnh qua ESP-NOW. Ứng dụng Android dùng ARCore.

**Câu nối sang slide 6** (nói trong lúc bấm chuyển):

> *Bây giờ, lần lượt từng câu hỏi.*

### Slide 6 · Quét phòng → sơ đồ 2D → mô hình 3D

*A (bạn) · 30 giây*

> Câu thứ nhất: tòa nhà trông thế nào. Người dùng ngắm camera vào 4 góc chân tường, ARCore lấy tọa độ, khép vùng là ra sơ đồ 2D và diện tích; ứng dụng tự căn thẳng, căn vuông. Thêm chiều cao trần và cửa, ta có mô hình 3D, xuất file GLB làm bản đồ sơ tán.

**Câu nối sang slide 7** (nói trong lúc bấm chuyển):

> *Có bản đồ rồi, câu thứ hai: người đang ở đâu trên bản đồ đó.*

### Slide 7 · Bắt node gần nhất, tự chuyển node khi di chuyển

*A (bạn) · 30 giây*

**Thao tác:** Bấm phát clip, nói trong lúc clip chạy.

> Mỗi node ESP32 phát beacon Bluetooth liên tục. Điện thoại chỉ nghe và đo cường độ sóng của từng node; node nào mạnh nhất thì người đang ở đó. Khi di chuyển, điện thoại tự chuyển sang node mạnh hơn. Toàn bộ chạy offline.

**Câu nối sang slide 8** (nói trong lúc bấm chuyển):

> *Biết người ở đâu rồi, nhưng chỗ nào đang đông? Đây là phần trọng tâm.*

### Slide 8 · Suy hao do người chắn → % lưu thông

*A (bạn) · 35 giây*

**Thao tác:** Nhấn giọng: “ITU-R P.526”.

> Mỗi cặp ESP32-S3 đo sóng Wi-Fi CSI trên 52 sóng mang, 100 lần mỗi giây. Người chắn đường truyền làm sóng suy hao. Công thức nhiễu xạ trong khuyến nghị ITU-R P.526 của Liên minh Viễn thông Quốc tế cho biết suy hao ứng với mức che khuất bao nhiêu; chip giải ngược công thức để ra phần trăm lưu thông.

**Câu nối sang slide 9** (nói trong lúc bấm chuyển):

> *Nhưng chỉ có công thức thì chưa đủ.*

### Slide 9 · Học sâu trên CSI kém khi sang môi trường mới

*A (bạn) · 40 giây*

> Chạy lại dữ liệu thật, công thức vẫn có 155 giây báo an toàn khi có người, nên cần thêm AI. Nhưng học sâu trên CSI kém khi đổi môi trường: CrossSense tại MobiCom 2018 chỉ còn khoảng 20% độ chính xác ở nơi mới; EI vẫn chỉ khoảng 75% dù có nhãn từ 22 cặp người và phòng; Widar3.0 phải thu 258 nghìn mẫu. Tức là mỗi lần đổi môi trường lại phải thu và gán nhãn từ đầu.

**Câu nối sang slide 10** (nói trong lúc bấm chuyển):

> *Vì thế nhóm chọn hướng khác: một Tiny AI tự học lại nền ở môi trường mới, chạy ngay trên chip.*

### Slide 10 · Chỉ học lúc trống, vẫn nhận ra người ở nơi mới

*A (bạn) · 55 giây*

**Thao tác:** Chỉ vùng xanh (giây trống), rồi đám chấm cam văng xa (giây có người).

> Tiny AI học thuộc hành lang lúc trống trông như thế nào. Hình bên phải là dữ liệu thật, mỗi chấm một giây: giây trống tụm trong vùng xanh, giây có người văng hẳn ra ngoài. Không cần dữ liệu đám đông, không cần gán nhãn. Khi môi trường sóng thay đổi, mỗi link tự học lại nền, nhưng chỉ khi AI xác nhận trống suốt 30 giây, nên không bao giờ học nhầm người vào nền. Nhờ vậy, sang hành lang mới hệ thống tự thích nghi, đúng chỗ học sâu làm không được. Thử ở một phòng chưa từng thấy, một người đi lại: 1.154 giây có người, AI bắt được hết; 910 giây trống, chỉ 17 giây báo nhầm.

**Câu nối sang slide 11** (nói trong lúc bấm chuyển):

> *Khi gắn AI vào cả hệ thống, kết quả thế nào?*

### Slide 11 · AI giảm mạnh các lỗi nguy hiểm

*A (bạn) · 25 giây*

> So với chỉ dùng công thức, trên cùng dữ liệu thật: báo nhầm an toàn khi có người giảm 89%, học nhầm người thành nền giảm 75%, nhờ AI gác cổng việc học lại nền, và nhảy trạng thái giảm 31%.

**Câu nối sang slide 12** (nói trong lúc bấm chuyển):

> *Và đây là toàn bộ hệ thống chạy trên dữ liệu thật.*

### Slide 12 · Người bước vào → Bị chặn, đi ra → An toàn

*A (bạn) · 35 giây*

**Thao tác:** Bấm phát clip (30 giây), nói theo diễn biến trong clip.

> Dữ liệu thu tối 17/10, chạy qua đúng firmware đang nạp trên chip. Phòng trống: an toàn. Người bước vào: sóng dao động mạnh, chuyển ngay sang bị chặn. Người đi ra: hồi lại an toàn.

**Câu nối sang slide 13** (nói trong lúc bấm chuyển):

> *Biết chỗ nào đông rồi, câu cuối cùng là đi đường nào. Xin mời bạn [tên].*

### Slide 13 · Đông thì đắt, D* Lite tự né trong 1,7 ms

*B (bạn điều hướng) · 40 giây*

> Cảm ơn bạn. Độ đông đo bằng CSI đi thẳng vào hàm chi phí BPR cải tiến: hành lang càng đông, chi phí đi qua càng đắt, theo bình phương; có khói thì cộng phạt; đầy hoặc có cháy thì không đi qua. Đường đi tìm bằng D* Lite: mỗi giây số đo thay đổi, thuật toán chỉ sửa phần thay đổi. Đo trên bản đồ thật 3 tầng, 41 hành lang: 1,7 mili giây mỗi lần, giảm 63% thời gian tính.

**Câu nối sang slide 14** (nói trong lúc bấm chuyển):

> *Nhưng chọn đúng đường thôi chưa đủ, còn phải nhìn trước và chỉ dẫn tận nơi.*

### Slide 14 · Nhìn trước 15 giây, chia tải, chỉ dẫn tại chỗ

*B (bạn điều hướng) · 35 giây*

> Hệ thống không chờ nghẽn rồi mới đổi đường: nó mô phỏng 20 kịch bản tương lai, lấy biên thận trọng P90, nhìn trước 15 giây để bẻ luồng sớm. Tại ngã rẽ, tải được chia tối đa hai tuyến theo độ rộng còn lại. Lệnh đi qua ESP-NOW tới bảng LED và loa tiếng Việt.

**Câu nối sang slide 15** (nói trong lúc bấm chuyển):

> *Em xin trả lời lại cho bạn để kết luận.*

### Slide 15 · WiEvac trả lời đủ 4 câu hỏi

*A (bạn) · 30 giây*

> Cảm ơn bạn. Tóm lại, WiEvac trả lời đủ 4 câu hỏi: bản đồ AR, định vị BLE, biết chỗ đông bằng sóng Wi-Fi với Tiny AI 1,5 KB trên chip, tự học lại nền ở môi trường mới, giảm 89% báo nhầm an toàn, và chỉ đường bằng D* Lite. Bước tiếp theo là thử với đám đông và nhiều tầng. Nhóm em xin cảm ơn Hội đồng và xin lắng nghe câu hỏi.

## Nếu bị chậm (cắt theo thứ tự)

1. **Slide 5:** bỏ câu "Bảng LED và loa nhận lệnh qua ESP-NOW. Ứng dụng Android dùng ARCore." Tiết kiệm khoảng 5 giây.
2. **Slide 9:** bỏ câu Widar3.0. Tiết kiệm khoảng 5 giây.
3. **Slide 6:** bỏ "ứng dụng tự căn thẳng, căn vuông". Tiết kiệm khoảng 3 giây.
4. **Slide 14:** bỏ câu "Tại ngã rẽ…". Tiết kiệm khoảng 5 giây.

Không bao giờ bỏ câu nối: câu nối giữ cho bài liền mạch.

## Đối chiếu barem

Thể lệ VEDC 2026 không công bố tỷ trọng điểm vòng bán kết. Bảng dưới dùng barem bán kết 2025, cộng thêm các nội dung thể lệ 2026 yêu cầu.

| Tiêu chí | Trọng số 2025 | Slide đáp ứng |
|---|---|---|
| Mô hình hệ thống kỹ thuật | 20% | 4 (4 câu hỏi), 5 (phần cứng) |
| Phần cứng & phần mềm | 25% | 5, 8 (công thức trên chip), 10 (Tiny AI trên chip) |
| Demo & prototype hoạt động | 20% | 3 (app 2 điện thoại), 7 (định vị), 12 (CSI dữ liệu thật) |
| Tính ứng dụng & mở rộng | 20% | 2, 9, 15 |
| Trình bày & phản biện | 15% | Kịch bản này + phần câu hỏi |

**Nội dung thể lệ 2026 yêu cầu:**
- mô hình hệ thống: slide 4;
- nguyên lý hoạt động: slide 7, 8, 10;
- thuật toán: slide 10, 13, 14;
- kế hoạch hoàn thiện: slide 15;
- **bản vẽ mạch: còn thiếu**, cần ảnh hoặc sơ đồ mạch thật để chèn vào slide 5.

## Câu hỏi Hội đồng hay hỏi (trả lời dưới 30 giây)

**Một người mà % lưu thông xuống thấp vậy?**
Người đứng chắn đúng đường truyền giữa hai node, và AI chắc chắn có người nên trừ thêm điểm. Trong sơ tán, báo mạnh tay an toàn hơn báo nhầm là an toàn.

**Clip slide 12 có phải chạy thật không?**
Đó là bản thu CSI thô thật tối 17/10, 100 gói mỗi giây, mã SHA-256 của file hiện ngay trên clip. Dữ liệu chạy qua chính mã nguồn firmware đang nạp trên chip. Nhãn có người/trống bấm tay lúc thu, độc lập với thuật toán.

**Dùng mô hình AI gì?**
Mô hình hỗn hợp Gauss 4 thành phần. Nó học vùng dữ liệu lúc hành lang trống; giây nào nằm xa vùng đó là bất thường, tức là có người. Mô hình chỉ 1,5 KB, chạy ngay trên ESP32-S3.

**AI chính xác bao nhiêu?**
Ở phòng chưa từng thấy: bắt 1.154 trên 1.154 giây có người, báo nhầm 17 trên 910 giây trống. Nếu được hỏi chỉ số chuẩn: AUROC 0,9994, với điều kiện một phòng, một người đi lại.

**Sao không dùng học sâu?**
Như slide 9: học sâu trên CSI giảm mạnh khi đổi môi trường và phải thu, gán nhãn lại. Tiny AI chỉ cần hành lang trống, chạy được ngay trên ESP32-S3.

**Môi trường thay đổi thì sao (dịch node, nhiễu, đổi phòng)?**
Mỗi link tự phát hiện môi trường sóng đã đổi, giữ cảnh báo ở mức thận trọng, rồi tự học lại nền. Hệ thống chỉ học lại khi AI xác nhận phòng trống và ổn định suốt 30 giây, nên không học nhầm người vào nền. Vật cản tĩnh đã ghi nhận cũng không bị học thành nền. Không cần hiệu chuẩn lại bằng tay.

**Thử ở bao nhiêu nơi?**
Train ở 2 hành lang, kiểm tra ở một phòng chưa từng thấy, cộng thực địa 1 TX và 2 RX ngày 17–18/10. Đám đông và nhiều tầng là bước tiếp theo.

**Mất sóng, mất điện thì sao?**
Mất dữ liệu thì hệ thống báo "chưa rõ", không báo an toàn. Điều hướng coi hành lang mất sóng như đã đầy nên không dẫn người vào.

**Độ rộng hành lang lấy ở đâu?**
Nhập một lần trên dashboard khi lắp đặt, rồi tự đồng bộ sang bản đồ điều hướng.

**Chi phí?**
Toàn linh kiện phổ thông; một bộ phát phục vụ tới 10 bộ thu. *(Giá cụ thể: nhóm điền theo hóa đơn thật, không đoán.)*

## Cách tập

1. **Lần 1:** đọc to theo file này, bấm giờ, so với cột "Mốc cộng dồn".
2. **Lần 2:** chỉ nhìn slide. Thuộc câu nối trước, vì câu nối là chỗ hay bị quên nhất.
3. **Lần 3:** hai người tập cùng, có bàn giao ở slide 12 và trả lời lại ở slide 14, bấm đủ 3 clip.
4. **Lần cuối:** tập trên đúng máy thi, kiểm tra 3 clip chạy được (nếu dùng PPTX thì nhúng clip bằng `slide_media/embed_clips.py`).
