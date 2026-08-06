"""
crawler.py
----------
Crawler chính — phiên bản 2, dựa trên cấu trúc thật đã xác minh qua
HTML người dùng cung cấp (không còn suy đoán selector).

Luồng xử lý:
  1. Mở trang danh sách (config.LISTING_URL) lần đầu để lấy tổng số văn bản.
  2. Lặp qua từng trang bằng cách ĐỔI URL (Start=&page=), không cần click —
     Domino render toàn bộ danh sách qua query string.
  3. Với mỗi trang, trích xuất trực tiếp từ DOM: số hiệu, ParentUNID, trích
     yếu, ngày ban hành, ngày hiệu lực, tình trạng, link file đính kèm.
  4. Với mỗi văn bản, mở thêm trang Thuộc tính (ThuocTinh?openForm&ParentUNID=)
     để lấy cơ quan ban hành, người ký, loại văn bản, lĩnh vực... (bảng
     <th>/<td> sạch, không cần regex dò text).
  5. Tải file đính kèm (nếu bật DOWNLOAD_ATTACHMENTS).
  6. Upsert vào MySQL.

Nguyên tắc bắt buộc (theo yêu cầu gốc) vẫn được giữ nguyên:
- 1 tab duy nhất, không crawl song song.
- Delay ngẫu nhiên giữa các thao tác.
- Chờ theo networkidle/selector, không dùng sleep cố định để "đợi load".
- Retry có backoff khi timeout/lỗi mạng.
"""

import os
import re
import math
import random
import time
import logging
from datetime import datetime
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

import config
from db import Database

logger = logging.getLogger("congbao.crawler")


def setup_logging():
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    log_file = os.path.join(
        config.LOGS_DIR, f"crawl_{datetime.now():%Y%m%d_%H%M%S}.log"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"),
                  logging.StreamHandler()],
    )


def human_delay():
    time.sleep(random.uniform(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC))


