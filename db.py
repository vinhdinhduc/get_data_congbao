"""
db.py
-----
Quản lý kết nối MySQL và upsert dữ liệu văn bản.
Dùng mysql-connector-python. Upsert qua `INSERT ... ON DUPLICATE KEY UPDATE`
để chạy lại crawler nhiều lần không lỗi trùng khóa, và cho phép resume.
"""

import logging
import mysql.connector
from mysql.connector import Error as MySQLError

import config

logger = logging.getLogger("congbao.db")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS van_ban (
    so_hieu            VARCHAR(255) NOT NULL,
    parent_unid        VARCHAR(64) NOT NULL,
    ngay_ban_hanh      DATE NULL,
    ngay_hieu_luc      DATE NULL,
    ngay_het_hieu_luc  DATE NULL,
    co_quan_ban_hanh   VARCHAR(500) NULL,
    loai_van_ban       VARCHAR(255) NULL,
    linh_vuc           VARCHAR(255) NULL,
    nguoi_ky           VARCHAR(255) NULL,
    trich_yeu          TEXT NULL,
    tinh_trang         VARCHAR(100) NULL,
    so_cong_bao        VARCHAR(100) NULL,
    trang_cong_bao     VARCHAR(50) NULL,
    source_url         VARCHAR(1000) NOT NULL,
    crawled_at         DATETIME NOT NULL,
    PRIMARY KEY (parent_unid),
    KEY idx_so_hieu (so_hieu)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

# 1 văn bản có thể có NHIỀU file đính kèm -> tách bảng riêng (1-nhiều)
# thay vì nhồi chuỗi ";" vào 1 cột của van_ban (khó query, dễ lệch index
# giữa url và local_path khi có file tải lỗi).
CREATE_FILE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS van_ban_file (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    parent_unid       VARCHAR(64) NOT NULL,
    file_index        INT NOT NULL DEFAULT 0,
    file_url          VARCHAR(1000) NOT NULL,
    file_local_path   VARCHAR(1000) NULL,
    downloaded_at      DATETIME NULL,
    UNIQUE KEY uq_parent_url (parent_unid, file_url(500)),
    KEY idx_parent_unid (parent_unid),
    CONSTRAINT fk_van_ban_file_parent FOREIGN KEY (parent_unid)
        REFERENCES van_ban (parent_unid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

UPSERT_SQL = """
INSERT INTO van_ban (
    so_hieu, parent_unid, ngay_ban_hanh, ngay_hieu_luc, ngay_het_hieu_luc,
    co_quan_ban_hanh, loai_van_ban, linh_vuc, nguoi_ky, trich_yeu,
    tinh_trang, so_cong_bao, trang_cong_bao,
    source_url, crawled_at
) VALUES (
    %(so_hieu)s, %(parent_unid)s, %(ngay_ban_hanh)s, %(ngay_hieu_luc)s,
    %(ngay_het_hieu_luc)s, %(co_quan_ban_hanh)s, %(loai_van_ban)s,
    %(linh_vuc)s, %(nguoi_ky)s, %(trich_yeu)s, %(tinh_trang)s,
    %(so_cong_bao)s, %(trang_cong_bao)s,
    %(source_url)s, %(crawled_at)s
)
ON DUPLICATE KEY UPDATE
    so_hieu             = VALUES(so_hieu),
    ngay_ban_hanh       = VALUES(ngay_ban_hanh),
    ngay_hieu_luc       = VALUES(ngay_hieu_luc),
    ngay_het_hieu_luc   = VALUES(ngay_het_hieu_luc),
    co_quan_ban_hanh    = VALUES(co_quan_ban_hanh),
    loai_van_ban        = VALUES(loai_van_ban),
    linh_vuc            = VALUES(linh_vuc),
    nguoi_ky            = VALUES(nguoi_ky),
    trich_yeu           = VALUES(trich_yeu),
    tinh_trang          = VALUES(tinh_trang),
    so_cong_bao         = VALUES(so_cong_bao),
    trang_cong_bao      = VALUES(trang_cong_bao),
    crawled_at          = VALUES(crawled_at);
"""

# Upsert từng file: nếu url đã tồn tại cho parent_unid này thì cập nhật lại
# local_path/downloaded_at (vd: lần trước tải lỗi, lần sau tải lại thành công)
UPSERT_FILE_SQL = """
INSERT INTO van_ban_file (
    parent_unid, file_index, file_url, file_local_path, downloaded_at
) VALUES (
    %(parent_unid)s, %(file_index)s, %(file_url)s, %(file_local_path)s,
    %(downloaded_at)s
)
ON DUPLICATE KEY UPDATE
    file_index       = VALUES(file_index),
    file_local_path  = VALUES(file_local_path),
    downloaded_at    = VALUES(downloaded_at);
"""

CHECK_EXISTS_SQL = "SELECT 1 FROM van_ban WHERE parent_unid = %s LIMIT 1;"


class Database:
    def __init__(self):
        self.conn = None

    def connect(self):
        if not config.DB_CONFIG["user"] :
            raise RuntimeError(
                "Thiếu DB_USER / DB_PASSWORD. Hãy đặt trong file .env "
                "(xem .env.example), không hard-code trong code."
            )
        self.conn = mysql.connector.connect(**config.DB_CONFIG)
        logger.info("Đã kết nối MySQL: %s", config.DB_CONFIG["host"])
        self._ensure_table()
        return self

    def _ensure_table(self):
        cur = self.conn.cursor()
        cur.execute(CREATE_TABLE_SQL)
        self.conn.commit()

        # Nếu bảng van_ban đã tồn tại từ phiên bản schema cũ (không có cột
        # parent_unid), CREATE TABLE IF NOT EXISTS ở trên sẽ không tự sửa.
        # Kiểm tra và báo lỗi rõ ràng thay vì để MySQL ném lỗi khó hiểu
        # ngay giữa lúc crawl.
        cur.execute("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'van_ban'
              AND column_name = 'parent_unid'
        """, (config.DB_CONFIG["database"],))
        has_column = cur.fetchone()[0] > 0

        if not has_column:
            cur.close()
            raise RuntimeError(
                "Bảng 'van_ban' đã tồn tại trong MySQL nhưng theo schema CŨ "
                "(thiếu cột 'parent_unid'). Đây là bảng tạo từ phiên bản "
                "trước của crawler. Hãy xóa bảng cũ rồi chạy lại:\n\n"
                "    mysql -u <user> -p -e \"DROP TABLE van_ban;\" <database>\n\n"
                "hoặc kết nối vào MySQL và chạy: DROP TABLE van_ban;\n"
                "(An toàn để xóa nếu bảng chưa có dữ liệu quan trọng — "
                "kiểm tra bằng SELECT COUNT(*) FROM van_ban; trước khi xóa "
                "nếu không chắc.)"
            )

        # Nếu van_ban vẫn còn 2 cột file_urls/file_local_paths kiểu CŨ
        # (trước khi tách bảng van_ban_file), báo cho biết cần chạy
        # migrate_files_to_table.py để chuyển dữ liệu sang bảng mới rồi
        # mới DROP 2 cột đó -- không tự ý xóa dữ liệu ở đây.
        cur.execute("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'van_ban'
              AND column_name = 'file_urls'
        """, (config.DB_CONFIG["database"],))
        has_old_file_columns = cur.fetchone()[0] > 0
        cur.close()

        if has_old_file_columns:
            logger.warning(
                "Bảng 'van_ban' vẫn còn cột cũ file_urls/file_local_paths "
                "(schema trước khi tách bảng van_ban_file). Chạy "
                "'python migrate_files_to_table.py' để chuyển dữ liệu file "
                "sang bảng van_ban_file mới, sau đó có thể DROP 2 cột cũ."
            )

        # Tạo bảng file (1 văn bản - nhiều file đính kèm)
        cur2 = self.conn.cursor()
        cur2.execute(CREATE_FILE_TABLE_SQL)
        self.conn.commit()
        cur2.close()

    def already_crawled(self, parent_unid: str) -> bool:
        """Dùng để resume nhanh hơn nếu muốn bỏ qua văn bản đã có (mặc định
        crawler vẫn upsert lại để cập nhật field mới nếu có)."""
        cur = self.conn.cursor()
        cur.execute(CHECK_EXISTS_SQL, (parent_unid,))
        exists = cur.fetchone() is not None
        cur.close()
        return exists

    def upsert_van_ban(self, record: dict):
        cur = self.conn.cursor()
        try:
            cur.execute(UPSERT_SQL, record)
            self.conn.commit()
        except MySQLError as e:
            self.conn.rollback()
            logger.error("Lỗi upsert parent_unid=%s: %s", record.get("parent_unid"), e)
            raise
        finally:
            cur.close()

    def upsert_van_ban_files(self, parent_unid: str, files: list[dict]):
        """files: list các dict {file_index, file_url, file_local_path,
        downloaded_at}. Gọi kể cả khi DOWNLOAD_ATTACHMENTS=False (khi đó
        file_local_path/downloaded_at = None, chỉ lưu link gốc) để bảng
        van_ban_file luôn phản ánh đúng có bao nhiêu file đính kèm."""
        if not files:
            return
        cur = self.conn.cursor()
        try:
            for f in files:
                cur.execute(UPSERT_FILE_SQL, {
                    "parent_unid": parent_unid,
                    "file_index": f.get("file_index", 0),
                    "file_url": f["file_url"],
                    "file_local_path": f.get("file_local_path"),
                    "downloaded_at": f.get("downloaded_at"),
                })
            self.conn.commit()
        except MySQLError as e:
            self.conn.rollback()
            logger.error("Lỗi upsert file cho parent_unid=%s: %s", parent_unid, e)
            raise
        finally:
            cur.close()

    def close(self):
        if self.conn and self.conn.is_connected():
            self.conn.close()
            logger.info("Đã đóng kết nối MySQL")