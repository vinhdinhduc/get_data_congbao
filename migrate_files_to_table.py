"""
migrate_files_to_table.py
--------------------------
Chuyển dữ liệu file đính kèm từ 2 cột CŨ (file_urls, file_local_paths -
dạng chuỗi nối ";") sang bảng van_ban_file MỚI (1 văn bản - nhiều file).

Không crawl lại gì cả, chỉ đọc dữ liệu đã có trong MySQL rồi ghi sang bảng
mới. An toàn chạy nhiều lần (upsert theo parent_unid + file_url).

LƯU Ý: vì 2 cột cũ lưu file_urls (tất cả link) và file_local_paths (chỉ
những file tải THÀNH CÔNG) tách rời nhau, khi số lượng 2 danh sách lệch
nhau, script này CHỈ match được theo thứ tự -- nếu văn bản nào có file tải
lỗi ở giữa danh sách thì có thể bị gán sai local_path cho url. Trường hợp
này script sẽ log cảnh báo để bạn biết văn bản nào cần kiểm tra lại thủ
công (hiếm khi xảy ra, vì hầu hết văn bản chỉ có 1 file).

Sau khi chạy xong và kiểm tra ổn, có thể DROP 2 cột cũ:
    ALTER TABLE van_ban DROP COLUMN file_urls, DROP COLUMN file_local_paths;

Chạy:
    python migrate_files_to_table.py
"""

import logging

import config
from db import Database, CREATE_FILE_TABLE_SQL

logger = logging.getLogger("congbao.migrate")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db = Database().connect()  # __init__ đã tự tạo bảng van_ban_file nếu chưa có

    cur = db.conn.cursor(dictionary=True)
    cur.execute("""
        SELECT COUNT(*) AS c FROM information_schema.columns
        WHERE table_schema = %s AND table_name = 'van_ban' AND column_name = 'file_urls'
    """, (config.DB_CONFIG["database"],))
    has_old_cols = cur.fetchone()["c"] > 0

    if not has_old_cols:
        logger.info("Bảng van_ban không còn cột file_urls/file_local_paths cũ. "
                     "Không có gì để migrate.")
        cur.close()
        db.close()
        return

    cur.execute("""
        SELECT parent_unid, file_urls, file_local_paths
        FROM van_ban
        WHERE file_urls IS NOT NULL AND file_urls != ''
    """)
    rows = cur.fetchall()
    cur.close()
    logger.info("Số văn bản có file cần migrate: %d", len(rows))

    migrated, mismatched = 0, 0
    for row in rows:
        urls = [u for u in (row["file_urls"] or "").split(";") if u]
        paths = [p for p in (row["file_local_paths"] or "").split(";") if p]

        if paths and len(paths) != len(urls):
            mismatched += 1
            logger.warning(
                "parent_unid=%s: số url (%d) != số local_path (%d) -- có thể "
                "có file tải lỗi nằm giữa danh sách, match theo thứ tự có thể "
                "SAI. Kiểm tra lại thủ công nếu văn bản này quan trọng.",
                row["parent_unid"], len(urls), len(paths),
            )

        file_records = []
        for i, url in enumerate(urls):
            local_path = paths[i] if i < len(paths) else None
            file_records.append({
                "file_index": i,
                "file_url": url,
                "file_local_path": local_path,
                "downloaded_at": None,  # không rõ thời điểm tải cũ, để trống
            })

        db.upsert_van_ban_files(row["parent_unid"], file_records)
        migrated += 1

    logger.info(
        "Hoàn tất migrate. Đã chuyển: %d văn bản, %d văn bản bị lệch số "
        "lượng url/local_path (xem log WARNING ở trên).",
        migrated, mismatched,
    )
    logger.info(
        "Sau khi kiểm tra dữ liệu trong bảng van_ban_file ổn, có thể chạy: "
        "ALTER TABLE van_ban DROP COLUMN file_urls, DROP COLUMN file_local_paths;"
    )
    db.close()


if __name__ == "__main__":
    main()