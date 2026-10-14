# Chức năng đang chạy

Điểm 0–100 là chỉ số thông thoáng cục bộ của từng link, chưa phải phần trăm người hay phần trăm hành lang đã hiệu chuẩn.

## ESP phát (TX)

- Phát gói đo CSI liên tục trên ESP-NOW để RX bắt sóng
- Gửi hello, nhận pair reply, ghép cặp từng RX
- Bỏ RX không còn phản hồi
- Đếm gửi thành công, thất bại, nghẽn hàng đợi
- Phát loa điều hướng khi nhận lệnh (đi thẳng, rẽ trái, rẽ phải, sơ tán, lối thoát, dừng, chờ, tắt)
- Trả ACK lệnh loa
- Đổi số node bằng serial `ID=` hoặc `SET_ID=`

## ESP nhận (RX)

- Bắt CSI, chỉ nhận MAC TX đã ghép cặp
- Loại khung CSI hỏng, sai dạng, sai metadata radio
- Lọc nhiễu, tính Doppler và độ lệch phổ của chính link đó
- Ra điểm thông thoáng 0–100
- Ra điểm độ rộng: nền trống của chính link, tụt khi phổ đổi hoặc mất bin
- Ra trạng thái: thông (`PASSABLE`), kém (`DEGRADED`), tắc (`BLOCKED`), không đủ dữ liệu (`UNKNOWN`)
- Học nền hành lang trống; không học khi CSI hỏng, mất gói, hoặc đang có người
- Đổi nền khi hành lang đổi, không khóa ngưỡng chỗ cũ
- Giữ trạng thái tắc một lúc khi đang tắc rồi mất sóng
- Dữ liệu cũ, kém chất lượng hoặc mơ hồ thì `UNKNOWN` / `DEGRADED`, không bịa 0%
- Gửi một kết quả mỗi cửa sổ lên Pi qua UDP
- Node còn Wi-Fi tới Pi làm cổng (gateway), chuyển tiếp node khác
- Mất Wi-Fi tới Pi thì thành relay, nhảy qua node còn nối được Pi
- Nhịp tim mesh, báo node mất, chân GPIO báo lỗi
- Bỏ gói mesh trùng
- Phát loa theo lệnh Pi, chuyển tiếp lệnh sang node khác, trả ACK
- Hiệu chuẩn hành lang trống: giữ nút BOOT 2 giây, hoặc gõ `C` trên serial
- Đổi số node bằng serial `ID=` hoặc `SET_ID=`

## Pi

- Nhận UDP kết quả từ ESP, không tính lại thuật toán CSI
- Kiểm tra gói: đúng schema, CRC, đúng link, không trùng, không lệch thứ tự, không quá cũ
- Mỗi link một trạng thái riêng
- Dashboard: điểm thông thoáng, điểm độ rộng, trạng thái, đường đi (thẳng lên Pi hoặc nhảy qua node)
- Node mất Wi-Fi mà còn relay thì hiện đường nhảy; mất hẳn thì đánh `STALE`
- Ghi dữ liệu: bắt đầu, dừng, hẹn theo giờ
- Đặt lịch thu; trong lịch ép nhãn hành lang trống
- Gán nhãn từng node: trống / có người / không rõ
- Thêm, sửa, xóa node trên cấu hình
- Gửi lệnh loa xuống node (một node hoặc cả mạng), hiện ACK và thời gian phản hồi
- API: sức khỏe, tổng quan, node, bản ghi, lệnh điều hướng