def with_retry(fn, *args, what="thao tác", **kwargs):
    last_err = None
    for attempt in range(1, config.RETRY_MAX_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = config.RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
            logger.warning(
                "Lỗi khi %s (lần %d/%d): %s. Thử lại sau %ds...",
                what, attempt, config.RETRY_MAX_ATTEMPTS, e, wait,
            )
            time.sleep(wait)
    logger.error("Bỏ cuộc sau %d lần thử: %s", config.RETRY_MAX_ATTEMPTS, what)
    raise last_err


def parse_vn_date(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


# ============================================================
# JS trích xuất DOM — chạy trong trình duyệt qua page.evaluate()
# ============================================================

LISTING_ROW_EXTRACT_JS = """
() => {
    const rows = document.querySelectorAll('div.divData table tr');
    const out = [];
    for (const tr of rows) {
        const linkEl = tr.querySelector('a.kyhieu');
        if (!linkEl) continue;  // bỏ qua hàng tiêu đề
        const href = linkEl.getAttribute('href') || '';
        const m = href.match(/ParentUNID=([0-9A-Fa-f]+)/);
        const parentUNID = m ? m[1] : null;
        if (!parentUNID) continue;

        const soHieu = linkEl.textContent.trim();
        const excerptEl = tr.querySelector('span.excerpt');
        const trichYeu = excerptEl ? excerptEl.textContent.trim() : '';
        const wEffectEl = tr.querySelector('span.w-effect');
        const ngayBanHanh = wEffectEl ? wEffectEl.textContent.trim() : '';
        const effectingEl = tr.querySelector('span.effecting');
        const ngayHieuLuc = effectingEl ? effectingEl.textContent.trim() : '';

        const tds = tr.querySelectorAll('td.datacell');
        let tinhTrang = '';
        if (tds.length >= 5) {
            const span = tds[4].querySelector('span');
            tinhTrang = span ? span.textContent.trim() : tds[4].textContent.trim();
        }

        const fileLinks = [];
        if (tds.length >= 6) {
            tds[tds.length - 1].querySelectorAll("a[href*='$file/']").forEach(a => {
                fileLinks.push(a.getAttribute('href'));
            });
        }

        out.push({
            soHieu, parentUNID, trichYeu, ngayBanHanh, ngayHieuLuc,
            tinhTrang, fileLinks
        });
    }
    return out;
}
"""

PROPERTIES_EXTRACT_JS = """
() => {
    const data = {};
    const table = document.querySelector('table.tbl-attributes');
    if (!table) return data;
    table.querySelectorAll('tr').forEach(tr => {
        const ths = tr.querySelectorAll('th');
        const tds = tr.querySelectorAll('td');
        for (let i = 0; i < ths.length; i++) {
            const label = ths[i].textContent.trim();
            const value = tds[i] ? tds[i].textContent.trim() : '';
            data[label] = value;
        }
    });
    return data;
}
"""

TOTAL_COUNT_JS = """
() => {
    const el = document.querySelector('#total');
    return el ? parseInt(el.textContent.trim(), 10) : null;
}
"""


class CongBaoCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.db = Database().connect()
        os.makedirs(config.DOWNLOADS_DIR, exist_ok=True)

    def _listing_page_url(self, page_number: int) -> str:
        start = config.PAGE_SIZE * (page_number - 1) + 1
        return (f"{config.LISTING_URL}?OpenView"
                f"&Start={start}&page={page_number}")

    def _goto(self, page, url: str, what: str):
        def _do():
            page.goto(url, wait_until="networkidle", timeout=config.NAV_TIMEOUT_MS)
        with_retry(_do, what=what)

    def get_total_count(self, page) -> int:
        self._goto(page, self._listing_page_url(1), what="mở trang danh sách (lấy tổng số)")
        page.wait_for_selector(config.TOTAL_COUNT_SELECTOR, timeout=config.NAV_TIMEOUT_MS)
        total = page.evaluate(TOTAL_COUNT_JS)
        if not total:
            raise RuntimeError(
                "Không đọc được tổng số văn bản (#total). Kiểm tra lại "
                "config.TOTAL_COUNT_SELECTOR hoặc site đã đổi cấu trúc."
            )
        return total

    def extract_listing_page(self, page, page_number: int) -> list[dict]:
        url = self._listing_page_url(page_number)
        self._goto(page, url, what=f"mở trang danh sách #{page_number}")
        try:
            page.wait_for_selector("a.kyhieu", timeout=config.NAV_TIMEOUT_MS)
        except PWTimeoutError:
            logger.warning("Trang danh sách #%d không có văn bản nào (hết dữ liệu?)", page_number)
            return []
        rows = page.evaluate(LISTING_ROW_EXTRACT_JS)
        return rows

    def extract_properties(self, page, parent_unid: str) -> dict:
        url = config.PROPERTIES_URL_TEMPLATE.format(unid=parent_unid)

        def _goto_props():
            page.goto(url, wait_until="networkidle", timeout=config.NAV_TIMEOUT_MS)

        with_retry(_goto_props, what=f"mở trang Thuộc tính {parent_unid}")
        try:
            page.wait_for_selector("table.tbl-attributes", timeout=config.NAV_TIMEOUT_MS)
        except PWTimeoutError:
            logger.warning("Không thấy bảng thuộc tính cho %s", parent_unid)
            return {}
        raw = page.evaluate(PROPERTIES_EXTRACT_JS)

        mapped = {}
        for vn_label, db_col in config.PROPERTIES_FIELD_MAP.items():
            mapped[db_col] = raw.get(vn_label, "") or None
        return mapped

    def download_attachments(self, page, parent_unid: str, file_hrefs: list[str],
                              so_hieu: str) -> list[str]:
        local_paths = []
        safe_base = re.sub(r"[^\w\-.]", "_", so_hieu)
        for i, href in enumerate(file_hrefs):
            full_url = urljoin(f"{config.BASE_URL}/congbao.nsf/", href)
            ext = os.path.splitext(href.split("?")[0])[1] or ".bin"
            suffix = f"_{i}" if i > 0 else ""
            dest_path = os.path.join(config.DOWNLOADS_DIR, f"{safe_base}{suffix}{ext}")

            if os.path.exists(dest_path):
                local_paths.append(dest_path)
                continue

            def _download():
                with page.expect_download(timeout=config.NAV_TIMEOUT_MS) as dl_info:
                    # Khi goto() trỏ tới 1 file tải về, trình duyệt hủy
                    # điều hướng ngay để bắt đầu download -> Playwright ném
                    # net::ERR_ABORTED dù việc tải vẫn diễn ra bình thường.
                    # Đây là hành vi mong đợi, không phải lỗi thật.
                    try:
                        page.goto(full_url, timeout=config.NAV_TIMEOUT_MS)
                    except PWTimeoutError:
                        raise
                    except Exception as nav_err:
                        if "ERR_ABORTED" not in str(nav_err):
                            raise
                download = dl_info.value
                download.save_as(dest_path)

            try:
                with_retry(_download, what=f"tải file {full_url}")
                local_paths.append(dest_path)
                logger.info("Đã tải file: %s", dest_path)
            except Exception:
                logger.error("Không tải được file: %s", full_url)
        return local_paths

    def run(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent=config.USER_AGENT,
                viewport=config.VIEWPORT,
                accept_downloads=True,
            )
            page = context.new_page()

            total = self.get_total_count(page)
            total_pages = math.ceil(total / config.PAGE_SIZE)
            logger.info("Tổng số văn bản: %d (%d trang, %d/trang)",
                        total, total_pages, config.PAGE_SIZE)

            ok_count, err_count = 0, 0

            for page_number in range(1, total_pages + 1):
                human_delay()
                rows = self.extract_listing_page(page, page_number)
                logger.info("Trang %d/%d: %d văn bản", page_number, total_pages, len(rows))

                for row in rows:
                    parent_unid = row["parentUNID"]
                    so_hieu = row["soHieu"]
                    logger.info("Đang xử lý: %s (%s)", so_hieu, parent_unid)
                    try:
                        human_delay()
                        props = self.extract_properties(page, parent_unid)

                        local_paths = []
                        if config.DOWNLOAD_ATTACHMENTS and row["fileLinks"]:
                            human_delay()
                            local_paths = self.download_attachments(
                                page, parent_unid, row["fileLinks"], so_hieu
                            )

                        record = {
                            "so_hieu": props.get("so_hieu") or so_hieu,
                            "parent_unid": parent_unid,
                            "ngay_ban_hanh": parse_vn_date(
                                props.get("ngay_ban_hanh") or row["ngayBanHanh"]
                            ),
                            "ngay_hieu_luc": parse_vn_date(
                                props.get("ngay_hieu_luc") or row["ngayHieuLuc"]
                            ),
                            "ngay_het_hieu_luc": parse_vn_date(props.get("ngay_het_hieu_luc")),
                            "co_quan_ban_hanh": props.get("co_quan_ban_hanh"),
                            "loai_van_ban": props.get("loai_van_ban"),
                            "linh_vuc": props.get("linh_vuc"),
                            "nguoi_ky": props.get("nguoi_ky"),
                            "trich_yeu": props.get("trich_yeu") or row["trichYeu"],
                            "tinh_trang": props.get("tinh_trang") or row["tinhTrang"],
                            "so_cong_bao": props.get("so_cong_bao"),
                            "trang_cong_bao": props.get("trang_cong_bao"),
                            "file_urls": ";".join(
                                urljoin(f"{config.BASE_URL}/congbao.nsf/", h)
                                for h in row["fileLinks"]
                            ) or None,
                            "file_local_paths": ";".join(local_paths) or None,
                            "source_url": config.DETAIL_URL_TEMPLATE.format(unid=parent_unid),
                            "crawled_at": datetime.now(),
                        }
                        self.db.upsert_van_ban(record)
                        ok_count += 1
                    except Exception:
                        logger.exception("Lỗi xử lý văn bản %s (%s)", so_hieu, parent_unid)
                        err_count += 1

            logger.info("Hoàn tất. Thành công: %d, Lỗi: %d", ok_count, err_count)

            context.close()
            browser.close()
        self.db.close()