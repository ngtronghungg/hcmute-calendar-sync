# ⚡ HCMUTE Calendar & Realtime Sync

<div align="center">
  <p><strong>Nền tảng Tự động hóa Thời khóa biểu, Lịch thi & Trợ lý AI Sinh viên Trường ĐH Sư phạm Kỹ thuật TP.HCM</strong></p>
  <p>
    <a href="https://online.hcmute.edu.vn"><img src="https://img.shields.io/badge/HCMUTE-Portal%20API-0071e3?style=flat-square" alt="HCMUTE Portal"></a>
    <a href="https://t.me/hcmute_calendar_bot"><img src="https://img.shields.io/badge/Telegram-Bot%20AI-229ED9?style=flat-square&logo=telegram&logoColor=white" alt="Telegram Bot"></a>
    <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12">
    <img src="https://img.shields.io/badge/Design-Apple%20Minimalism-f5f5f7?style=flat-square&logo=apple&logoColor=black" alt="Apple Style">
    <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License">
  </p>
</div>

---

## 🌟 Giới Thiệu

**HCMUTE Calendar & Realtime Sync** là giải pháp đồng bộ lịch học và lịch thi thông minh dành riêng cho sinh viên trường Đại học Sư phạm Kỹ thuật TP.HCM ([online.hcmute.edu.vn](https://online.hcmute.edu.vn)). 

Không còn nỗi lo quên phòng học, trễ giờ thi hay sót thông báo học phí, hệ thống kết nối trực tiếp với máy chủ đào tạo HCMUTE để đưa toàn bộ lịch học vào **Apple Calendar (iPhone/Mac/Apple Watch)**, **Google Calendar** và kích hoạt **Trợ lý Telegram Bot AI** phục vụ bạn 24/7.

---

## ✨ Tính Năng Nổi Bật

### 1. 📱 Giao diện Web Chuẩn Apple Minimalism
- **Thiết kế tối giản & mượt mà:** Phong cách Dark Mode kính mờ (*Glassmorphism*), tương phản cao, tối ưu hiển thị hoàn hảo trên cả iOS Safari và Android.
- **Đăng nhập linh hoạt:** Đăng nhập trực tiếp bằng tài khoản sinh viên hoặc dán Bearer Token.
- **Bảo mật tuyệt đối:** Không lưu trữ mật khẩu trên máy chủ, hỗ trợ tính năng **Xóa toàn bộ dữ liệu (Wipe My Data)** chỉ với 1 chạm.

### 2. ⚡ Đồng bộ Tức thì vào Apple Calendar & Google Calendar
- **Đăng ký lịch trực tiếp (Live Subscription Feed):** Cung cấp đường dẫn chuẩn `webcal://` và `.ics` (RFC-5545).
- **Tự động cập nhật:** Khi có lịch học bù, đổi phòng hoặc báo nghỉ, lịch trên iPhone/Android tự động đồng bộ theo thời gian thực mà bạn không cần import lại.
- **Phân loại màu sắc thông minh:**
  - 🔴 **Đỏ tươi:** Cảnh báo Lịch thi kết thúc học phần & Hạn chót học phí (ưu tiên cao nhất).
  - 🎨 **Màu sắc theo môn:** Mỗi môn học một gam màu riêng biệt, có sẵn báo thức trước giờ học 20 phút.

### 3. 🤖 Trợ lý Telegram Bot & Trí tuệ Nhân tạo Gemini AI
- **☀️ Bản tin sáng 06:30:** Tự động gửi tin nhắn báo lịch học, số tiết, giờ học và phòng học trong ngày.
- **🔥 Đếm ngược kỳ thi:** Nhắc nhở phòng thi, số báo danh và thời gian làm bài trước ngày thi.
- **💬 Trò chuyện tiếng Việt tự nhiên (Google Gemini):**
  - Hỏi đáp linh hoạt: *"tuần sau tôi học gì"*, *"mai học phòng nào"*, *"khi nào thi môn kế tiếp"*,...
  - Nhận diện chính xác văn hóa sinh viên UTE (các toà nhà A1, A2, B, C, D, E, X, ký túc xá).
- **🗳️ Tự động báo nghỉ đám đông (Crowdsource):** Khi thầy cô báo nghỉ qua Zalo, sinh viên chỉ cần gõ `/nghi` trên Bot. Khi đủ tỷ lệ đồng thuận lớp (mặc định 80%), hệ thống tự động xóa buổi học khỏi Google Calendar của cả lớp.
- **➕ Thêm lịch học bù thần tốc:** Nhắn cú pháp ngắn `/bu Công nghệ kim loại, 02/11, 07:30, A108`, lịch bù lập tức xuất hiện trên điện thoại.

### 4. 📢 Bộ phân tích Hạn chót & Cảnh báo (Consideration Engine)
- Tự động bóc tách thông báo từ cổng trường để lọc các hạn chót quan trọng:
  - 💰 Hạn đóng học phí & công nợ (tránh bị cấm thi/hủy môn).
  - 🎓 Sinh hoạt công dân đầu khóa / giữa khóa / cuối khóa.
  - 📜 Nộp chứng chỉ Tiếng Anh / Tin học chuẩn đầu ra.
  - ✍️ Khảo sát đánh giá môn học & Đăng ký học phần.

### 5. 🛡️ Bảng Điều khiển Quản trị (Admin Portal)
- Truy cập an toàn tại `/admin` bằng mã bảo mật riêng biệt.
- **Nhật ký truy cập thời gian thực:** Tự động cập nhật trực tiếp mỗi 6 giây (`● Trực tiếp`) danh sách MSSV đã tương tác.
- **Phát sóng thông báo khẩn (Broadcast):** Gửi tin nhắn tức thì tới Telegram của toàn bộ sinh viên trong trường.
- **Tối ưu tài nguyên:** Hỗ trợ kích hoạt Webhook HTTPS, đưa CPU về 0.0% khi rảnh và RAM chỉ ~50MB.

---

## 🛠️ Cấu Trúc Dự Án

```
CalendarGetting/
├── web/
│   ├── index.html           # Giao diện Sinh viên (Apple Minimalist Glassmorphism)
│   └── admin.html           # Trung tâm Quản trị Admin (Realtime Live Polling)
├── src/
│   ├── hcmute_api.py        # Client kết nối trực tiếp Portal API đào tạo HCMUTE
│   ├── schedule_parser.py   # Bóc tách TKB, khung giờ tiết học chuẩn HCMUTE (07:30...)
│   ├── conversational_ai.py # Bộ não AI tiếng Việt (Xử lý tuần, môn học & Google Gemini)
│   ├── telegram_bot.py      # Dịch vụ Telegram Bot Daemon & Nhắc lịch tự động
│   ├── notice_analyzer.py   # Bộ lọc hạn chót (Consideration Engine)
│   ├── ics_exporter.py      # Trình tạo file lịch chuẩn Apple/Google Calendar (RFC 5545)
│   └── web_server.py        # REST API Backend xây dựng trên nền tảng Flask
├── config/
│   └── config.json          # Cấu hình hệ thống (không chứa mật khẩu hay API key)
├── tests/
│   └── test_components.py   # Bộ kiểm thử tự động (Unit Test đạt 100% pass)
├── Procfile                 # Lệnh khởi chạy tối ưu cho Render / Gunicorn
├── render.yaml              # Cấu hình hạ tầng triển khai Cloud
├── requirements.txt         # Danh sách thư viện Python tinh gọn
├── run.bat                  # Script chạy nhanh 1 chạm trên Windows
└── main.py                  # Giao diện dòng lệnh CLI (Rich Terminal)
```

---

## 🚀 Hướng Dẫn Chạy Cục Bộ (Localhost)

### 1. Yêu cầu hệ thống
- Python 3.10 trở lên.
- Git.

### 2. Cài đặt thư viện
```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
pip install -r requirements.txt
```

### 3. Khởi động Web Dashboard
- **Cách 1 (Windows):** Nhấp đúp chuột vào file [`run.bat`](run.bat).
- **Cách 2 (Terminal):**
  ```bash
  python src/web_server.py
  ```
Trình duyệt sẽ tự động mở trang web tại `http://127.0.0.1:5000`.

---

## ☁️ Hướng Dẫn Triển Khai Miễn Phí Trên Render (Cloud 24/7)

Hệ thống được thiết kế tối ưu đặc biệt cho gói **Free Tier của Render** (không tốn chi phí, RAM < 60MB, CPU 0% khi rảnh):

1. Đăng ký tài khoản miễn phí tại [render.com](https://render.com).
2. Nhấn **New +** ➔ **Web Service** ➔ Kết nối với repository GitHub của bạn.
3. Thiết lập thông số:
   - **Environment:** `Python`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:**
     ```bash
     gunicorn -w 1 --threads 4 --timeout 120 -b 0.0.0.0:$PORT src.web_server:app
     ```
4. Thêm các biến môi trường tại mục **Environment** (đảm bảo an toàn không lộ lên GitHub):
   - `ADMIN_PASSCODE`: Mật khẩu đăng nhập trang quản trị `/admin`.
   - `TELEGRAM_BOT_TOKEN`: Token lấy từ [@BotFather](https://t.me/BotFather).
   - `TELEGRAM_BOT_USERNAME`: Username của bot (ví dụ: `hcmute_calendar_bot`).
   - `GEMINI_API_KEY`: API Key miễn phí lấy từ [Google AI Studio](https://aistudio.google.com).
5. Nhấn **Create Web Service**. Sau 2-3 phút, trang web của bạn sẽ hoạt động chính thức trên toàn cầu!

---

## 🔒 Cam Kết Bảo Mật & Minh Bạch Dữ Liệu

> *"Show, don't tell — Minh bạch bằng mã nguồn mở"*

- **Không lưu trữ mật khẩu:** Mật khẩu sinh viên được chuyển thẳng tới cổng đào tạo trường qua HTTPS và bị hủy khỏi bộ nhớ RAM ngay sau khi nhận phiên xác thực (`del password`).
- **Kho dữ liệu riêng tư cô lập:** Mọi thông tin nhạy cảm, token, lịch sử truy cập đều được lưu cục bộ trong thư mục `data/` và được [`.gitignore`](.gitignore) bảo vệ nghiêm ngặt, **không bao giờ bị đẩy lên GitHub**.
- **Quyền tự quyết dữ liệu:** Sinh viên có thể chủ động xóa sạch toàn bộ phiên đăng nhập của mình bất cứ lúc nào qua tính năng **Xóa dữ liệu (Wipe Data)** trên giao diện.

---

## 🤝 Đóng Góp Phát Triển (Contributing)

Mọi ý kiến đóng góp, báo lỗi hoặc đề xuất tính năng mới từ các bạn sinh viên và giảng viên HCMUTE đều rất đáng trân trọng:
1. Fork repository này.
2. Tạo nhánh tính năng mới (`git checkout -b feature/tinh-nang-moi`).
3. Commit thay đổi (`git commit -m 'Thêm tính năng...'`).
4. Đẩy lên nhánh của bạn (`git push origin feature/tinh-nang-moi`).
5. Mở một **Pull Request**.

---

## 📄 Bản Quyền (License)

Dự án được phân phối dưới giấy phép **MIT License**. Tự do sử dụng, tùy biến và chia sẻ vì mục đích hỗ trợ cộng đồng sinh viên HCMUTE.

<div align="center">
  <p>Thực hiện với niềm đam mê công nghệ dành cho cộng đồng sinh viên <strong>HCMUTE</strong> 💙</p>
</div>
