# Kịch bản thuyết trình WiEvac (12 phút, một người nói)

Theo đúng file **WiEvac — Thuyết trình đề tài.pptx** (16 slide, 3 clip).

**Tổng dự kiến: khoảng 11:00**, đã tính 3 clip và thời gian bấm chuyển slide. Còn khoảng 1 phút dự phòng cho lúc hồi hộp hoặc clip chậm.

**Cách dùng file này:**
- Mỗi slide có một dòng **Ý chính**. Nếu quên lời, chỉ cần nói được ý chính là đủ.
- **Lời nói** là gợi ý để kể, không phải để đọc thuộc từng chữ. Nói tự nhiên theo ý mình cũng được, miễn giữ đúng con số.
- **Câu nối** nói trong lúc bấm sang slide sau, để bài liền mạch.
- Đừng đọc lại chữ trên slide. Slide để Hội đồng nhìn, còn mình kể **vì sao nó hay**.

## Bảng thời gian

| # | Slide | Nói | Mốc cộng dồn |
|---|---|---|---|
| 1 | WiEvac | 15 s | 0:17 |
| 2 | Khi cháy, mọi người dồn về một lối | 40 s | 0:59 |
| 3 | Demo: WiEvac khi tòa nhà có cháy (clip 51 s) | 55 s | 1:56 |
| 4 | Hệ thống trả lời 4 câu hỏi | 30 s | 2:28 |
| 5 | Linh kiện phổ thông, xử lý ngay trên chip | 30 s | 3:00 |
| 6 | Quét phòng → sơ đồ 2D → mô hình 3D | 35 s | 3:37 |
| 7 | Định vị BLE (clip 25 s) | 30 s | 4:09 |
| 8 | Công thức ITU: tính % lưu thông, tự học lại nền | 55 s | 5:06 |
| 9 | Chỉ có công thức thì chưa đủ | 40 s | 5:48 |
| 10 | Vì sao không dùng học sâu? | 40 s | 6:30 |
| 11 | Tiny AI: cách hoạt động | 60 s | 7:32 |
| 12 | Tiny AI vá cả 3 lỗi của công thức | 25 s | 7:59 |
| 13 | Dữ liệu thật (clip 22 s) | 35 s | 8:36 |
| 14 | Đông thì đắt, D* Lite tự né trong 1,7 ms | 50 s | 9:28 |
| 15 | Dự đoán trước 15 giây, chia tải, chỉ dẫn tại chỗ | 45 s | 10:15 |
| 16 | Kết luận | 45 s | 11:02 |

Mỗi lần bấm chuyển slide tính thêm 2 giây. Cộng các chỗ ngập ngừng, tổng thực tế thường khoảng 11:15 đến 11:30.

**Mốc kiểm tra khi tập:**
- Hết slide 5: khoảng **3:00**.
- Hết slide 10: khoảng **6:30**.
- Hết slide 13: khoảng **8:35**.

Chậm quá 20 giây ở một mốc thì dùng ngay mục "Nếu bị chậm" ở cuối file.

---

## Mở đầu

### Slide 1 · WiEvac (15 giây)

**Ý chính:** chào và nói tên đề tài trong một câu.

> Kính thưa Hội đồng, em là [tên], nhóm WiEvac, Trường Đại học Công nghệ Kỹ thuật Hưng Yên. Nhóm dùng chính sóng Wi-Fi trong tòa nhà để biết chỗ nào đông, rồi chỉ đường cho mọi người né chỗ đông.

**Câu nối:** *Vì sao lại cần biết chỗ đông?*

### Slide 2 · Khi cháy, mọi người dồn về một lối (40 giây)

**Ý chính:** biển báo không biết chỗ đông, camera thì không dùng được lúc cháy. Cần một cách khác.

**Thao tác:** chỉ cụm chấm đỏ ở lối gần, rồi lối xa đang trống.

> Khi có cháy, biển thoát hiểm chỉ một hướng cố định, luôn là lối gần nhất. Kết quả là mọi người dồn về một cửa, chen lấn, có thể giẫm đạp, trong khi cửa xa hơn lại trống. Muốn chỉ đường thông minh thì phải biết hành lang nào đang đông. Camera làm được, nhưng xâm phạm riêng tư, và đúng lúc cháy thì khói che, mất điện thì tối. Nên nhóm đặt bài toán: không dùng camera, mà vẫn biết mỗi hành lang còn đi được bao nhiêu phần trăm.

