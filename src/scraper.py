"""ニュース収集モジュール（httpx + BeautifulSoup4）"""

from __future__ import annotations

import asyncio
import logging
import random
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from . import database as db
from .config import load_settings, load_sources

logger = logging.getLogger(__name__)


def _get_user_agent(settings: dict) -> str:
    """User-Agent をランダムに選択"""
    agents = settings.get("scraper", {}).get("user_agents", ["AINAP/1.0"])
    return random.choice(agents)


def _check_robots_txt(base_url: str, path: str, user_agent: str) -> bool:
    """robots.txt でクロール許可されているか確認"""
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(user_agent, urljoin(base_url, path))
    except Exception:
        logger.warning("robots.txt の取得に失敗: %s（許可として続行）", robots_url)
        return True


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
    reraise=True,
)
async def _fetch_page(client: httpx.AsyncClient, url: str) -> str:
    """ページ取得（リトライ付き）"""
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.text


async def _fetch_article_body(
    client: httpx.AsyncClient,
    article_url: str,
    body_selector: str,
    max_chars: int,
    interval: tuple[int, int],
) -> str | None:
    """個別記事ページから本文を取得"""
    await asyncio.sleep(random.uniform(*interval))
    try:
        html = await _fetch_page(client, article_url)
        soup = BeautifulSoup(html, "lxml")
        paragraphs = soup.select(body_selector)
        if not paragraphs:
            return None
        body = "\n".join(p.get_text(strip=True) for p in paragraphs)
        return body[:max_chars]
    except Exception:
        logger.warning("本文取得失敗: %s", article_url, exc_info=True)
        return None


async def scrape_source(source: dict, settings: dict) -> list[dict]:
    """単一ソースからニュース記事を収集"""
    scraper_cfg = settings.get("scraper", {})
    user_agent = _get_user_agent(settings)
    interval = (
        scraper_cfg.get("request_interval_min", 3),
        scraper_cfg.get("request_interval_max", 8),
    )
    max_articles = scraper_cfg.get("max_articles_per_source", 5)
    max_chars = scraper_cfg.get("body_max_chars", 2000)
    respect_robots = scraper_cfg.get("respect_robots_txt", True)

    source_name = source["name"]
    source_url = source["url"]
    selectors = source["selectors"]
    category = source.get("category_map", "")

    if respect_robots:
        parsed = urlparse(source_url)
        if not _check_robots_txt(source_url, parsed.path, user_agent):
            logger.warning("robots.txt でブロックされています: %s", source_url)
            return []

    collected = []
    headers = {"User-Agent": user_agent}

    async with httpx.AsyncClient(
        headers=headers, timeout=30.0, follow_redirects=True
    ) as client:
        try:
            html = await _fetch_page(client, source_url)
        except Exception:
            logger.error("一覧ページ取得失敗: %s", source_url, exc_info=True)
            return []

        soup = BeautifulSoup(html, "lxml")
        articles = soup.select(selectors["article_list"])

        for article_el in articles[:max_articles]:
            # タイトル取得
            title_el = article_el.select_one(selectors["title"])
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not title:
                continue

            # リンク取得
            link_el = article_el.select_one(selectors["link"])
            if not link_el:
                continue
            href = link_el.get("href", "")
            if not href:
                continue
            article_url = urljoin(source_url, href)

            # 重複チェック
            if db.is_url_exists(article_url):
                logger.debug("重複スキップ: %s", article_url)
                continue

            # 日付取得
            date_el = article_el.select_one(selectors.get("date", ""))
            published_at = None
            if date_el:
                published_at = date_el.get("datetime") or date_el.get_text(strip=True)

            # 本文取得（個別ページにアクセス）
            body = await _fetch_article_body(
                client, article_url, selectors.get("body", "p"), max_chars, interval
            )

            # DB 保存
            try:
                article_id = db.save_article(
                    url=article_url,
                    title=title,
                    source_name=source_name,
                    body=body,
                    published_at=published_at,
                    category=category,
                )
                collected.append(
                    {
                        "id": article_id,
                        "url": article_url,
                        "title": title,
                        "source_name": source_name,
                        "body": body,
                        "category": category,
                    }
                )
                logger.info("収集完了: [%s] %s", source_name, title)
            except Exception:
                logger.warning("記事保存失敗: %s", article_url, exc_info=True)

            # リクエスト間隔
            await asyncio.sleep(random.uniform(*interval))

    return collected


async def scrape_all_sources() -> list[dict]:
    """全ソースからニュースを収集（順次処理でメモリ節約）"""
    settings = load_settings()
    sources = load_sources()
    all_collected = []

    for source in sources:
        logger.info("収集開始: %s", source["name"])
        try:
            articles = await scrape_source(source, settings)
            all_collected.extend(articles)
        except Exception:
            logger.error("ソース収集エラー: %s", source["name"], exc_info=True)

    logger.info("収集完了: 合計 %d 記事", len(all_collected))
    return all_collected
