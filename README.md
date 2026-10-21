# ⚡ UTECal - HCMUTE Calendar & Realtime Sync

<div align="center">
  <p><strong>Nền tảng Tự động hóa Thời khóa biểu, Lịch thi & Đồng bộ Lịch dành cho Sinh viên HCMUTE</strong></p>
  <p>
    <a href="https://online.hcmute.edu.vn"><img src="https://img.shields.io/badge/HCMUTE-Portal%20API-0071e3?style=flat-square" alt="HCMUTE Portal"></a>
    <a href="https://t.me/hcmute_calendar_bot"><img src="https://img.shields.io/badge/Telegram-Bot%20AI-229ED9?style=flat-square&logo=telegram&logoColor=white" alt="Telegram Bot"></a>
    <img src="https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python Version">
    <img src="https://img.shields.io/badge/Privacy-Local%20First%20%26%20Zero--Knowledge-10b981?style=flat-square" alt="Privacy First">
    <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License">
  </p>
</div>

---

## 🌟 Giới Thiệu

**UTECal** là giải pháp đồng bộ thời khóa biểu và lịch thi thông minh dành cho sinh viên trường Đại học Sư phạm Kỹ thuật TP.HCM ([online.hcmute.edu.vn](https://online.hcmute.edu.vn)). 

Ứng dụng kết nối trực tiếp với cổng đào tạo HCMUTE để tự động hóa việc đưa lịch học vào **Apple Calendar (iPhone, Mac, Apple Watch)**, **Google Calendar (Android, PC)** và kích hoạt **Trợ lý Telegram Bot** nhắc lịch thông minh.

> **🔒 Thiết kế ưu tiên quyền riêng tư (Local-First & Zero-Knowledge):**  
> Dành cho những bạn quan tâm tới an toàn thông tin: UTECal được phát triển theo hướng mã nguồn mở hoàn toàn. Bạn có thể tự tải mã nguồn về và chạy trực tiếp trên máy tính cá nhân (Localhost). Mọi kết nối sẽ đi thẳng từ máy của bạn tới máy chủ đào tạo HCMUTE mà không thông qua bất kỳ server trung gian nào.

---

## ✨ Tính Năng Nổi Bật

### 1. 📱 Giao diện Thời khóa biểu Đa góc nhìn (Thứ / Buổi / Tuần)
- **Bố cục ứng dụng lịch trực quan:** Trình bày thời khóa biểu mạch lạc theo các tiêu chuẩn:
  - 📊 **Lưới Buổi (Matrix View):** Bảng ma trận 7 ngày (Thứ 2 ➔ Chủ Nhật) × 3 buổi (Sáng, Chiều, Tối). Mỗi môn học hiển thị với màu sắc riêng biệt, hiển thị rõ mã phòng, tên môn, giảng viên và khung giờ.
  - 📅 **Lịch Từng Ngày (Day View):** Gom nhóm ca học theo từng ngày, có thanh chọn nhanh thứ trong tuần (`[T2]`, `[T3]`,...) tối ưu cho màn hình điện thoại.
  - 📋 **Lịch trình Toàn khóa (Agenda):** Danh sách tổng hợp toàn bộ các ca học trong học kỳ.
- **Điều hướng Tuần linh hoạt:** Nút `[◀ Tuần trước]`, `[Hôm nay]`, `[Tuần sau ▶]` kèm khoảng ngày cụ thể, tự động làm nổi bật ngày hiện tại.
- **Bảng Chi tiết Ca học & Đánh dấu Giảng viên cho nghỉ:** Chạm vào bất kỳ buổi học nào để xem chi tiết phòng học, cơ sở, giảng viên và đánh dấu giảng viên báo nghỉ chỉ với 1 chạm.
- **Giao diện hiện đại:** Hỗ trợ Dark Mode tinh tế, tương phản cao, mượt mà trên cả trình duyệt máy tính và điện thoại di động.

### 2. ⚡ Đồng bộ Tức thì vào Apple Calendar & Google Calendar
- **Đăng ký lịch trực tiếp (Live Subscription Feed):** Cung cấp đường dẫn chuẩn `webcal://` và định dạng `.ics` (RFC-5545).
- **Tự động cập nhật:** Khi có lịch học bù, đổi phòng hoặc giảng viên cho nghỉ, ứng dụng lịch trên thiết bị của bạn tự động đồng bộ mà không cần tải lại file.
- **Phân loại màu sắc thông minh:**
  - 🔴 **Màu đỏ:** Lịch thi kết thúc học phần & Hạn chót học phí (ưu tiên cao nhất).
  - 🎨 **Màu sắc theo môn:** Mỗi môn học mang một mã màu riêng, kèm thông báo nhắc trước giờ học.

### 3. 🤖 Trợ lý Telegram Bot & Trí tuệ Nhân tạo Gemini AI
- **☀️ Bản tin sáng 06:30:** Tự động gửi tin nhắn tổng hợp lịch học, số tiết, giờ bắt đầu và phòng học trong ngày.
- **🔥 Đếm ngược kỳ thi:** Nhắc nhở phòng thi, số báo danh và thời gian làm bài trước ngày thi.
- **💬 Trò chuyện tiếng Việt tự nhiên:**
  - Hỏi đáp lịch học linh hoạt: *"tuần sau học gì"*, *"mai học phòng nào"*, *"khi nào thi môn kế tiếp"*,...
  - Nhận diện các địa điểm quen thuộc tại HCMUTE (tòa nhà A1, A2, B, C, D, E, X, ký túc xá).
- **🏖️ Xác nhận GV cho nghỉ đám đông (Crowdsourcing):** Khi giảng viên cho nghỉ, sinh viên có thể xác nhận trên Web hoặc Telegram (`/nghi`). Khi đủ tỷ lệ đồng thuận trong lớp, hệ thống sẽ tự động cập nhật lịch cho cả lớp.
- **➕ Thêm lịch học bù:** Nhập cú pháp ngắn `/bu <Tên môn>, <Ngày>, <Giờ>, <Phòng>` để thêm nhanh lịch học bù vào thiết bị.

### 4. 📢 Bộ Lọc Hạn Chót & Cảnh Báo (Consideration Engine)
- Tự động bóc tách và thông báo các mốc thời gian quan trọng từ cổng đào tạo:
  - 💰 Hạn đóng học phí & công nợ (tránh bị cấm thi hoặc hủy học phần).
  - 🎓 Sinh hoạt công dân sinh viên (đầu khóa / giữa khóa / cuối khóa).
  - 📜 Hạn nộp chứng chỉ Tiếng Anh / Tin học chuẩn đầu ra.
  - ✍️ Khảo sát đánh giá môn học & Đăng ký môn học.

### 5. 💡 Hòm Thư Gợi Ý & Hệ Thống AI Đánh Giá (Feedback)
- Sinh viên có thể gửi ý kiến, đề xuất tính năng mới hoặc phản hồi trực tiếp trên tab **Gợi ý**.
- Hệ thống hỗ trợ đánh giá, phân loại mức độ cần thiết của đóng góp để hoàn thiện ứng dụng ngày một tốt hơn.

---

## 🚀 Hướng Dẫn Chạy Cục Bộ (Localhost)

Dành cho những bạn muốn tự quản lý dữ liệu 100% trên máy tính của mình mà không gửi thông tin qua bất kỳ website trung gian nào:

### 1. Yêu cầu môi trường
- Máy tính chạy **Windows**, **macOS** hoặc **Linux**.
- Đã cài đặt **Python 3.10 trở lên** (Tải tại [python.org](https://www.python.org/downloads/) — *Lưu ý tích chọn "Add Python to PATH" khi cài đặt trên Windows*).
- Git (hoặc tải trực tiếp file mã nguồn ZIP).

---

### 2. Khởi chạy 1-Click trên Windows (Cách nhanh nhất)

1. Tải repository này về máy (Bấm nút **Code** ➔ **Download ZIP** và giải nén, hoặc dùng `git clone`).
2. Mở thư mục dự án và nhấp đúp chuột vào file:
   ```text
   start.bat
   ```
3. Script sẽ tự động:
   - Kiểm tra và khởi tạo môi trường Python ảo (`.venv`).
   - Cài đặt đầy đủ các thư viện cần thiết từ `requirements.txt`.
   - Khởi động máy chủ web và tự động mở trình duyệt tại:
     ```
     http://127.0.0.1:5000
     ```
4. Đăng nhập bằng MSSV và mật khẩu cổng đào tạo để sử dụng ngay!

---

### 3. Khởi chạy thủ công trên Terminal (Windows / macOS / Linux)

Nếu bạn sử dụng macOS, Linux hoặc muốn chạy lệnh từng bước trong terminal:

**Bước 1: Tải mã nguồn về máy**
```bash
git clone https://github.com/tronghunh/utecal.git
cd utecal
```

**Bước 2: Tạo môi trường ảo và cài đặt thư viện**
```bash
# Tạo môi trường ảo
python -m venv .venv

# Kích hoạt môi trường:
# Trên Windows (Command Prompt hoặc PowerShell):
.venv\Scripts\activate
# Trên macOS / Linux:
source .venv/bin/activate

# Cài đặt thư viện:
pip install -r requirements.txt
```

**Bước 3: Khởi chạy ứng dụng**
```bash
python run.py
```
Trình duyệt sẽ tự động mở trang chủ tại `http://127.0.0.1:5000`.

---

### 4. Cấu hình tính năng mở rộng (Tùy chọn)

- **Sử dụng cơ bản (Xem TKB, Lịch thi, Xuất .ics, Đồng bộ Apple/Google Calendar):**  
  👉 **Không cần cấu hình bất kỳ API Key nào!** Ứng dụng hoạt động ngay sau khi khởi chạy.

- **Sử dụng Bot Telegram & AI Gemini (Nếu muốn):**  
  Bạn có thể cấu hình các biến môi trường hoặc chỉnh sửa file `config/config.json`:
  - `TELEGRAM_BOT_TOKEN`: Token lấy từ [@BotFather](https://t.me/BotFather).
  - `GEMINI_API_KEY`: Khóa API miễn phí từ [Google AI Studio](https://aistudio.google.com).

---

## 🔒 Cam Kết Bảo Mật & Quyền Riêng Tư

> **Nguyên tắc cốt lõi: Minh bạch và Tự chủ dữ liệu**

- **Không lưu trữ mật khẩu:** Mật khẩu đăng nhập chỉ được sử dụng trong một yêu cầu duy nhất gửi trực tiếp tới máy chủ cổng đào tạo HCMUTE (`https://portalapi.hcmute.edu.vn`) qua giao thức mã hóa HTTPS để lấy phiên xác thực, sau đó bị xóa ngay khỏi bộ nhớ RAM (`del password`).
- **Kho dữ liệu nội bộ được bảo vệ:** Toàn bộ dữ liệu phiên (token đăng nhập, cache lịch đã tải) được lưu cục bộ trong thư mục `data/` trên máy tính của bạn và được bảo vệ nghiêm ngặt bởi file [`.gitignore`](.gitignore), đảm bảo **không bao giờ bị đẩy lên GitHub**.
- **Kết nối trực tiếp (Direct Connection):** Khi chạy cục bộ, máy tính của bạn giao tiếp trực tiếp với máy chủ của trường, không có máy chủ thứ ba nào đứng giữa ghi lại thông tin hay traffic của bạn.
- **Quyền xóa sạch dữ liệu (Wipe Data):** Bất cứ khi nào bạn nhấn **Đăng xuất** hoặc **Xóa toàn bộ dữ liệu**, phiên làm việc và các dữ liệu liên quan sẽ lập tức bị xóa bỏ hoàn toàn.

---

## 🛠️ Cấu Trúc Dự Án

```
utecal/
├── web/
│   ├── index.html           # Giao diện chính người dùng (Dark mode, hỗ trợ PC & Mobile)
│   └── admin.html           # Bảng điều khiển quản lý nội bộ
├── src/
│   ├── hcmute_api.py        # Module kết nối trực tiếp cổng đào tạo HCMUTE Portal API
│   ├── schedule_parser.py   # Bóc tách TKB và ánh xạ khung giờ tiết học chuẩn HCMUTE
│   ├── conversational_ai.py # Bộ não AI xử lý ngôn ngữ tự nhiên tiếng Việt & Gemini
│   ├── telegram_bot.py      # Dịch vụ thông báo và tương tác qua Telegram Bot
│   ├── notice_analyzer.py   # Bộ phân tích thông báo và nhắc hạn chót (Consideration)
│   ├── feedback_manager.py  # Bộ xử lý và phân loại ý kiến đóng góp sinh viên
│   ├── ics_exporter.py      # Bộ tạo file lịch chuẩn Apple/Google Calendar (RFC-5545)
│   └── web_server.py        # Máy chủ REST API xây dựng trên Flask
├── config/
│   └── config.json          # Cấu hình hệ thống (không chứa mật khẩu hay thông tin nhạy cảm)
├── tests/
│   ├── test_components.py   # Kiểm thử các thành phần hệ thống
│   ├── test_feedback.py     # Kiểm thử luồng tiếp nhận gợi ý
│   └── test_sessions.py     # Kiểm thử cơ chế bảo mật phiên
├── requirements.txt         # Danh mục thư viện Python cần thiết
├── start.bat                # Khởi động nhanh 1-click cho người dùng Windows
├── run.py                   # Script khởi chạy ứng dụng cục bộ
├── run.bat                  # Script khởi chạy qua batch
└── main.py                  # Giao diện dòng lệnh CLI (Terminal)
```

---

## 🤝 Đóng Góp Phát Triển (Contributing)

Mọi đóng góp, báo lỗi hoặc đề xuất cải tiến từ cộng đồng sinh viên HCMUTE đều được hoan nghênh:
1. Fork dự án này.
2. Tạo một nhánh tính năng mới (`git checkout -b feature/tinh-nang-moi`).
3. Commit các thay đổi (`git commit -m 'Thêm tính năng...'`).
4. Đẩy mã lên nhánh của bạn (`git push origin feature/tinh-nang-moi`).
5. Tạo một **Pull Request** mới.

---

## 📄 Bản Quyền & Tác Giả (License & Author)

Dự án được phân phối dưới giấy phép mã nguồn mở **MIT License**. Tự do sử dụng, chỉnh sửa và chia sẻ vì mục đích hỗ trợ học tập cho cộng đồng sinh viên HCMUTE.

<div align="center">
  <p>Tool by <strong><a href="https://github.com/tronghunh">@tronghunh</a></strong></p>
  <p>Dành tặng cộng đồng sinh viên <strong>HCMUTE</strong> 💙</p>
</div>
