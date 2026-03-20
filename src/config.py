"""設定読み込みモジュール"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

# プロジェクトルート（src/ の親ディレクトリ）
BASE_DIR = Path(__file__).resolve().parent.parent

# .env 読み込み
load_dotenv(BASE_DIR / ".env")


def load_settings() -> dict:
    """config/settings.yaml を読み込んで辞書で返す"""
    path = BASE_DIR / "config" / "settings.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_sources() -> list[dict]:
    """config/sources.yaml から有効なソース一覧を返す"""
    path = BASE_DIR / "config" / "sources.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [s for s in data.get("sources", []) if s.get("enabled", True)]


def load_prompt_template() -> str:
    """config/prompts/article_gen.txt のテンプレートを返す"""
    path = BASE_DIR / "config" / "prompts" / "article_gen.txt"
    with open(path, encoding="utf-8") as f:
        return f.read()


def get_env(key: str, default: str | None = None) -> str:
    """環境変数を取得。未設定時はデフォルト値か例外を発生"""
    value = os.environ.get(key, default)
    if value is None:
        raise EnvironmentError(f"Required environment variable '{key}' is not set")
    return value