**Câu nối:** *Trước khi nói cách làm, mời Hội đồng xem hệ thống chạy thế nào.*

### Slide 3 · Demo hai điện thoại (55 giây, clip 51 giây)

**Ý chính:** cửa 1 gần hơn nhưng đang đông, ứng dụng tự đổi sang cửa 2.

**Thao tác:** bấm phát clip ngay khi sang slide, nói theo diễn biến. Đợi clip hết mới nói câu nối.

> *(Lúc màn hình đỏ hiện)* Khi phát hiện cháy, mọi điện thoại cài ứng dụng cùng nhận cảnh báo, không cần mạng Internet.
>
> *(Lúc bên trái chỉ về cửa thoát 1)* Bên trái là một người ở tầng 2. Lúc đầu ứng dụng chỉ về cửa thoát 1, vì đó là cửa gần nhất.
>
> *(Lúc hiện "Cửa thoát 1 đang đông người")* Nhưng hệ thống đo được cửa 1 đang đông, nên ứng dụng đổi ngay sang cửa thoát 2. Đây là điều biển báo thường không làm được: cửa gần chưa chắc là cửa nhanh nhất.
>
> *(Lúc xuống cầu thang và ra ngoài)* Người này được dẫn xuống cầu thang và ra ngoài an toàn. Bên phải, người ở tầng 1 được chỉ ra ngoài ngay.

**Câu nối:** *Vậy hệ thống biết cửa 1 đông bằng cách nào?*

### Slide 4 · Hệ thống trả lời 4 câu hỏi (30 giây)

**Ý chính:** chia đề tài thành 4 câu hỏi, câu 3 và 4 là trọng tâm.

**Thao tác:** chỉ lần lượt 4 ô từ trái sang phải.

> Để chỉ đường được như vậy, hệ thống phải trả lời 4 câu hỏi. Một: tòa nhà trông thế nào. Hai: tôi đang ở đâu. Hai câu này điện thoại lo. Ba: chỗ nào đang đông. Bốn: vậy nên đi đường nào. Hai câu sau là trọng tâm và cũng là cái mới nhất của đề tài, vì nó cần biết được đám đông mà không dùng camera.

**Câu nối:** *Và tất cả chạy trên phần cứng rất rẻ.*

### Slide 5 · Phần cứng và phần mềm (30 giây)

**Ý chính:** linh kiện phổ thông, một con chip làm nhiều việc, cả hệ thống chạy offline.

> Điểm em muốn nhấn mạnh là toàn linh kiện phổ thông. Một con chip ESP32-S3 làm được ba việc: phát sóng, thu sóng, và phát tín hiệu Bluetooth để định vị. Mọi tính toán, kể cả AI, chạy ngay trên con chip đó. Raspberry Pi 5 làm trung tâm tìm đường. Bảng LED và loa chỉ dẫn tại chỗ. Cả hệ thống chạy offline, mất Internet vẫn hoạt động, đúng lúc sự cố là lúc cần nhất.

**Câu nối:** *Giờ em đi lần lượt từng câu hỏi.*

---

## ① Bản đồ và ② Định vị

### Slide 6 · Quét phòng bằng camera AR (35 giây)

**Ý chính:** không cần bản vẽ, cầm điện thoại quét là có bản đồ.

> Câu thứ nhất: tòa nhà trông thế nào. Nhiều tòa nhà cũ không còn bản vẽ. Với WiEvac, chỉ cần cầm điện thoại ngắm vào 4 góc chân tường, ứng dụng dùng ARCore lấy tọa độ và vẽ ra sơ đồ, tính luôn diện tích. Tay người run thì cạnh hơi lệch, nên ứng dụng tự nắn cho thẳng, cho vuông. Nhập thêm chiều cao trần và ngắm khung cửa là có mô hình 3D, dùng luôn làm bản đồ sơ tán.

**Câu nối:** *Có bản đồ rồi, câu thứ hai: người đang ở đâu trên bản đồ đó.*

### Slide 7 · Định vị BLE (30 giây, clip 25 giây)

**Ý chính:** dùng lại chính các node ESP32, điện thoại tự biết mình ở đâu, không cần GPS.

**Thao tác:** bấm phát clip, nói trong lúc clip chạy.

