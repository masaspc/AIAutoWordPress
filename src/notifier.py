"""メール通知モジュール（msmtp経由）"""

from __future__ import annotations

import logging
import subprocess
from email.mime.text import MIMEText

from .config import get_env

logger = logging.getLogger(__name__)


def _send_mail(subject: str, body: str) -> bool:
    """msmtp でメール送信"""
    try:
        notify_email = get_env("NOTIFY_EMAIL")
        smtp_user = get_env("SMTP_USER")
    except EnvironmentError:
        logger.warning("メール設定が未構成のため通知をスキップ")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = notify_email

    try:
        proc = subprocess.run(
            ["msmtp", "-t"],
            input=msg.as_string(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            logger.info("メール送信成功: %s", subject)
            return True
        else:
            logger.error("msmtp エラー: %s", proc.stderr)
            return False
    except FileNotFoundError:
        logger.warning("msmtp が見つかりません。メール通知をスキップ。")
        return False
    except subprocess.TimeoutExpired:
        logger.error("msmtp タイムアウト")
        return False


def notify_success(title: str, wp_url: str, category: str = "") -> bool:
    """投稿成功通知"""
    subject = f"[AINAP] 投稿成功: {title}"
    body = (
        f"記事が投稿されました。\n\n"
        f"タイトル: {title}\n"
        f"URL: {wp_url}\n"
        f"カテゴリー: {category}\n"
    )
    return _send_mail(subject, body)


def notify_error(error_type: str, error_msg: str, context: str = "") -> bool:
    """エラー通知"""
    subject = f"[AINAP] エラー: {error_type}"
    body = (
        f"エラーが発生しました。\n\n"
        f"種別: {error_type}\n"
        f"内容: {error_msg}\n"
    )
    if context:
        body += f"コンテキスト: {context}\n"
    return _send_mail(subject, body)


def notify_dead_letter(article_title: str, error_msg: str) -> bool:
    """Dead Letter Queue 通知"""
    subject = f"[AINAP] Dead Letter Queue: {article_title}"
    body = (
        f"5回以上失敗した記事をDead Letter Queueに移動しました。\n\n"
        f"タイトル: {article_title}\n"
        f"最終エラー: {error_msg}\n"
        f"手動確認が必要です。\n"
    )
    return _send_mail(subject, body)


def send_daily_summary(posts: list[dict]) -> bool:
    """日次サマリーメール"""
    subject = "[AINAP] 日次サマリー"
    if not posts:
        body = "本日の投稿はありませんでした。\n"
    else:
        body = f"本日の投稿: {len(posts)}件\n\n"
        total_tokens_in = 0
        total_tokens_out = 0
        for i, post in enumerate(posts, 1):
            body += f"{i}. {post.get('title', 'N/A')}\n"
            body += f"   URL: {post.get('wp_url', 'N/A')}\n"
            tokens_in = post.get("tokens_in", 0)
            tokens_out = post.get("tokens_out", 0)
            body += f"   Tokens: {tokens_in} in / {tokens_out} out\n\n"
            total_tokens_in += tokens_in
            total_tokens_out += tokens_out
        body += f"合計トークン: {total_tokens_in} in / {total_tokens_out} out\n"
    return _send_mail(subject, body)
