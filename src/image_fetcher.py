"""画像取得モジュール - Pixabay API で記事に合った画像を取得"""

from __future__ import annotations

import logging
import os
import tempfile
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

PIXABAY_API_URL = "https://pixabay.com/api/"


def _get_pixabay_key() -> str | None:
    """Pixabay API キーを取得（未設定なら None）"""
    return os.environ.get("PIXABAY_API_KEY")


def fetch_image(keywords: list[str], min_width: int = 1200) -> dict | None:
    """キーワードで Pixabay から画像を検索し、最適な1枚を返す

    Args:
        keywords: 検索キーワードのリスト（英語）
        min_width: 最小幅（px）

    Returns:
        {"url": str, "download_path": str, "alt": str, "credit": str} or None
    """
    api_key = _get_pixabay_key()
    if not api_key:
        logger.info("PIXABAY_API_KEY 未設定: 画像取得をスキップ")
        return None

    query = "+".join(keywords[:3])

    try:
        params = {
            "key": api_key,
            "q": query,
            "image_type": "photo",
            "orientation": "horizontal",
            "min_width": min_width,
            "per_page": 5,
            "safesearch": "true",
            "lang": "en",
            "order": "popular",
        }

        with httpx.Client(timeout=15.0) as client:
            resp = client.get(PIXABAY_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        hits = data.get("hits", [])
        if not hits:
            logger.warning("Pixabay 画像が見つかりません: %s", query)
            return None

        # 最も適した画像を選択（最初の結果 = 最も人気）
        best = hits[0]
        image_url = best.get("largeImageURL") or best.get("webformatURL")
        alt_text = best.get("tags", "AI technology image")
        credit = f"Image by {best.get('user', 'Pixabay')} on Pixabay"

        # 画像をダウンロード
        download_path = _download_image(image_url, client=None)
        if not download_path:
            return None

        logger.info("画像取得成功: %s", image_url)
        return {
            "url": image_url,
            "download_path": download_path,
            "alt": alt_text,
            "credit": credit,
        }

    except Exception:
        logger.warning("Pixabay 画像取得失敗", exc_info=True)
        return None


def _download_image(url: str, client: httpx.Client | None = None) -> str | None:
    """画像をダウンロードして一時ファイルに保存"""
    try:
        should_close = False
        if client is None:
            client = httpx.Client(timeout=30.0)
            should_close = True

        try:
            resp = client.get(url)
            resp.raise_for_status()
        finally:
            if should_close:
                client.close()

        # Content-Type からファイル拡張子を推定
        content_type = resp.headers.get("content-type", "image/jpeg")
        ext = ".jpg"
        if "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"

        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        tmp.write(resp.content)
        tmp.close()

        logger.info("画像ダウンロード完了: %s (%d bytes)", tmp.name, len(resp.content))
        return tmp.name

    except Exception:
        logger.warning("画像ダウンロード失敗: %s", url, exc_info=True)
        return None
