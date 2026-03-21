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
    """Claude のレスポンスから JSON を抽出してパースする（途中切れにも対応）"""
    # ```json ... ``` ブロックを探す
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # JSON ブロックがない場合、テキスト全体から { ... } を探す
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # JSON が途中で切れている場合の修復を試みる
    match = re.search(r"\{.*", text, re.DOTALL)
    if match:
        return _repair_truncated_json(match.group(0))

    raise ValueError("レスポンスから JSON を抽出できません")


def _repair_truncated_json(raw: str) -> dict:
    """途中切れの JSON を修復する（max_tokens 到達時の対策）"""
    logger.warning("JSON が途中切れ: 修復を試みます (%d chars)", len(raw))

    # 末尾のバッククォートを除去
    raw = re.sub(r"```\s*$", "", raw)

    # content フィールドの途中で切れている場合が最も多い
    # 閉じられていない文字列リテラルを閉じる
    repaired = raw.rstrip()

    # 末尾の不完全なキー/値を除去して閉じる
    # 例: ..."content": "...<p>途中    → ..."content": "..."
    # 閉じていない文字列があるか確認
    try:
        json.loads(repaired)
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # 戦略: 末尾から不完全な部分を除去して閉じていく
    # 1. 閉じていない文字列リテラルを閉じる
    # 2. 閉じていない配列 [] を閉じる
    # 3. 閉じていないオブジェクト {} を閉じる

    # まず未閉じの文字列をエスケープして閉じる
    # content 内の HTML にダブルクォートが含まれるので注意
    # 最後の完全な key-value ペアの後ろで切る方が安全
    for trim_len in range(min(2000, len(repaired)), 0, -1):
        candidate = repaired[:len(repaired) - trim_len]
        # 最後のカンマまたは完全な値の後で切る
        # 最後の ", を見つける
        last_comma = candidate.rfind(",")
        last_brace = candidate.rfind("}")
        cut_pos = max(last_comma, last_brace)
        if cut_pos <= 0:
            continue

        fragment = candidate[:cut_pos]
        # 閉じ括弧を補完
        open_braces = fragment.count("{") - fragment.count("}")
        open_brackets = fragment.count("[") - fragment.count("]")
        suffix = "]" * max(0, open_brackets) + "}" * max(0, open_braces)
        try:
            result = json.loads(fragment + suffix)
            logger.info("JSON 修復成功（%d文字切り捨て）", len(repaired) - cut_pos)
            return result
        except json.JSONDecodeError:
            continue

    raise ValueError("途中切れ JSON の修復に失敗しました")


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
    stop_reason = response.stop_reason

    if stop_reason == "max_tokens":
        logger.warning(
            "Claude 応答が max_tokens で切れました (output=%d tokens)。JSON 修復を試みます。",
            tokens_out,
        )

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

    # image_keywords が無い場合はデフォルト
    if not result.get("image_keywords"):
        result["image_keywords"] = ["AI", "technology"]

    logger.info("記事生成完了: %s", result["title"])
    return result
