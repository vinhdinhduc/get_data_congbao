"""
scripts/explore2.py
--------------------
Bản khảo sát nâng cao — dùng sau khi explore.py cho thấy trang
DanhMucVB không chứa link thẳng tới văn bản (cấu trúc thật là
Năm -> Tháng -> Ngày công báo -> Văn bản -> Chi tiết).

Script này:
1. Mở trang danh mục.
2. In ra TOÀN BỘ thẻ <a> (href + text), không lọc, để thấy rõ pattern.
3. Tìm link có text dạng "ngày ... tháng ... năm ..." (1 số công báo cụ thể),
   click vào link ĐẦU TIÊN.
4. Lưu HTML trang đó vào samples/, in ra toàn bộ <a> của trang này.
5. Nếu tìm thấy link dạng chứa 'str/' hoặc 'OpenDocument', click tiếp vào
   link đầu tiên đó để lấy mẫu trang chi tiết cuối cùng, lưu HTML.

Chạy:
    python scripts/explore2.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402
import config  # noqa: E402


def dump_links(page, label: str):
    links = page.eval_on_selector_all(
        "a",
        "els => els.map(e => ({href: e.getAttribute('href'), text: e.innerText.trim()}))",
    )
    print(f"\n--- Toàn bộ <a> tại '{label}' (tổng {len(links)}) ---")
    for l in links:
        if l["href"]:
            print(f"  text={l['text']!r:50s} href={l['href']}")
    return links


def explore():
    os.makedirs(config.SAMPLES_DIR, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=config.USER_AGENT, viewport=config.VIEWPORT
        )
        page = context.new_page()

        print(f"--- Mở trang danh mục: {config.CATEGORY_URL} ---")
        page.goto(config.CATEGORY_URL, wait_until="networkidle")

        links_level1 = dump_links(page, "Danh mục (cấp 1)")

        with open(os.path.join(config.SAMPLES_DIR, "cap1_danh_muc.html"),
                   "w", encoding="utf-8") as fh:
            fh.write(page.content())

        # Tìm link "ngày ... tháng ... năm ..."
        date_pattern = re.compile(r"ngày\s+\d+\s+tháng\s+\d+\s+năm\s+\d+", re.IGNORECASE)
        date_links = [l for l in links_level1 if l["text"] and date_pattern.search(l["text"])]

        if not date_links:
            print("\n!!! Không tìm thấy link dạng 'ngày ... tháng ... năm ...'. "
                  "Xem lại danh sách link ở trên để tự xác định selector đúng.")
            input("\nNhấn Enter để đóng trình duyệt...")
            context.close()
            browser.close()
            return

        target_text = date_links[0]["text"]
        print(f"\n--- Click vào link cấp 1 đầu tiên: {target_text!r} ---")
        page.click(f"text={target_text}", timeout=config.NAV_TIMEOUT_MS)
        page.wait_for_load_state("networkidle")

        with open(os.path.join(config.SAMPLES_DIR, "cap2_so_cong_bao.html"),
                   "w", encoding="utf-8") as fh:
            fh.write(page.content())
        print(f"URL hiện tại (cấp 2): {page.url}")

        links_level2 = dump_links(page, "Số công báo (cấp 2)")

        # Tìm link nghi là văn bản chi tiết
        detail_like = [
            l for l in links_level2
            if l["href"] and ("str/" in l["href"] or "OpenDocument" in l["href"])
        ]
        print(f"\nSố link nghi là trang chi tiết ở cấp 2: {len(detail_like)}")

        if detail_like:
            first = detail_like[0]
            print(f"\n--- Click vào văn bản đầu tiên: {first['text']!r} ---")
            page.click(f"text={first['text']}", timeout=config.NAV_TIMEOUT_MS)
            page.wait_for_load_state("networkidle")

            with open(os.path.join(config.SAMPLES_DIR, "cap3_chi_tiet.html"),
                       "w", encoding="utf-8") as fh:
                fh.write(page.content())
            print(f"URL hiện tại (cấp 3 - chi tiết): {page.url}")

            body_text = page.inner_text("body")
            print("\n--- 50 dòng đầu nội dung text trang chi tiết ---")
            for line in body_text.splitlines()[:50]:
                if line.strip():
                    print(f"  {line.strip()}")
        else:
            print("\nCấp 2 vẫn chưa có link chi tiết trực tiếp — có thể còn "
                  "thêm 1 cấp trung gian nữa. Xem danh sách link cấp 2 ở "
                  "trên để xác định bước tiếp theo thủ công.")

        print("\nHãy tự inspect thêm bằng DevTools (F12) nếu cần. "
              "Nhấn Enter để đóng trình duyệt...")
        input()

        context.close()
        browser.close()


if __name__ == "__main__":
    explore()