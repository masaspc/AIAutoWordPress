"""記事品質チェックモジュール"""

from __future__ import annotations

import logging
import re

from .config import load_settings

logger = logging.getLogger(__name__)

DISCLAIMER_JA = '<p class="ai-disclaimer">※本記事はAIによる自動生成記事です。</p>'


def check_article_quality(article: dict, source_url: str = "") -> dict:
    """生成記事の品質をチェックし、必要に応じて修正して返す

    Args:
        article: generator.py が返す辞書（title, content, excerpt, tags, slug）
        source_url: 元記事URL

    Returns:
        検証・修正済みの記事辞書

    Raises:
        ValueError: 品質基準を満たさない場合
    """
    settings = load_settings()
    gen_cfg = settings.get("generator", {})
    min_chars = gen_cfg.get("article_min_chars", 800)
    max_chars = gen_cfg.get("article_max_chars", 3000)
    include_disclaimer = gen_cfg.get("include_disclaimer", True)
    include_source_link = gen_cfg.get("include_source_link", True)

    content = article.get("content", "")
    title = article.get("title", "")

    # --- タイトルチェック ---
    if not title:
        raise ValueError("タイトルが空です")
    if len(title) > 60:
        logger.warning("タイトルが60文字超過 (%d文字): %s", len(title), title)
        # 切り詰めはしない（SEO上は警告のみ）

    # --- 本文文字数チェック ---
    text_only = re.sub(r"<[^>]+>", "", content)
    char_count = len(text_only.strip())

    if char_count < min_chars:
        raise ValueError(
            f"本文が短すぎます ({char_count}文字 < 最小{min_chars}文字)"
        )

    if char_count > max_chars:
        logger.warning(
            "本文が長すぎます (%d文字 > 最大%d文字)。切り詰めは行いません。",
            char_count,
            max_chars,
        )

    # --- 必須フィールド ---
    if not article.get("excerpt"):
        raise ValueError("リード文（excerpt）が空です")

    # --- HTML 基本検証 ---
    if "<" not in content:
        logger.warning("HTMLタグが検出されません。プレーンテキストとして続行。")

    # --- 免責事項の自動挿入 ---
    if include_disclaimer and "AI" in DISCLAIMER_JA:
        if "自動生成" not in content and "AI" not in content[-200:]:
            content += f"\n{DISCLAIMER_JA}"
            logger.info("免責事項を自動挿入しました")

    # --- 出典リンクの自動挿入 ---
    if include_source_link and source_url:
        if source_url not in content:
            source_block = (
                f'\n<p class="source-link">出典: '
                f'<a href="{source_url}" target="_blank" rel="noopener noreferrer">'
                f"元記事を読む</a></p>"
            )
            content += source_block
            logger.info("出典リンクを自動挿入しました")

    article["content"] = content
    logger.info("品質チェック通過: %s (%d文字)", title, char_count)
    return article
