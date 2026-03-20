"""Claude API 記事生成モジュール"""

from __future__ import annotations

import json
import logging
import re

import anthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_env, load_prompt_template, load_settings

logger = logging.getLogger(__name__)


def _build_prompt(article: dict) -> str:
    """プロンプトテンプレートに変数を埋め込む"""
    template = load_prompt_template()
    return template.format(
        title=article.get("title", ""),
        source_name=article.get("source_name", ""),
        url=article.get("url", ""),
        body=article.get("body", "") or "",
    )


def _parse_json_response(text: str) -> dict:
    """Claude のレスポンスから JSON を抽出してパースする"""
    # ```json ... ``` ブロックを探す
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    # JSON ブロックがない場合、テキスト全体から { ... } を探す
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    raise ValueError("レスポンスから JSON を抽出できません")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=10, min=10, max=60),
    retry=retry_if_exception_type(
        (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError)
    ),
    reraise=True,
)
def _call_claude(prompt: str, settings: dict) -> tuple[str, int, int]:
    """Claude API を呼び出し、レスポンステキストとトークン数を返す"""
    claude_cfg = settings.get("claude", {})
    api_key = get_env("ANTHROPIC_API_KEY")

    client = anthropic.Anthropic(api_key=api_key)

    # ストリーミングでメモリ消費を抑制
    collected_text = []
    tokens_in = 0
    tokens_out = 0

    with client.messages.stream(
        model=claude_cfg.get("model", "claude-sonnet-4-20250514"),
        max_tokens=claude_cfg.get("max_tokens", 4096),
        temperature=claude_cfg.get("temperature", 0.7),
        messages=[{"role": "user", "content": prompt}],
        timeout=claude_cfg.get("timeout_sec", 60),
    ) as stream:
        for text in stream.text_stream:
            collected_text.append(text)

    response = stream.get_final_message()
    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens

    return "".join(collected_text), tokens_in, tokens_out


def generate_article(article: dict) -> dict:
    """収集記事から日本語ブログ記事を生成

    Args:
        article: DB から取得した記事データ（id, url, title, source_name, body, category）

    Returns:
        生成結果の辞書:
        {
            "title": str,
            "excerpt": str,
            "content": str (HTML),
            "tags": list[str],
            "slug": str,
            "tokens_in": int,
            "tokens_out": int,
        }
    """
    settings = load_settings()
    prompt = _build_prompt(article)

    logger.info("記事生成開始: %s", article.get("title", ""))

    response_text, tokens_in, tokens_out = _call_claude(prompt, settings)

    logger.info(
        "Claude API 応答完了 (input=%d tokens, output=%d tokens)",
        tokens_in,
        tokens_out,
    )

    result = _parse_json_response(response_text)

    # 必須フィールドの検証
    required = ["title", "content"]
    for field in required:
        if field not in result or not result[field]:
            raise ValueError(f"生成結果に必須フィールド '{field}' がありません")

    result["tokens_in"] = tokens_in
    result["tokens_out"] = tokens_out

    # excerpt が無い場合はタイトルを使用
    if not result.get("excerpt"):
        result["excerpt"] = result["title"]

    # tags が無い場合は空リスト
    if not result.get("tags"):
        result["tags"] = []

    # slug が無い場合は空文字
    if not result.get("slug"):
        result["slug"] = ""

    logger.info("記事生成完了: %s", result["title"])
    return result
