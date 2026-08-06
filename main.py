"""
main.py — điểm chạy chính.

Cách chạy:
    python main.py            # chạy headless
    python main.py --headed   # mở trình duyệt thật để quan sát (debug)
"""

import argparse
from crawler import CongBaoCrawler, setup_logging


def main():
    parser = argparse.ArgumentParser(description="Crawler Công báo tỉnh Sơn La")
    parser.add_argument(
        "--headed", action="store_true",
        help="Chạy với headless=False để quan sát trình duyệt (debug)."
    )
    args = parser.parse_args()

    setup_logging()
    crawler = CongBaoCrawler(headless=not args.headed)
    crawler.run()


if __name__ == "__main__":
    main()