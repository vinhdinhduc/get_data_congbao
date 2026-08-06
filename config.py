"""
config.py
---------
Tập trung URL, selector, tham số crawl. Toàn bộ giá trị dưới đây đã được
XÁC MINH qua HTML thật (view-source) do người dùng cung cấp — không còn
là suy đoán như bản đầu tiên.

Cấu trúc thật của site (Domino):
  Trang danh sách (VanBanQPPL2 hoặc VanBan)
    -> bảng phẳng, đã phân trang (đổi URL bằng Start=&page=, KHÔNG cần click)
    -> mỗi dòng có link 'a.kyhieu' trỏ tới NoiDung?openForm&ParentUNID=<UNID>
  Trang ThuộcTính (ThuocTinh?openForm&ParentUNID=<UNID>)
    -> bảng <th>Nhãn</th><td>Giá trị</td> chứa đủ field cần thiết
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# URL gốc
# ============================================================
BASE_URL = "https://congbao.sonla.gov.vn"

# Nguồn danh sách văn bản — CHỌN 1:
#   "VanBanQPPL2" : văn bản quy phạm pháp luật (6351 văn bản tính đến 08/2026)
#   "VanBan"      : "Sơ đồ văn bản" — cùng cấu trúc HTML, có thể bao quát hơn
# Đổi giá trị này để đổi nguồn crawl mà không sửa logic.
LISTING_VIEW_NAME = "VanBanQPPL2"
LISTING_URL = f"{BASE_URL}/congbao.nsf/{LISTING_VIEW_NAME}"

# Số dòng mỗi trang — xác nhận từ JS phân trang của site
# (jumpToPage(): pageSize = parseInt(...) || 30)
PAGE_SIZE = 30

# ============================================================
# Selector — ĐÃ XÁC MINH qua HTML thật
# ============================================================

# Tổng số văn bản hiển thị ở đầu trang danh sách
TOTAL_COUNT_SELECTOR = "#total"

# Mỗi dòng văn bản trong bảng danh sách nằm trong 1 <tr> có ô <td class='doc-item'>
# JS trích xuất toàn bộ field của 1 trang danh sách — xem crawler.py: extract_listing_rows()

# Trang chi tiết dùng để tải file gốc + đối chiếu (không cần crawl riêng vì
# ThuocTinh đã đủ field text; file đính kèm lấy trực tiếp từ trang danh sách)
DETAIL_URL_TEMPLATE = BASE_URL + "/congbao.nsf/NoiDung?openForm&ParentUNID={unid}"

# Trang Thuộc tính — nguồn chính cho các field còn thiếu trong bảng danh sách
PROPERTIES_URL_TEMPLATE = BASE_URL + "/congbao.nsf/ThuocTinh?openForm&ParentUNID={unid}"

# Ánh xạ nhãn tiếng Việt (key trong bảng Thuộc tính) -> tên cột DB
# JS trích xuất trả về dict {nhãn: giá trị}; đối chiếu bằng dict này.
PROPERTIES_FIELD_MAP = {
    "Số, ký hiệu": "so_hieu",
    "Ngày ban hành": "ngay_ban_hanh",
    "Cơ quan ban hành": "co_quan_ban_hanh",
    "Người ký": "nguoi_ky",
    "Loại văn bản": "loai_van_ban",
    "Lĩnh vực": "linh_vuc",
    "Trích yếu": "trich_yeu",
    "Ngày hiệu lực": "ngay_hieu_luc",
    "Ngày hết hiệu lực": "ngay_het_hieu_luc",
    "Tình trạng hiệu lực": "tinh_trang",
    "Công báo": "so_cong_bao",
    "Trang": "trang_cong_bao",
}

# ============================================================
# Tham số hành vi crawl ("giả lập người dùng thật")
# ============================================================
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1366, "height": 768}

DELAY_MIN_SEC = 1.5
DELAY_MAX_SEC = 4.0

MAX_CONCURRENT_PAGES = 1  # KHÔNG tăng — ràng buộc bắt buộc từ yêu cầu gốc

NAV_TIMEOUT_MS = 30_000
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SEC = 3  # 3s, 6s, 12s...

# Có tải file PDF/DOC gốc về máy không (tắt = chỉ lưu link gốc vào DB)
DOWNLOAD_ATTACHMENTS = True

# ============================================================
# Thư mục lưu trữ
# ============================================================
SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "samples")
DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")

# ============================================================
# Database (đọc từ biến môi trường / .env — KHÔNG hard-code)
# ============================================================
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "congbao_sonla"),
}