> Trong nhà thì GPS không dùng được. Nhóm tận dụng luôn các node ESP32: mỗi node phát tín hiệu Bluetooth liên tục, giống như một ngọn hải đăng nhỏ. Điện thoại nghe tất cả, node nào mạnh nhất thì người đang ở gần node đó. Người đi tới đâu, điện thoại tự chuyển sang node mạnh hơn, vị trí trên bản đồ cập nhật theo. Không cần thêm thiết bị, không cần mạng.

**Câu nối:** *Biết người ở đâu rồi. Giờ đến phần trọng tâm: chỗ nào đang đông.*

---

## ③ Đo độ đông: phần trọng tâm

### Slide 8 · Công thức ITU (55 giây)

**Ý chính:** người chắn sóng Wi-Fi làm sóng yếu đi. Nhóm dùng công thức chuẩn quốc tế để đổi độ yếu đó ra % lưu thông, và hệ thống tự thích nghi khi môi trường thay đổi.

**Thao tác:** chỉ công thức, rồi ô ①, rồi ô ②.

> Đây là cái lạ nhất của đề tài: dùng chính sóng Wi-Fi làm cảm biến. Mỗi gói Wi-Fi mang theo thông tin sóng đã đi qua không gian thế nào, gọi là CSI, đo trên 52 dải sóng nhỏ, 100 lần mỗi giây. Khi có người đứng chắn giữa hai node, sóng bị yếu đi, giống như ánh sáng bị che bớt.
>
> Yếu đi bao nhiêu thì nhóm không tự đoán, mà dùng công thức nhiễu xạ ITU-R P.526 của Liên minh Viễn thông Quốc tế, cho hai việc. Một: so với lúc hành lang trống, chip tính ngược ra phần trăm còn đi được. Hai: dời node hay kê lại bàn ghế thì hệ thống tự học lại "lúc trống" mới, không cần hiệu chuẩn bằng tay.

**Câu nối:** *Nhưng khi chạy thử trên dữ liệu thật, nhóm thấy chỉ có công thức thì chưa đủ.*

### Slide 9 · Chỉ có công thức thì chưa đủ (40 giây)

**Ý chính:** nhóm tự chỉ ra 3 lỗi của công thức. Gốc rễ là công thức không phân biệt được "có người" với "môi trường đổi".

**Thao tác:** chỉ lần lượt 3 con số từ trái sang phải.

> Nhóm chạy lại dữ liệu thật chỉ với công thức và tìm ra 3 lỗi. 155 giây hệ thống báo an toàn trong khi có người, lỗi nguy hiểm nhất khi sơ tán. 12 lần học nhầm người vào nền: người đứng yên lâu thì sóng ổn định, công thức tưởng là trống. Và kết quả nhảy qua lại hơn 5 lần mỗi phút. Gốc rễ chỉ có một: công thức không phân biệt được sóng thay đổi vì có người, hay vì môi trường thay đổi. Cần thêm một bộ não.

**Câu nối:** *Nhưng dùng AI loại nào?*

### Slide 10 · Vì sao không dùng học sâu? (40 giây)

**Ý chính:** học sâu phải thu dữ liệu và gán nhãn lại mỗi khi sang nơi mới, nên khó mang ra thực tế.

> Cách phổ biến là học sâu. Nhưng các nghiên cứu hàng đầu cho thấy học sâu trên sóng Wi-Fi rất kém khi sang môi trường mới. CrossSense, hội nghị MobiCom 2018: phương pháp có sẵn chỉ còn khoảng 20% độ chính xác ở nơi mới. EI, cùng hội nghị, có chống nhiễu môi trường vẫn chỉ khoảng 75%. Widar3.0 giữ được trên 80% nhưng phải thu tới 258 nghìn mẫu. Tức là mỗi tòa nhà mới lại phải thu dữ liệu, gán nhãn lại từ đầu. Với một hệ thống sơ tán lắp ở nhiều nơi, làm vậy là không khả thi.

**Câu nối:** *Nên nhóm chọn một hướng khác hẳn.*

### Slide 11 · Tiny AI: cách hoạt động (60 giây)

**Ý chính:** AI chỉ học một thứ: hành lang lúc trống. Cái gì khác thường thì là có người. Mô hình nhỏ 1,5 KB, chạy ngay trên chip, sang phòng mới không cần học lại.

