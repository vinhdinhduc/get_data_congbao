"""
repair_missing_congbao.py
--------------------------
Script "vá" nhanh: KHÔNG crawl lại toàn bộ 6351 văn bản, chỉ mở lại trang
Thuộc tính cho những bản ghi đang thiếu so_cong_bao / trang_cong_bao trong
DB (do dùng code crawler.py phiên bản CŨ), rồi UPDATE lại đúng 2 cột đó
(+ các field khác nếu lấy được, không đụng tới file_local_paths / file_urls
đã có sẵn).

Dùng khi: bạn đã crawl một phần bằng code cũ (bug so_cong_bao/trang_cong_bao),
giờ đã có code extract_properties() mới, chỉ muốn vá lại phần thiếu thay vì
crawl lại từ đầu.

Chạy:
    python repair_missing_congbao.py
"""

import time
import random
import logging

from playwright.sync_api import sync_playwright

import config
from db import Database
from crawler import (
    CongBaoCrawler, setup_logging, human_delay, parse_vn_date,
)

logger = logging.getLogger("congbao.repair")

SELECT_MISSING_SQL = """
    SELECT parent_unid, so_hieu
    FROM van_ban
    WHERE so_cong_bao IS NULL OR trang_cong_bao IS NULL
"""

UPDATE_SQL = """
    UPDATE van_ban
    SET so_cong_bao      = COALESCE(%(so_cong_bao)s, so_cong_bao),
        trang_cong_bao   = COALESCE(%(trang_cong_bao)s, trang_cong_bao),
        co_quan_ban_hanh = COALESCE(%(co_quan_ban_hanh)s, co_quan_ban_hanh),
        loai_van_ban     = COALESCE(%(loai_van_ban)s, loai_van_ban),
        linh_vuc         = COALESCE(%(linh_vuc)s, linh_vuc),
        nguoi_ky         = COALESCE(%(nguoi_ky)s, nguoi_ky)
    WHERE parent_unid = %(parent_unid)s
"""


def main():
    setup_logging()
    db = Database().connect()

    cur = db.conn.cursor(dictionary=True)
    cur.execute(SELECT_MISSING_SQL)
    rows = cur.fetchall()
    cur.close()

    logger.info("Số bản ghi cần vá lại so_cong_bao/trang_cong_bao: %d", len(rows))
    if not rows:
        logger.info("Không có gì để vá. Thoát.")
        db.close()
        return

    # Tái sử dụng logic extract_properties() đã sửa trong crawler.py,
    # không tái sử dụng toàn bộ CongBaoCrawler (vì nó tự mở connection DB
    # riêng) -- ta chỉ cần method + browser page.
    crawler_helper = CongBaoCrawler.__new__(CongBaoCrawler)  # không gọi __init__

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=config.USER_AGENT, viewport=config.VIEWPORT
        )
        page = context.new_page()

        ok, err = 0, 0
        for i, row in enumerate(rows, 1):
            parent_unid = row["parent_unid"]
            so_hieu = row["so_hieu"]
            logger.info("[%d/%d] Vá: %s (%s)", i, len(rows), so_hieu, parent_unid)
            try:
                time.sleep(random.uniform(config.DELAY_MIN_SEC, config.DELAY_MAX_SEC))
                props = crawler_helper.extract_properties(page, parent_unid)
                update_record = {
                    "parent_unid": parent_unid,
                    "so_cong_bao": props.get("so_cong_bao"),
                    "trang_cong_bao": props.get("trang_cong_bao"),
                    "co_quan_ban_hanh": props.get("co_quan_ban_hanh"),
                    "loai_van_ban": props.get("loai_van_ban"),
                    "linh_vuc": props.get("linh_vuc"),
                    "nguoi_ky": props.get("nguoi_ky"),
                }
                ucur = db.conn.cursor()
                ucur.execute(UPDATE_SQL, update_record)
                db.conn.commit()
                ucur.close()

                if props.get("so_cong_bao") and props.get("trang_cong_bao"):
                    ok += 1
                else:
                    err += 1
                    logger.warning(
                        "%s vẫn KHÔNG lấy được so_cong_bao/trang_cong_bao sau vá "
                        "-- cần inspect thủ công (xem verify.py phần debug).",
                        parent_unid,
                    )
            except Exception:
                logger.exception("Lỗi khi vá %s (%s)", so_hieu, parent_unid)
                err += 1

        logger.info("Hoàn tất vá. Thành công: %d, Vẫn thiếu/lỗi: %d", ok, err)
        context.close()
        browser.close()

    db.close()


if __name__ == "__main__":
    main()