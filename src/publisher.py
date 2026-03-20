"""WordPress REST API 投稿モジュール"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from unidecode import unidecode

from .config import BASE_DIR, get_env, load_settings

logger = logging.getLogger(__name__)

QUEUE_DIR = BASE_DIR / "data" / "queue"


def _get_auth() -> tuple[str, str]:
    """WordPress の認証情報を返す"""
    return get_env("WP_USERNAME"), get_env("WP_APP_PASSWORD")


def _get_base_url() -> str:
    """WordPress REST API のベースURLを返す"""
    url = get_env("WP_BASE_URL").rstrip("/")
    return f"{url}/wp-json/wp/v2"


def _generate_slug(title: str) -> str:
    """日本語タイトルからスラッグを生成"""
    slug = unidecode(title).lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    slug = "-".join(slug.split())
    return slug[:80]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
    reraise=True,
)
def _wp_request(
    method: str, endpoint: str, json_data: dict | None = None
) -> dict:
    """WordPress REST API へのリクエスト（リトライ付き）"""
    base_url = _get_base_url()
    auth = _get_auth()

    with httpx.Client(timeout=30.0) as client:
        resp = client.request(
            method,
            f"{base_url}/{endpoint}",
            json=json_data,
            auth=auth,
        )
        resp.raise_for_status()
        return resp.json()


def _resolve_category_id(category_name: str) -> int | None:
    """カテゴリー名からIDを解決。存在しない場合は作成"""
    try:
        categories = _wp_request("GET", f"categories?search={category_name}")
        for cat in categories:
            if cat.get("name") == category_name:
                return cat["id"]

        # 存在しない場合は作成
        result = _wp_request("POST", "categories", {"name": category_name})
        return result.get("id")
    except Exception:
        logger.warning("カテゴリー解決失敗: %s", category_name, exc_info=True)
        return None


def _resolve_tag_ids(tags: list[str], auto_create: bool = True) -> list[int]:
    """タグ名リストからIDリストを解決"""
    tag_ids = []
    for tag_name in tags:
        try:
            existing = _wp_request("GET", f"tags?search={tag_name}")
            found = False
            for t in existing:
                if t.get("name", "").lower() == tag_name.lower():
                    tag_ids.append(t["id"])
                    found = True
                    break
            if not found and auto_create:
                result = _wp_request("POST", "tags", {"name": tag_name})
                tag_ids.append(result["id"])
        except Exception:
            logger.warning("タグ解決失敗: %s", tag_name, exc_info=True)
    return tag_ids


def _save_to_queue(article: dict, source_url: str) -> None:
    """投稿失敗時にローカルJSONに保存"""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"post_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    filepath = QUEUE_DIR / filename
    data = {**article, "source_url": source_url, "queued_at": datetime.utcnow().isoformat()}
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("キューに保存: %s", filepath)


def publish_article(
    article: dict, source_url: str = "", category_name: str = ""
) -> dict:
    """WordPress に記事を投稿

    Args:
        article: generator が返す辞書（title, content, excerpt, tags, slug）
        source_url: 元記事URL
        category_name: カテゴリー名

    Returns:
        {"wp_post_id": int, "wp_url": str}

    Raises:
        Exception: 投稿失敗時
    """
    settings = load_settings()
    wp_cfg = settings.get("wordpress", {})

    slug = article.get("slug") or _generate_slug(article["title"])

    post_data = {
        "title": article["title"],
        "content": article["content"],
        "excerpt": article.get("excerpt", ""),
        "status": wp_cfg.get("post_status", "publish"),
        "slug": slug,
    }

    # カテゴリー
    categories = []
    if category_name:
        cat_id = _resolve_category_id(category_name)
        if cat_id:
            categories.append(cat_id)
    if not categories:
        default_id = wp_cfg.get("default_category_id", 1)
        categories.append(default_id)
    post_data["categories"] = categories

    # タグ
    tags = article.get("tags", [])
    if tags:
        auto_create = wp_cfg.get("auto_create_tags", True)
        tag_ids = _resolve_tag_ids(tags, auto_create=auto_create)
        if tag_ids:
            post_data["tags"] = tag_ids

    # カスタムフィールド
    if wp_cfg.get("custom_field_ai_flag", True):
        post_data["meta"] = {
            "ai_generated": True,
            "source_url": source_url,
        }

    logger.info("WordPress 投稿中: %s", article["title"])

    try:
        result = _wp_request("POST", "posts", post_data)
        wp_post_id = result.get("id")
        wp_url = result.get("link", "")
        logger.info("投稿成功: ID=%s URL=%s", wp_post_id, wp_url)
        return {"wp_post_id": wp_post_id, "wp_url": wp_url}
    except Exception:
        logger.error("WordPress 投稿失敗", exc_info=True)
        _save_to_queue(article, source_url)
        raise


def retry_queued_posts() -> list[dict]:
    """キューに保存された投稿を再試行"""
    results = []
    if not QUEUE_DIR.exists():
        return results

    for json_file in sorted(QUEUE_DIR.glob("*.json")):
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
            source_url = data.pop("source_url", "")
            data.pop("queued_at", None)
            result = publish_article(data, source_url=source_url)
            json_file.unlink()
            results.append(result)
            logger.info("キュー再投稿成功: %s", json_file.name)
        except Exception:
            logger.warning("キュー再投稿失敗: %s", json_file.name, exc_info=True)

    return results