**Thao tác:** chỉ cột trái (huấn luyện), rồi cột phải (trên chip) từ trên xuống, kết bằng dòng cuối slide.

> Ý tưởng rất đơn giản. Thay vì dạy AI người trông thế nào, nhóm chỉ cho AI học một thứ: hành lang lúc trống trông thế nào. Giống như một người bảo vệ thuộc lòng hành lang lúc vắng: có gì khác thường là biết ngay có người, không cần ai chỉ trước.
>
> Huấn luyện một lần, không gán nhãn, ra mô hình hỗn hợp Gauss chỉ 1,5 KB, nạp thẳng vào chip ESP32-S3.
>
> Mỗi giây, chip tính 42 đặc trưng về cách sóng rung, đo xem khác thường bao nhiêu. Vượt ngưỡng là có người, AI hạ điểm. Trống suốt 30 giây mà nền có đổi, AI mới cho học lại nền, nên không học nhầm người vào nền.
>
> Và vì AI nhìn cách sóng rung chứ không nhìn sóng mạnh hay yếu, vốn mỗi phòng một khác, nên sang phòng mới không cần huấn luyện lại.

**Câu nối:** *Và kết quả trên dữ liệu thật như sau.*

### Slide 12 · Tiny AI vá cả 3 lỗi (25 giây)

**Ý chính:** cùng dữ liệu, có AI thì giảm 89%, 75%, 31% đúng 3 lỗi ở slide 9.

**Thao tác:** chỉ lần lượt 3 cặp cột, từ trái sang phải.

> Cùng dữ liệu thật, cùng 3 lỗi vừa rồi. Báo nhầm an toàn khi có người: từ 155 giây xuống 17 giây, giảm 89%. Học nhầm người vào nền: từ 12 lần xuống 3, giảm 75%. Nhảy trạng thái giảm 31%. Tất cả nhờ một mô hình 1,5 KB, chưa từng thấy căn phòng này lúc huấn luyện.

**Câu nối:** *Và đây là toàn bộ hệ thống chạy trên dữ liệu thật.*

### Slide 13 · Dữ liệu thật (35 giây, clip 22 giây)

**Ý chính:** dữ liệu sóng thật, chạy qua đúng firmware trên chip: người vào thì báo bị chặn, người ra thì báo an toàn.

**Thao tác:** nói một câu giới thiệu rồi bấm phát clip, nói theo diễn biến.

> Đây là sóng Wi-Fi thật thu tối 17 tháng 10, chạy qua đúng chương trình đang nạp trên chip. Phía trên là sóng của 52 dải, phía dưới là phần trăm lưu thông.
>
> *(Clip chạy)* Phòng trống: sóng phẳng, hệ thống báo an toàn. Người bước vào: sóng xao động mạnh, hệ thống chuyển ngay sang bị chặn. Người đi ra: sóng lặng lại, hệ thống hồi về an toàn. Màu nền là nhãn nhóm bấm tay lúc thu, khớp với kết quả của hệ thống.

**Câu nối:** *Biết chỗ nào đông rồi, câu hỏi cuối cùng là: đi đường nào.*

---

## ④ Điều hướng

### Slide 14 · Đông thì đắt, D* Lite tự né (50 giây)

**Ý chính:** mỗi hành lang có một "giá", càng đông càng đắt. Hệ thống tìm đường rẻ nhất và cập nhật cực nhanh mỗi giây.

**Thao tác:** chỉ đường cong (càng đông càng dốc), rồi công thức, rồi dòng 1,7 ms.

> Nhóm gán cho mỗi hành lang một cái giá, giống phí đi đường. Hành lang càng đông thì giá càng đắt, và đắt theo bình phương, tức là đông gấp đôi thì đắt gấp bốn. Có khói thì cộng thêm phạt. Đầy hoặc có cháy thì cấm đi. Công thức này dựa trên hàm BPR mà ngành giao thông dùng để tính kẹt xe.
>
> Số đo đông thay đổi mỗi giây, nên đường cũng phải tính lại mỗi giây. Nhóm dùng thuật toán D* Lite: không tính lại từ đầu, mà chỉ sửa đúng phần vừa thay đổi. Trên bản đồ 3 tầng, 41 hành lang, mỗi lần chỉ mất 1,7 mili giây, nhanh hơn 63% so với tính lại từ đầu.

