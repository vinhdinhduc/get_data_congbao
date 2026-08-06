"""
explore_gazette_api.py
------------------------
Gọi THẲNG 2 API ẩn phát hiện được từ JS trong trang chủ (không cần click
UI qua Playwright nữa, nhanh hơn nhiều):

  1. GazettesByYearMonth?openView&Count=-1&RestrictToCategory=<năm>
     -> HTML chứa toàn bộ cây Tháng -> Số công báo của 1 năm (1 lần gọi
     duy nhất, KHÔNG cần gọi riêng từng tháng).

  2. GazettesByYearMonthDayNo?openView&Count=-1&RestrictToCategory=<fullNo>
     -> HTML chứa thẻ <gazettefile>...</gazettefile> -- đây là chỗ chứa
     link tải "Tải cuốn công báo" (file PDF đóng số) mà anh yêu cầu.

Mục tiêu: xác định giá trị docCate thật (nằm trong attribute 'value' của
<span> bên trong <a class="gazette-no">, chỉ xuất hiện SAU khi gọi API #1)
và cấu trúc HTML thật trả về từ API #2, để viết crawler chính thức.

KHÔNG lưu DB, chỉ in ra console + lưu HTML thô vào samples/ để đối chiếu.

Chạy:
    python explore_gazette_api.py [năm]
    (mặc định năm = 2026 nếu không truyền)
"""

import os
import sys
import re
from urllib.parse import quote

from playwright.sync_api import sync_playwright

import config


def save(text: str, filename: str):
    os.makedirs(config.SAMPLES_DIR, exist_ok=True)
    path = os.path.join(config.SAMPLES_DIR, filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"Đã lưu -> {path}")


def main():
    year = sys.argv[1] if len(sys.argv) > 1 else "2026"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent=config.USER_AGENT)

        # ===== API #1: danh sách Tháng -> Số công báo của 1 năm =====
        url1 = (f"{config.BASE_URL}/congbao.nsf/GazettesByYearMonth"
                f"?openView&Count=-1&RestrictToCategory={year}")
        print(f"--- GET {url1} ---")
        resp1 = context.request.get(url1)
        print(f"Status: {resp1.status}")
        html1 = resp1.text()
        save(html1, f"gazettes_by_year_{year}.html")
        print(f"Độ dài response: {len(html1)} ký tự. In 3000 ký tự đầu:\n")
        print(html1[:3000])

        # Parse response bằng 1 page tạm (set_content) để dùng CSS selector
        page = context.new_page()
        page.set_content(html1)

        rows_info = page.eval_on_selector_all(
            "tr",
            """els => els.map(tr => ({
                text: tr.innerText.trim().slice(0, 80),
                cate: tr.getAttribute('cate'),
                iscate: tr.getAttribute('iscate')
            }))""",
        )
        print(f"\n--- Tổng số <tr> trong response: {len(rows_info)} ---")
        for r in rows_info[:30]:
            print(f"  cate={r['cate']!r:20s} iscate={r['iscate']!r:6s} text={r['text']!r}")

        spans = page.eval_on_selector_all(
            "a.gazette-no span, span[value]",
            "els => els.map(e => ({value: e.getAttribute('value'), "
            "doccate: e.getAttribute('doccate'), text: e.innerText.trim()}))",
        )
        print(f"\n--- Tổng số <span value=...> (số công báo cụ thể) tìm thấy: {len(spans)} ---")
        for s in spans[:30]:
            print(f"  text={s['text']!r:30s} value={s['value']!r} doccate={s['doccate']!r}")

        if not spans or not spans[0]["value"]:
            print("\n!!! KHÔNG tìm thấy span[value] nào -- có thể response #1 rỗng "
                  "hoặc cấu trúc khác dự đoán. Xem lại nội dung đã lưu trong "
                  f"samples/gazettes_by_year_{year}.html để tự đối chiếu tay.")
            page.close()
            context.close()
            browser.close()
            return

        # ===== API #2: file đóng số của 1 Số công báo cụ thể =====
        doc_cate_raw = spans[0]["value"]
        full_no_edit = doc_cate_raw.replace("+", "_") if "+" in doc_cate_raw else doc_cate_raw

        # Thử cả 2 cách encode vì JS gốc KHÔNG encodeURIComponent lại
        # fullNo_edit trước khi gắn vào URL (dựa vào browser tự xử lý) --
        # với Python request cần tự quyết định encode kiểu nào mới đúng.
        variants = {
            "raw (không encode)": full_no_edit,
            "quote() chuẩn": quote(full_no_edit),
        }
        for label, val in variants.items():
            url2 = (f"{config.BASE_URL}/congbao.nsf/GazettesByYearMonthDayNo"
                     f"?openView&Count=-1&RestrictToCategory={val}")
            print(f"\n--- GET ({label}): {url2} ---")
            try:
                resp2 = context.request.get(url2)
                print(f"Status: {resp2.status}")
                html2 = resp2.text()
                save(html2, f"gazette_file_{label.split()[0]}.html")
                print(f"Độ dài response: {len(html2)} ký tự. Toàn bộ nội dung:\n")
                print(html2)
                m = re.search(r"<gazettefile>(.*?)</gazettefile>", html2, re.DOTALL)
                if m:
                    print(f"\n>>> Nội dung trong <gazettefile>...</gazettefile>:\n{m.group(1)}")
                else:
                    print("\n>>> Không tìm thấy thẻ <gazettefile> trong response này.")
            except Exception as e:
                print(f"  Lỗi khi gọi: {e}")

        # ===== (tham khảo) API danh sách văn bản trong số -- không cần
        # crawl, chỉ gọi để xác nhận docCate map đúng số công báo nào =====
        url3 = (f"{config.BASE_URL}/congbao.nsf/DocumentsByGazette"
                 f"?openView&Count=-1&RestrictToCategory={quote(doc_cate_raw)}")
        print(f"\n--- (tham khảo, không bắt buộc dùng) GET {url3} ---")
        try:
            resp3 = context.request.get(url3)
            print(f"Status: {resp3.status}, độ dài: {len(resp3.text())} ký tự")
            save(resp3.text(), "documents_by_gazette_sample.html")
        except Exception as e:
            print(f"  Lỗi khi gọi: {e}")

        page.close()
        context.close()
        browser.close()

    print("\n=== XONG. Gửi lại toàn bộ output console này cho mình. ===")


if __name__ == "__main__":
    main()