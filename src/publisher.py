"""WordPress REST API 投稿モジュール"""

from __future__ import annotations

import json
import logging
import os
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

# リトライすべきでない HTTP ステータスコード（WAF/認証エラーなど）
_NO_RETRY_STATUS = {401, 403, 404, 405}


class WPFatalError(Exception):
    """リトライ不要な WordPress エラー（WAF ブロック等）"""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"WP API {status_code}: {body[:200]}")


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
    """WordPress REST API へのリクエスト（リトライ付き）

    403/401 等の WAF・認証エラーは即座に WPFatalError を送出し、
    無駄なリトライを回避する。
    """
    base_url = _get_base_url()
    auth = _get_auth()

    # ブラウザに近いヘッダーで XSERVER WAF を回避
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    with httpx.Client(timeout=30.0, headers=headers) as client:
        resp = client.request(
            method,
            f"{base_url}/{endpoint}",
            json=json_data,
            auth=auth,
        )

        if resp.status_code in _NO_RETRY_STATUS:
            body = resp.text[:500]
            logger.error(
                "WP API 致命的エラー（リトライ不可）: %s %s -> %s, body=%s",
                method, endpoint, resp.status_code, body,
            )
            raise WPFatalError(resp.status_code, body)

        if resp.status_code >= 400:
            body = resp.text[:500]
            logger.error(
                "WP API エラー: %s %s -> %s, body=%s",
                method, endpoint, resp.status_code, body,
            )
        resp.raise_for_status()
        return resp.json()


def upload_featured_image(
    image_path: str, title: str, alt_text: str = "", credit: str = ""
) -> int | None:
    """画像をWordPressメディアライブラリにアップロードし、メディアIDを返す

    Args:
        image_path: ローカル画像ファイルパス
        title: 画像タイトル（記事タイトルを使用）
        alt_text: alt属性テキスト
        credit: クレジット表記

    Returns:
        メディアID or None（失敗時）
    """
    base_url = _get_base_url()
    auth = _get_auth()

    file_path = Path(image_path)
    if not file_path.exists():
        logger.warning("画像ファイルが見つかりません: %s", image_path)
        return None

    # Content-Type を拡張子から判定
    ext_to_mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    mime_type = ext_to_mime.get(file_path.suffix.lower(), "image/jpeg")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Content-Disposition": f'attachment; filename="{file_path.name}"',
        "Content-Type": mime_type,
    }

    logger.info("画像アップロード開始: %s (%s, %d bytes)", file_path, mime_type, file_path.stat().st_size)

    try:
        with httpx.Client(timeout=60.0, headers=headers) as client:
            with open(file_path, "rb") as f:
                image_bytes = f.read()

            logger.info("画像データ読み込み完了: %d bytes -> POST %s/media", len(image_bytes), base_url)

            resp = client.post(
                f"{base_url}/media",
                content=image_bytes,
                auth=auth,
            )

            logger.info("画像アップロード応答: HTTP %s", resp.status_code)

            if resp.status_code in _NO_RETRY_STATUS:
                logger.warning(
                    "画像アップロード失敗 (HTTP %s): %s",
                    resp.status_code, resp.text[:300],
                )
                return None

            resp.raise_for_status()
            media_data = resp.json()
            media_id = media_data.get("id")

        # alt_text を設定
        if media_id and alt_text:
            try:
                _wp_request("POST", f"media/{media_id}", {
                    "alt_text": alt_text,
                    "caption": credit,
                })
            except Exception:
                logger.warning("画像メタデータ更新失敗（投稿は続行）")

        logger.info("画像アップロード成功: media_id=%s", media_id)
        return media_id

    except Exception:
        logger.warning("画像アップロード失敗", exc_info=True)
        return None
    finally:
        # 一時ファイルを削除
        try:
            os.unlink(image_path)
        except OSError:
            pass


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
        logger.warning("カテゴリー解決失敗: %s（デフォルトカテゴリーを使用）", category_name)
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
        except WPFatalError:
            logger.warning("タグ解決: WAFブロックのためスキップ: %s", tag_name)
            break  # WAF にブロックされている場合、残りのタグも同様なのでループ終了
        except Exception:
            logger.warning("タグ解決失敗: %s", tag_name)
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
    article: dict,
    source_url: str = "",
    category_name: str = "",
    featured_image_id: int | None = None,
) -> dict:
    """WordPress に記事を投稿

    Args:
        article: generator が返す辞書（title, content, excerpt, tags, slug）
        source_url: 元記事URL
        category_name: カテゴリー名
        featured_image_id: アイキャッチ画像のメディアID

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

    # アイキャッチ画像
    if featured_image_id:
        post_data["featured_media"] = featured_image_id

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

    # タグ（WAF ブロック時はスキップ）
    tags = article.get("tags", [])
    if tags:
        auto_create = wp_cfg.get("auto_create_tags", True)
        tag_ids = _resolve_tag_ids(tags, auto_create=auto_create)
        if tag_ids:
            post_data["tags"] = tag_ids

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


def retry_queued_posts(limit: int = 1) -> list[dict]:
    """キューに保存された投稿を再試行（最大 limit 件）"""
    results = []
    if not QUEUE_DIR.exists():
        return results

    for json_file in sorted(QUEUE_DIR.glob("*.json")):
        if len(results) >= limit:
            break
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