**Câu nối:** *Nhưng chọn đúng đường lúc này thôi chưa đủ. Hệ thống còn phải nhìn trước.*

### Slide 15 · Dự đoán trước 15 giây (45 giây)

**Ý chính:** không đợi nghẽn mới đổi đường; dự đoán trước 15 giây, chia người ra hai đường, và chỉ dẫn bằng LED, loa ngay tại chỗ.

**Thao tác:** chỉ đồ thị (đường cam vượt ngưỡng trước), rồi lần lượt 3 ô bên phải.

> Đợi nghẽn rồi mới đổi đường thì đã muộn. Hệ thống mô phỏng 20 tình huống sắp tới, lấy trường hợp xấu mức P90, nhìn trước 15 giây. Thấy sắp nghẽn là bẻ luồng ngay.
>
> Ở ngã rẽ, hệ thống không đẩy tất cả sang một đường mới, vì như vậy lại dồn ứ ở đường mới. Nó chia người ra tối đa hai tuyến, theo độ rộng còn lại của mỗi tuyến.
>
> Lệnh chỉ dẫn đi qua ESP-NOW, sóng trực tiếp giữa các chip, không cần router hay Internet, tới bảng LED và loa nói tiếng Việt ngay tại hành lang, để cả người không cầm điện thoại cũng biết đi đường nào.

**Câu nối:** *Em xin tóm lại.*

### Slide 16 · Kết luận (45 giây)

**Ý chính:** nhắc lại 4 câu hỏi và cái hay nhất của mỗi câu.

> Tóm lại, WiEvac trả lời đủ 4 câu hỏi. Tòa nhà trông thế nào: cầm điện thoại quét là có bản đồ, không cần bản vẽ. Tôi đang ở đâu: định vị bằng Bluetooth, chạy offline. Chỗ nào đông: dùng chính sóng Wi-Fi, không camera, với công thức chuẩn ITU và một Tiny AI 1,5 KB chạy ngay trên chip, giảm 89% số lần báo nhầm an toàn. Đi đường nào: D* Lite tính lại trong 1,7 mili giây, nhìn trước 15 giây, chỉ dẫn tận nơi bằng LED và loa.
>
> Em xin cảm ơn Hội đồng, và rất mong nhận được câu hỏi.

---

## Nếu bị chậm (cắt theo thứ tự)

1. **Slide 6:** bỏ câu "Tay người run thì cạnh hơi lệch…". Tiết kiệm khoảng 5 giây.
2. **Slide 10:** bỏ câu Widar3.0. Tiết kiệm khoảng 6 giây.
3. **Slide 5:** bỏ câu "Bảng LED và loa chỉ dẫn tại chỗ". Tiết kiệm khoảng 3 giây.
4. **Slide 15:** rút đoạn chia tải còn "Ở ngã rẽ, người được chia ra tối đa hai tuyến." Tiết kiệm khoảng 7 giây.
5. **Slide 14:** bỏ câu "Công thức này dựa trên hàm BPR…". Tiết kiệm khoảng 5 giây.

**Không bao giờ cắt:** slide 3 (demo), slide 11 (Tiny AI), slide 12 (kết quả). Đó là ba chỗ ghi điểm nhất.

## 5 điểm "hay, mới, lạ" phải nói bằng được

1. **Không camera:** dùng chính sóng Wi-Fi làm cảm biến, không xâm phạm riêng tư, khói và bóng tối không ảnh hưởng.
2. **Cửa gần chưa chắc là cửa nhanh:** hệ thống đổi sang cửa xa khi cửa gần đông (demo slide 3).
3. **Có cơ sở khoa học:** công thức chuẩn ITU, không tự đoán; nhóm tự chỉ ra giới hạn của công thức.
4. **Tiny AI chỉ học lúc trống:** không gán nhãn, 1,5 KB, chạy trên chip, sang phòng mới không cần học lại. Đây là chỗ học sâu chưa làm được.
5. **Nhìn trước và chỉ dẫn tận nơi:** dự đoán 15 giây, chia tải hai tuyến, LED và loa chạy offline.

## Câu hỏi Hội đồng hay hỏi (trả lời dưới 30 giây)

**CSI là gì, khác gì đo cường độ Wi-Fi bình thường?**
Cường độ Wi-Fi chỉ là một con số. CSI cho biết sóng đi qua không gian thế nào trên từng dải trong 52 dải nhỏ, nên nhạy hơn rất nhiều với người đứng chắn.

