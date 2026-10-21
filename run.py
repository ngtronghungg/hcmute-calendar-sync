"""
UTECal - HCMUTE Calendar & Sync - Local Runner
Chạy ứng dụng cục bộ (Localhost) an toàn, riêng tư, bảo mật 100%.
"""

import sys
import os

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.web_server import run_app

if __name__ == "__main__":
    print("\n" + "=" * 56)
    print("🎓 UTECal — HCMUTE CALENDAR & SYNC (CHẠY CỤC BỘ)")
    print("🔒 Môi trường máy tính riêng: Không qua trung gian, bảo mật 100%")
    print("=" * 56 + "\n")
    run_app(port=5000, open_browser=True)
