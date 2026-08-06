"""
scripts/explore.py
-------------------
Công cụ khảo sát DOM thật (Bước 1 trong yêu cầu gốc).

Vì tool web_fetch của Claude bị robots.txt của site chặn, việc khảo sát
DOM thật BẮT BUỘC bạn phải tự chạy script này trên máy của bạn.

Chạy:
    python scripts/explore.py

Script sẽ:
1. Mở trình duyệt thật (headless=False) tới trang danh mục.
2. In ra: có dùng frame/frameset không, danh sách selector <a> khả nghi
   trỏ tới trang chi tiết, có phân trang không.
3. Lưu HTML của trang danh mục và 1 trang chi tiết đầu tiên vào samples/.
4. Dừng lại (input) để bạn tự inspect bằng DevTools trước khi đóng.

Sau khi chạy xong, đối chiếu output với các biến trong config.py
(CATEGORY_FRAME_NAME, CATEGORY_ITEM_LINK_SELECTOR, DETAIL_FIELD_LABELS...)
và chỉnh lại cho khớp.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402
import config  # noqa: E402


def explore():
    os.makedirs(config.SAMPLES_DIR, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=config.USER_AGENT, viewport=config.VIEWPORT
        )
        page = context.new_page()

        print(f"\n--- Mở trang danh mục: {config.CATEGORY_URL} ---")
        page.goto(config.CATEGORY_URL, wait_until="networkidle")

        # 1. Kiểm tra frame
        frames = page.frames
        print(f"\nSố lượng frame phát hiện: {len(frames)}")
        for f in frames:
            print(f"  - frame name={f.name!r} url={f.url}")

        # 2. Lưu HTML trang danh mục
        html_path = os.path.join(config.SAMPLES_DIR, "danh_muc.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(page.content())
        print(f"\nĐã lưu HTML danh mục -> {html_path}")

        # 3. Thử tìm link nghi là trang chi tiết
        candidate_links = page.eval_on_selector_all(
            "a", "els => els.map(e => e.getAttribute('href')).filter(Boolean)"
        )
        detail_like = [h for h in candidate_links if "str/" in h or "OpenDocument" in h]
        print(f"\nTổng số thẻ <a>: {len(candidate_links)}")
        print(f"Số link nghi là trang chi tiết (chứa 'str/' hoặc 'OpenDocument'): "
              f"{len(detail_like)}")
        for h in detail_like[:10]:
            print(f"  - {h}")

        # 4. Mở thử 1 trang chi tiết (nếu có) và lưu HTML
        if detail_like:
            first_detail = detail_like[0]
            if not first_detail.startswith("http"):
                first_detail = config.BASE_URL.rstrip("/") + "/" + first_detail.lstrip("/")
            print(f"\n--- Mở thử trang chi tiết: {first_detail} ---")
            page.goto(first_detail, wait_until="networkidle")
            detail_html_path = os.path.join(config.SAMPLES_DIR, "chi_tiet_mau.html")
            with open(detail_html_path, "w", encoding="utf-8") as fh:
                fh.write(page.content())
            print(f"Đã lưu HTML chi tiết mẫu -> {detail_html_path}")

            body_text = page.inner_text("body")
            print("\n--- 40 dòng đầu nội dung text trang chi tiết (để đối chiếu "
                  "nhãn field: Số hiệu, Ngày ban hành...) ---")
            for line in body_text.splitlines()[:40]:
                if line.strip():
                    print(f"  {line.strip()}")

        print("\nHãy tự inspect bằng DevTools (F12) trong cửa sổ trình duyệt "
              "đang mở. Nhấn Enter tại đây để đóng trình duyệt...")
        input()

        context.close()
        browser.close()


if __name__ == "__main__":
    explore()