**Một người mà % lưu thông xuống thấp vậy?**
Người đứng chắn đúng đường truyền giữa hai node, và AI chắc chắn có người nên hạ điểm thêm. Trong sơ tán, báo thận trọng an toàn hơn báo nhầm là an toàn.

**Clip slide 13 có phải chạy thật không?**
Đó là bản thu CSI thô thật tối 17/10, 100 gói mỗi giây, chạy qua đúng mã nguồn firmware đang nạp trên chip. Nhãn có người, trống do nhóm bấm tay lúc thu, độc lập với thuật toán. Đoạn giữa lúc người đứng chắn được lược bớt cho clip ngắn; số giây trên trục vẫn là giờ thật.

**Dùng mô hình AI gì?**
Mô hình hỗn hợp Gauss 4 thành phần. Nó học vùng dữ liệu lúc hành lang trống; giây nào nằm xa vùng đó là khác thường, tức là có người. Mô hình chỉ 1,5 KB, chạy ngay trên ESP32-S3.

**AI chính xác bao nhiêu?**
Ở phòng chưa từng thấy: bắt được 1.154 trên 1.154 giây có người, báo nhầm 17 trên 910 giây trống. Nếu được hỏi chỉ số chuẩn: AUROC 0,9994, với điều kiện một phòng, một người đi lại.

**Sao sang phòng mới không cần huấn luyện lại?**
AI không nhìn mức sóng mạnh hay yếu, vốn khác nhau ở mỗi phòng, mà nhìn cách sóng rung: độ dao động, độ gồ ghề, độ lặp lại trên 8 nhóm dải sóng. Mỗi link còn tự lấy mức nhiễu của chính nó làm chuẩn khi khởi động. Mô hình huấn luyện ở 2 hành lang, chạy thẳng ở phòng C chưa từng thấy.

**Môi trường thay đổi thì sao (dời node, kê đồ)?**
Hệ thống tự phát hiện sóng nền đã đổi, giữ cảnh báo ở mức thận trọng, rồi tự học lại nền. Chỉ học lại khi AI xác nhận phòng trống suốt 30 giây, nên không học nhầm người vào nền. Không cần hiệu chuẩn bằng tay.

**Thử ở bao nhiêu nơi?**
Huấn luyện ở 2 hành lang, kiểm tra ở một phòng chưa từng thấy, cộng thử thực địa 1 bộ phát và 2 bộ thu ngày 17–18/10. Đám đông và nhiều tầng là bước tiếp theo.

**Mất sóng, mất điện thì sao?**
Mất dữ liệu thì hệ thống báo "chưa rõ", không bao giờ báo an toàn. Điều hướng coi hành lang mất sóng như đã đầy, nên không dẫn người vào.

**Đo 1,7 ms trên máy nào?**
Trên máy tính phát triển, bản đồ thật 3 tầng, 41 hành lang. So với tính lại từ đầu là 4,6 ms, giảm 63%.

**Độ rộng hành lang lấy ở đâu?**
Nhập một lần trên dashboard khi lắp đặt, rồi tự đồng bộ sang bản đồ điều hướng.

**Chi phí?**
Toàn linh kiện phổ thông: ESP32-S3, Raspberry Pi 5, bảng LED, loa. *(Giá cụ thể: điền theo hóa đơn thật của nhóm, không đoán.)*

## Cách tập (tối nay)

1. **Lần 1:** đọc to cả file, bấm giờ từng slide. Đánh dấu chỗ nào vấp.
2. **Lần 2:** chỉ nhìn dòng **Ý chính** của mỗi slide và tự kể bằng lời của mình. Đây là lần quan trọng nhất: kể được là không cần học thuộc.
3. **Lần 3:** chạy trên đúng file PPTX, bấm đủ 3 clip, bấm giờ cả bài. Mục tiêu khoảng 11 phút.
4. **Học thuộc 3 thứ:** các câu nối, các con số (155, 12, 5,16; 89%, 75%, 31%; 1,5 KB; 1,7 ms; 15 giây), và câu mở đầu.
5. **Trước giờ thi:** mở file trên máy thi, bấm thử 3 clip xem có chạy và có tiếng không (các clip không có tiếng, chỉ có hình).
