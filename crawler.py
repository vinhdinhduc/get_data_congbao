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
    function norm(s) {
        return (s || '').replace(/\\s+/g, ' ').trim().replace(/:$/, '');
    }
    const data = {};
    // QUÉT TOÀN TRANG (không chỉ trong table.tbl-attributes) vì 1 số field
    // như "Công báo" / "Trang" có thể nằm ở bảng con/khác selector.
    // Dùng ':scope > th' và ':scope > td' để chỉ lấy CON TRỰC TIẾP của tr,
    // tránh trường hợp có bảng lồng bên trong 1 ô làm lệch index th<->td.
    document.querySelectorAll('tr').forEach(tr => {
        const ths = tr.querySelectorAll(':scope > th');
        const tds = tr.querySelectorAll(':scope > td');
        for (let i = 0; i < ths.length; i++) {
            const label = norm(ths[i].textContent);
            if (!label) continue;
            const value = tds[i] ? norm(tds[i].textContent) : '';
            // Không ghi đè nếu đã có giá trị (ưu tiên lần gặp đầu tiên,
            // phòng trường hợp nhãn trùng tên xuất hiện ở chỗ khác trang)
            if (!(label in data) || !data[label]) {
                data[label] = value;
            }
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
        logger.debug("Raw properties (%s): %s", parent_unid, raw)

        mapped = {}
        for vn_label, db_col in config.PROPERTIES_FIELD_MAP.items():
            mapped[db_col] = raw.get(vn_label, "") or None

        # Fallback: nếu vẫn thiếu Công báo/Trang (site có thể render 2 field
        # này ngoài cấu trúc th/td chuẩn), dò trực tiếp trong text toàn trang.
        if not mapped.get("so_cong_bao") or not mapped.get("trang_cong_bao"):
            body_text = page.inner_text("body")
            if not mapped.get("so_cong_bao"):
                m = re.search(
                    r"Công báo[^\S\r\n]*\n?[^\S\r\n]*(Số\s*\d+[^\n]*?Ngày\s*[\d/]+)",
                    body_text,
                )
                if m:
                    mapped["so_cong_bao"] = m.group(1).strip()
                    logger.info("Đã lấy Công báo qua fallback regex cho %s", parent_unid)
            if not mapped.get("trang_cong_bao"):
                m = re.search(r"\bTrang\b[^\S\r\n]*\n?[^\S\r\n]*(\d+)", body_text)
                if m:
                    mapped["trang_cong_bao"] = m.group(1).strip()
                    logger.info("Đã lấy Trang qua fallback regex cho %s", parent_unid)
            if not mapped.get("so_cong_bao") or not mapped.get("trang_cong_bao"):
                logger.warning(
                    "Vẫn KHÔNG lấy được so_cong_bao/trang_cong_bao cho %s dù đã "
                    "fallback. Cần inspect lại HTML thật của trang này "
                    "(xem hướng dẫn debug trong verify.py).",
                    parent_unid,
                )
        return mapped

    def download_attachments(self, page, parent_unid: str, file_hrefs: list[str],
                              so_hieu: str, doc_date=None) -> list[dict]:
        """Tải file đính kèm, lưu vào downloads/<năm>/<tháng>/<file>.

        Trả về list dict {file_index, file_url, file_local_path,
        downloaded_at} - LUÔN có 1 phần tử cho mỗi href trong file_hrefs,
        kể cả khi tải lỗi (file_local_path=None) để không lệch index giữa
        url và local_path như cách lưu chuỗi ";" cũ.

        doc_date: đối tượng date() dùng để xác định năm/tháng lưu file
        (ưu tiên ngày ban hành). Nếu không có (None), lưu vào thư mục
        'khong_xac_dinh' để không làm rơi mất file, đồng thời log cảnh báo.
        """
        if doc_date is not None:
            year_dir = f"{doc_date.year:04d}"
            month_dir = f"{doc_date.month:02d}"
        else:
            year_dir = "khong_xac_dinh"
            month_dir = "khong_xac_dinh"
            logger.warning(
                "Không xác định được ngày ban hành cho %s (%s) -> lưu file "
                "vào downloads/khong_xac_dinh/", so_hieu, parent_unid,
            )
        out_dir = os.path.join(config.DOWNLOADS_DIR, year_dir, month_dir)
        os.makedirs(out_dir, exist_ok=True)

        results = []
        safe_base = re.sub(r"[^\w\-.]", "_", so_hieu)
        for i, href in enumerate(file_hrefs):
            full_url = urljoin(f"{config.BASE_URL}/congbao.nsf/", href)
            ext = os.path.splitext(href.split("?")[0])[1] or ".bin"
            suffix = f"_{i}" if i > 0 else ""
            dest_path = os.path.join(out_dir, f"{safe_base}{suffix}{ext}")

            if os.path.exists(dest_path):
                results.append({
                    "file_index": i, "file_url": full_url,
                    "file_local_path": dest_path, "downloaded_at": datetime.now(),
                })
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
                logger.info("Đã tải file: %s", dest_path)
                results.append({
                    "file_index": i, "file_url": full_url,
                    "file_local_path": dest_path, "downloaded_at": datetime.now(),
                })
            except Exception:
                logger.error("Không tải được file: %s", full_url)
                # Vẫn ghi nhận URL (để biết văn bản này có file, dù chưa
                # tải được), local_path=None -- có thể chạy lại sau để vá.
                results.append({
                    "file_index": i, "file_url": full_url,
                    "file_local_path": None, "downloaded_at": None,
                })
        return results

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

                        # Tính ngày TRƯỚC khi tải file, để dùng làm thư mục năm/tháng
                        ngay_ban_hanh = parse_vn_date(
                            props.get("ngay_ban_hanh") or row["ngayBanHanh"]
                        )
                        ngay_hieu_luc = parse_vn_date(
                            props.get("ngay_hieu_luc") or row["ngayHieuLuc"]
                        )
                        # Ưu tiên ngày ban hành để phân thư mục; nếu thiếu thì
                        # dùng tạm ngày hiệu lực, cuối cùng mới chịu là None.
                        doc_date = ngay_ban_hanh or ngay_hieu_luc

                        file_records = []
                        if row["fileLinks"]:
                            if config.DOWNLOAD_ATTACHMENTS:
                                human_delay()
                                file_records = self.download_attachments(
                                    page, parent_unid, row["fileLinks"], so_hieu,
                                    doc_date=doc_date,
                                )
                            else:
                                # Không tải file nhưng vẫn ghi nhận có bao
                                # nhiêu file / URL gốc vào van_ban_file.
                                file_records = [
                                    {
                                        "file_index": i,
                                        "file_url": urljoin(
                                            f"{config.BASE_URL}/congbao.nsf/", h
                                        ),
                                        "file_local_path": None,
                                        "downloaded_at": None,
                                    }
                                    for i, h in enumerate(row["fileLinks"])
                                ]

                        record = {
                            "so_hieu": props.get("so_hieu") or so_hieu,
                            "parent_unid": parent_unid,
                            "ngay_ban_hanh": ngay_ban_hanh,
                            "ngay_hieu_luc": ngay_hieu_luc,
                            "ngay_het_hieu_luc": parse_vn_date(props.get("ngay_het_hieu_luc")),
                            "co_quan_ban_hanh": props.get("co_quan_ban_hanh"),
                            "loai_van_ban": props.get("loai_van_ban"),
                            "linh_vuc": props.get("linh_vuc"),
                            "nguoi_ky": props.get("nguoi_ky"),
                            "trich_yeu": props.get("trich_yeu") or row["trichYeu"],
                            "tinh_trang": props.get("tinh_trang") or row["tinhTrang"],
                            "so_cong_bao": props.get("so_cong_bao"),
                            "trang_cong_bao": props.get("trang_cong_bao"),
                            "source_url": config.DETAIL_URL_TEMPLATE.format(unid=parent_unid),
                            "crawled_at": datetime.now(),
                        }
                        self.db.upsert_van_ban(record)
                        self.db.upsert_van_ban_files(parent_unid, file_records)
                        ok_count += 1
                    except Exception:
                        logger.exception("Lỗi xử lý văn bản %s (%s)", so_hieu, parent_unid)
                        err_count += 1

            logger.info("Hoàn tất. Thành công: %d, Lỗi: %d", ok_count, err_count)

            context.close()
            browser.close()
        self.db.close()