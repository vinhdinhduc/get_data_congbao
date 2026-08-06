"""
scripts/verify.py
------------------
Chạy thử NHANH: lấy trang danh sách đầu tiên (30 văn bản) + trích xuất
Thuộc tính của văn bản đầu tiên, in ra kết quả để bạn đối chiếu bằng mắt
trước khi chạy full crawl (có thể mất nhiều giờ với 6351 văn bản).

Không ghi vào DB — chỉ in ra console.

Chạy:
    python scripts/verify.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402
import config  # noqa: E402
from crawler import (  # noqa: E402
    LISTING_ROW_EXTRACT_JS, PROPERTIES_EXTRACT_JS, TOTAL_COUNT_JS,
    parse_vn_date,
)


def verify():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=config.USER_AGENT, viewport=config.VIEWPORT
        )
        page = context.new_page()

        url = f"{config.LISTING_URL}?OpenView&Start=1&page=1"
        print(f"--- Mở trang danh sách: {url} ---")
        page.goto(url, wait_until="networkidle")

        total = page.evaluate(TOTAL_COUNT_JS)
        print(f"Tổng số văn bản (#total): {total}")

        page.wait_for_selector("a.kyhieu")
        rows = page.evaluate(LISTING_ROW_EXTRACT_JS)
        print(f"\nSố văn bản trích xuất được ở trang 1: {len(rows)}")
        print("\n3 văn bản đầu tiên:")
        for r in rows[:3]:
            print(json.dumps(r, ensure_ascii=False, indent=2))

        if rows:
            first = rows[0]
            props_url = config.PROPERTIES_URL_TEMPLATE.format(unid=first["parentUNID"])
            print(f"\n--- Mở trang Thuộc tính: {props_url} ---")
            page.goto(props_url, wait_until="networkidle")
            page.wait_for_selector("table.tbl-attributes")
            props_raw = page.evaluate(PROPERTIES_EXTRACT_JS)
            print("\nDữ liệu Thuộc tính (nhãn gốc tiếng Việt):")
            print(json.dumps(props_raw, ensure_ascii=False, indent=2))

            print("\nSau khi map qua PROPERTIES_FIELD_MAP:")
            for vn_label, db_col in config.PROPERTIES_FIELD_MAP.items():
                print(f"  {db_col:20s} = {props_raw.get(vn_label)!r}")

            print("\nParse ngày thử:")
            print(f"  ngay_ban_hanh -> {parse_vn_date(props_raw.get('Ngày ban hành'))}")
            print(f"  ngay_hieu_luc -> {parse_vn_date(props_raw.get('Ngày hiệu lực'))}")

            # --- DEBUG riêng cho Công báo / Trang ---
            # Nếu 2 giá trị này vẫn None/rỗng sau khi map, in ra outerHTML
            # của TẤT CẢ <tr> có chứa chữ "Công báo" để tự soi cấu trúc thật
            # (có thể site đặt field này trong bảng khác / thẻ khác th-td).
            if not props_raw.get("Công báo") or not props_raw.get("Trang"):
                print("\n!!! Công báo / Trang chưa lấy được qua bảng chuẩn. "
                      "Dump outerHTML các <tr> chứa 'Công báo' để đối chiếu:")
                debug_rows = page.eval_on_selector_all(
                    "tr",
                    """els => els
                        .filter(tr => tr.textContent.includes('Công báo'))
                        .map(tr => tr.outerHTML)"""
                )
                for i, html in enumerate(debug_rows):
                    print(f"\n--- tr[{i}] chứa 'Công báo' ---\n{html}\n")
                if not debug_rows:
                    print("  (Không tìm thấy <tr> nào chứa chữ 'Công báo' trên trang này -- "
                          "field này có thể nằm ngoài <table>, ví dụ trong <div>/<span>. "
                          "Hãy tự bấm F12 và tìm 'Công báo' trong Elements panel.)")

        print("\nNhấn Enter để đóng trình duyệt...")
        input()
        context.close()
        browser.close()


if __name__ == "__main__":
    verify()