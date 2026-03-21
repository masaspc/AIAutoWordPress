"""AINAP メインエントリポイント

バッチ実行パイプライン:
1. flock で多重起動防止
2. ニュース収集
3. 記事生成（Claude API）
4. 品質チェック
5. WordPress 投稿
6. 通知送信
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path

from .config import BASE_DIR, load_settings
from . import database as db
from .generator import generate_article
from .image_fetcher import fetch_image
from .notifier import (
    notify_dead_letter,
    notify_error,
    notify_pipeline_complete,
    notify_success,
    send_daily_summary,
)
from .publisher import publish_article, retry_queued_posts, upload_featured_image
from .quality import check_article_quality
from .scraper import scrape_all_sources

LOCK_FILE = BASE_DIR / "data" / "ainap.lock"

logger = logging.getLogger("ainap")


class JsonFormatter(logging.Formatter):
    """JSON 形式のログフォーマッタ"""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, ensure_ascii=False)


def setup_logging() -> None:
    """ログ設定を初期化"""
    settings = load_settings()
    log_cfg = settings.get("logging", {})

    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    rotation_days = log_cfg.get("rotation_days", 7)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # ファイルハンドラ（日次ローテーション）
    log_file = log_dir / "ainap.log"
    file_handler = logging.handlers.TimedRotatingFileHandler(
        str(log_file),
        when="midnight",
        interval=1,
        backupCount=rotation_days,
        encoding="utf-8",
    )

    if log_cfg.get("format") == "json":
        file_handler.setFormatter(JsonFormatter())
    else:
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root_logger.addHandler(file_handler)

    # stderr ハンドラ（systemd journald 用）
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root_logger.addHandler(stderr_handler)


def acquire_lock() -> object | None:
    """flock でロックを取得。既に実行中の場合は None を返す"""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fp = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fp
    except OSError:
        lock_fp.close()
        return None


def process_article(article: dict, settings: dict) -> dict | None:
    """単一記事の生成→品質チェック→投稿パイプライン"""
    article_id = article.get("id") or article.get("article_id")

    try:
        # 記事生成
        generated = generate_article(article)
        db.update_article_status(article_id, "generated")

        # 品質チェック
        checked = check_article_quality(generated, source_url=article.get("url", ""))

        # アイキャッチ画像の取得・アップロード
        featured_image_id = None
        image_keywords = generated.get("image_keywords", [])
        if image_keywords:
            image_data = fetch_image(image_keywords)
            if image_data:
                featured_image_id = upload_featured_image(
                    image_path=image_data["download_path"],
                    title=checked["title"],
                    alt_text=image_data.get("alt", ""),
                    credit=image_data.get("credit", ""),
                )

        # WordPress 投稿
        category = article.get("category", "")
        result = publish_article(
            checked,
            source_url=article.get("url", ""),
            category_name=category,
            featured_image_id=featured_image_id,
        )

        # DB 記録
        db.save_post(
            article_id=article_id,
            wp_post_id=result["wp_post_id"],
            wp_url=result["wp_url"],
            title=checked["title"],
            tokens_in=generated.get("tokens_in", 0),
            tokens_out=generated.get("tokens_out", 0),
        )
        db.update_article_status(article_id, "published")

        # 成功通知
        notification_cfg = settings.get("notification", {})
        if notification_cfg.get("on_success", True):
            notify_success(checked["title"], result["wp_url"], category)

        logger.info("パイプライン完了: %s", checked["title"])
        return result

    except Exception as e:
        logger.error("記事処理失敗: %s", article.get("title", ""), exc_info=True)
        db.update_article_status(article_id, "failed")
        db.enqueue_failed(article_id, type(e).__name__, str(e))

        # Dead Letter チェック
        from . import database
        queue_entries = database.get_dead_letter_entries()
        for entry in queue_entries:
            if entry["article_id"] == article_id:
                database.move_to_dead_letter(entry["queue_id"])
                notify_dead_letter(article.get("title", ""), str(e))

        notification_cfg = settings.get("notification", {})
        if notification_cfg.get("on_error", True):
            notify_error(type(e).__name__, str(e), article.get("title", ""))

        return None


async def run_pipeline() -> None:
    """メインパイプライン実行"""
    settings = load_settings()
    schedule_cfg = settings.get("schedule", {})
    max_posts = schedule_cfg.get("max_posts_per_run", 1)

    logger.info("=== AINAP パイプライン開始 ===")

    # DB 初期化
    db.init_db()

    published_count = 0
    failed_count = 0
    retried_count = 0

    # キューに残った JSON ファイルの再投稿（max_posts を超えない）
    if published_count < max_posts:
        queued_results = retry_queued_posts(limit=max_posts - published_count)
        if queued_results:
            published_count += len(queued_results)
            retried_count += len(queued_results)
            logger.info("キュー再投稿: %d 件成功", len(queued_results))

    # リトライキュー処理（max_posts を超えない）
    if published_count < max_posts:
        retry_items = db.get_retry_queue()
        for item in retry_items:
            if published_count >= max_posts:
                break
            logger.info("リトライ処理: %s", item.get("title", ""))
            result = process_article(item, settings)
            if result:
                published_count += 1
                retried_count += 1
            else:
                failed_count += 1

    # 新規ニュース収集
    collected = await scrape_all_sources()
    logger.info("新規収集: %d 記事", len(collected))

    # 未処理記事から処理（max_posts を超えない + 類似記事スキップ）
    if published_count < max_posts:
        remaining = max_posts - published_count
        # 類似記事スキップ分を考慮して多めに取得
        candidates = db.get_unprocessed_articles(limit=remaining * 5)

        for article in candidates:
            if published_count >= max_posts:
                break

            # 類似タイトルチェック: 過去7日の投稿と類似していればスキップ
            if db.is_similar_title_exists(article.get("title", ""), days=7):
                logger.info("類似記事スキップ: %s", article.get("title", ""))
                db.update_article_status(article["id"], "skipped_similar")
                continue

            result = process_article(article, settings)
            if result:
                published_count += 1
            else:
                failed_count += 1
    logger.info("投稿完了: %d / %d 記事", published_count, len(unprocessed))

    # パイプライン完了通知（毎回送信）
    notify_pipeline_complete(
        collected=len(collected),
        published=published_count,
        failed=failed_count,
        retried=retried_count,
    )

    # 日次サマリー（21:00 JST 実行時）
    notification_cfg = settings.get("notification", {})
    if notification_cfg.get("daily_summary", True):
        now_hour = datetime.utcnow().hour + 9  # JST概算
        if now_hour >= 24:
            now_hour -= 24
        if now_hour == 21:
            posts_today = db.get_today_posts()
            send_daily_summary(posts_today)
            logger.info("日次サマリー送信完了")

    logger.info("=== AINAP パイプライン終了 ===")


def main() -> None:
    """エントリポイント"""
    setup_logging()

    # 多重起動防止
    lock_fp = acquire_lock()
    if lock_fp is None:
        logger.warning("別のプロセスが実行中です。終了します。")
        sys.exit(0)

    try:
        asyncio.run(run_pipeline())
    except KeyboardInterrupt:
        logger.info("中断されました")
    except Exception:
        logger.critical("予期せぬエラー", exc_info=True)
        try:
            notify_error("CriticalError", "予期せぬエラーが発生しました")
        except Exception:
            pass
        sys.exit(1)
    finally:
        lock_fp.close()


if __name__ == "__main__":
    main()
