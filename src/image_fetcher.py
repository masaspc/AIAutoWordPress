"""画像取得モジュール - Pixabay API で記事に合った画像を取得"""

from __future__ import annotations

import logging
import os
import tempfile

import httpx

logger = logging.getLogger(__name__)

PIXABAY_API_URL = "https://pixabay.com/api/"


def _get_pixabay_key() -> str | None:
    """Pixabay API キーを取得（未設定なら None）"""
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    return key if key else None


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
        logger.warning("PIXABAY_API_KEY 未設定: 画像取得をスキップ")
        return None

    # Pixabay は空白区切りのクエリ
    query = " ".join(keywords[:3])
    logger.info("Pixabay 画像検索: query='%s'", query)

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
            logger.info("Pixabay API レスポンス: HTTP %s", resp.status_code)

            if resp.status_code != 200:
                logger.warning("Pixabay API エラー: %s %s", resp.status_code, resp.text[:300])
                return None

            data = resp.json()

        total = data.get("totalHits", 0)
        hits = data.get("hits", [])
        logger.info("Pixabay 検索結果: %d hits (total=%d)", len(hits), total)

        if not hits:
            # min_width を下げてリトライ
            if min_width > 800:
                logger.info("min_width を下げて再検索")
                return fetch_image(keywords, min_width=640)
            logger.warning("Pixabay 画像が見つかりません: %s", query)
            return None

        # 最も適した画像を選択（最初の結果 = 最も人気）
        best = hits[0]
        image_url = best.get("largeImageURL") or best.get("webformatURL")
        if not image_url:
            logger.warning("Pixabay: 画像URLが取得できません")
            return None

        alt_text = best.get("tags", "AI technology image")
        credit = f"Image by {best.get('user', 'Pixabay')} on Pixabay"

        logger.info("Pixabay 画像選択: %s (by %s)", image_url, best.get("user", "?"))

        # 画像をダウンロード
        download_path = _download_image(image_url)
        if not download_path:
            return None

        return {
            "url": image_url,
            "download_path": download_path,
            "alt": alt_text,
            "credit": credit,
        }

    except Exception:
        logger.warning("Pixabay 画像取得失敗", exc_info=True)
        return None


def _download_image(url: str) -> str | None:
    """画像をダウンロードして一時ファイルに保存"""
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, follow_redirects=True)
            resp.raise_for_status()

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
