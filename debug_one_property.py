"""
debug_one_property.py
----------------------
Debug NHANH đúng 1 văn bản (theo parent_unid) đang bị thiếu
so_cong_bao/trang_cong_bao, để xác định:
  (a) văn bản đó THỰC SỰ chưa có Công báo/Trang trên site gốc (không phải
      lỗi code), hay
  (b) code vẫn chưa bắt đúng cấu trúc HTML thật.

Chạy:
    python debug_one_property.py 00322A58A69DB90C47258DFE000A2725

Sẽ in ra:
  1. Dict thô từ PROPERTIES_EXTRACT_JS (label tiếng Việt -> giá trị)
  2. Toàn bộ text hiển thị của trang (để mắt thường soát xem có chữ
     "Công báo" / "Trang" ở đâu không, dù không nằm trong bảng th/td)
  3. outerHTML của mọi <tr> có chứa chữ "Công báo" (nếu có)
"""

import sys

from playwright.sync_api import sync_playwright

import config
from crawler import PROPERTIES_EXTRACT_JS


def main():
    if len(sys.argv) != 2:
        print("Dùng: python debug_one_property.py <parent_unid>")
        sys.exit(1)
    parent_unid = sys.argv[1]
    url = config.PROPERTIES_URL_TEMPLATE.format(unid=parent_unid)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=config.USER_AGENT, viewport=config.VIEWPORT
        )
        page = context.new_page()

        print(f"--- Mở: {url} ---")
        page.goto(url, wait_until="networkidle", timeout=config.NAV_TIMEOUT_MS)

        raw = page.evaluate(PROPERTIES_EXTRACT_JS)
        print("\n=== 1. Dict thô (PROPERTIES_EXTRACT_JS) ===")
        for k, v in raw.items():
            print(f"  {k!r} -> {v!r}")

        print("\n=== 2. Toàn bộ text hiển thị của trang ===")
        body_text = page.inner_text("body")
        print(body_text)

        print("\n=== 3. outerHTML các <tr> chứa chữ 'Công báo' ===")
        rows = page.eval_on_selector_all(
            "tr",
            "els => els.filter(tr => tr.textContent.includes('Công báo'))"
            ".map(tr => tr.outerHTML)"
        )
        if not rows:
            print("  (Không có <tr> nào chứa 'Công báo' trên trang này -- "
                  "nghĩa là field này KHÔNG xuất hiện dưới dạng bảng ở đây. "
                  "Xem lại mục 2 ở trên bằng mắt: nếu chữ 'Công báo' cũng "
                  "KHÔNG xuất hiện trong text toàn trang, thì văn bản này "
                  "thực sự CHƯA có số Công báo trên site gốc -- không phải "
                  "lỗi code, không cần vá.)")
        for i, html in enumerate(rows):
            print(f"\n--- tr[{i}] ---\n{html}")

        context.close()
        browser.close()


if __name__ == "__main__":
    main()