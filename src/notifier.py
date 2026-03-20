"""Discord Webhook 通知モジュール"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error

from .config import get_env

logger = logging.getLogger(__name__)


def _send_discord(title: str, description: str, color: int = 0x00FF00) -> bool:
    """Discord Webhook でメッセージを送信"""
    try:
        webhook_url = get_env("DISCORD_WEBHOOK_URL")
    except EnvironmentError:
        logger.warning("DISCORD_WEBHOOK_URL が未設定のため通知をスキップ")
        return False

    embed = {
        "title": title,
        "description": description,
        "color": color,
    }
    payload = json.dumps({"embeds": [embed]}).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 204):
                logger.info("Discord 通知送信成功: %s", title)
                return True
            else:
                logger.error("Discord Webhook エラー: status=%s", resp.status)
                return False
    except urllib.error.URLError as e:
        logger.error("Discord Webhook 接続エラー: %s", e)
        return False
    except TimeoutError:
        logger.error("Discord Webhook タイムアウト")
        return False


def notify_success(title: str, wp_url: str, category: str = "") -> bool:
    """投稿成功通知"""
    description = (
        f"記事が投稿されました。\n\n"
        f"**タイトル**: {title}\n"
        f"**URL**: {wp_url}\n"
        f"**カテゴリー**: {category}\n"
    )
    return _send_discord("[AINAP] 投稿成功", description, color=0x00FF00)


def notify_error(error_type: str, error_msg: str, context: str = "") -> bool:
    """エラー通知"""
    description = (
        f"エラーが発生しました。\n\n"
        f"**種別**: {error_type}\n"
        f"**内容**: {error_msg}\n"
    )
    if context:
        description += f"**コンテキスト**: {context}\n"
    return _send_discord("[AINAP] エラー", description, color=0xFF0000)


def notify_dead_letter(article_title: str, error_msg: str) -> bool:
    """Dead Letter Queue 通知"""
    description = (
        f"5回以上失敗した記事をDead Letter Queueに移動しました。\n\n"
        f"**タイトル**: {article_title}\n"
        f"**最終エラー**: {error_msg}\n"
        f"手動確認が必要です。\n"
    )
    return _send_discord("[AINAP] Dead Letter Queue", description, color=0xFF8C00)


def notify_pipeline_complete(
    collected: int, published: int, failed: int, retried: int
) -> bool:
    """パイプライン完了通知（毎回送信）"""
    description = (
        f"**収集**: {collected} 件\n"
        f"**投稿**: {published} 件\n"
        f"**失敗**: {failed} 件\n"
        f"**リトライ**: {retried} 件\n"
    )
    if published > 0:
        color = 0x00FF00  # green
    elif failed > 0:
        color = 0xFF8C00  # orange
    else:
        color = 0x95A5A6  # gray
    return _send_discord("[AINAP] パイプライン完了", description, color=color)


def send_daily_summary(posts: list[dict]) -> bool:
    """日次サマリー"""
    if not posts:
        description = "本日の投稿はありませんでした。"
    else:
        description = f"本日の投稿: **{len(posts)}件**\n\n"
        total_tokens_in = 0
        total_tokens_out = 0
        for i, post in enumerate(posts, 1):
            description += f"**{i}. {post.get('title', 'N/A')}**\n"
            description += f"   URL: {post.get('wp_url', 'N/A')}\n"
            tokens_in = post.get("tokens_in", 0)
            tokens_out = post.get("tokens_out", 0)
            description += f"   Tokens: {tokens_in} in / {tokens_out} out\n\n"
            total_tokens_in += tokens_in
            total_tokens_out += tokens_out
        description += f"**合計トークン**: {total_tokens_in} in / {total_tokens_out} out"
    return _send_discord("[AINAP] 日次サマリー", description, color=0x3498DB)
