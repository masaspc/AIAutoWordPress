"""SQLite データベース操作モジュール"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .config import BASE_DIR

DB_PATH = BASE_DIR / "data" / "ainap.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash     TEXT UNIQUE NOT NULL,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    source_name  TEXT NOT NULL,
    body         TEXT,
    published_at TEXT,
    category     TEXT,
    thumbnail_url TEXT,
    collected_at TEXT DEFAULT (datetime('now')),
    status       TEXT DEFAULT 'collected'
);

CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER REFERENCES articles(id),
    wp_post_id   INTEGER,
    wp_url       TEXT,
    title        TEXT NOT NULL,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    published_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS failed_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER REFERENCES articles(id),
    error_type   TEXT NOT NULL,
    error_msg    TEXT,
    retry_count  INTEGER DEFAULT 0,
    next_retry   TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_url_hash ON articles(url_hash);
CREATE INDEX IF NOT EXISTS idx_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_collected ON articles(collected_at);
"""


@contextmanager
def get_connection():
    """SQLite コネクションのコンテキストマネージャ"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """データベースとテーブルを初期化"""
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)


def url_hash(url: str) -> str:
    """URL の SHA-256 ハッシュを返す"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def is_url_exists(url: str) -> bool:
    """URL が既に収集済みか判定"""
    h = url_hash(url)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM articles WHERE url_hash = ?", (h,)
        ).fetchone()
    return row is not None


def save_article(
    url: str,
    title: str,
    source_name: str,
    body: str | None = None,
    published_at: str | None = None,
    category: str | None = None,
    thumbnail_url: str | None = None,
) -> int:
    """収集記事を保存し、article_id を返す"""
    h = url_hash(url)
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO articles
               (url_hash, url, title, source_name, body, published_at, category, thumbnail_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (h, url, title, source_name, body, published_at, category, thumbnail_url),
        )
        return cur.lastrowid


def update_article_status(article_id: int, status: str) -> None:
    """記事ステータスを更新（collected / generated / published / failed）"""
    with get_connection() as conn:
        conn.execute(
            "UPDATE articles SET status = ? WHERE id = ?", (status, article_id)
        )


def get_unprocessed_articles(limit: int = 1) -> list[dict]:
    """未処理（collected）の記事を取得"""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, url, title, source_name, body, category
               FROM articles WHERE status = 'collected'
               ORDER BY collected_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_post(
    article_id: int,
    wp_post_id: int,
    wp_url: str,
    title: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> int:
    """投稿記録を保存"""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO posts
               (article_id, wp_post_id, wp_url, title, tokens_in, tokens_out)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (article_id, wp_post_id, wp_url, title, tokens_in, tokens_out),
        )
        return cur.lastrowid


def enqueue_failed(article_id: int, error_type: str, error_msg: str) -> None:
    """失敗キューに追加、またはリトライカウントを増加"""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id, retry_count FROM failed_queue WHERE article_id = ? AND error_type = ?",
            (article_id, error_type),
        ).fetchone()

        if existing:
            new_count = existing["retry_count"] + 1
            next_retry = (
                datetime.utcnow() + timedelta(minutes=5 * new_count)
            ).isoformat()
            conn.execute(
                "UPDATE failed_queue SET retry_count = ?, next_retry = ?, error_msg = ? WHERE id = ?",
                (new_count, next_retry, error_msg, existing["id"]),
            )
        else:
            next_retry = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
            conn.execute(
                """INSERT INTO failed_queue
                   (article_id, error_type, error_msg, retry_count, next_retry)
                   VALUES (?, ?, ?, 1, ?)""",
                (article_id, error_type, error_msg, next_retry),
            )


def get_retry_queue() -> list[dict]:
    """リトライ対象（retry_count < 5 かつ next_retry が現在時刻以前）を取得

    投稿済み（published）の記事は除外する。
    """
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT fq.id AS queue_id, fq.article_id, fq.error_type, fq.retry_count,
                      a.url, a.title, a.source_name, a.body, a.category
               FROM failed_queue fq
               JOIN articles a ON a.id = fq.article_id
               WHERE fq.retry_count < 5 AND fq.next_retry <= ?
                 AND a.status NOT IN ('published', 'skipped_similar')
               ORDER BY fq.next_retry ASC""",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def move_to_dead_letter(queue_id: int) -> None:
    """5回以上失敗したエントリを Dead Letter 扱いにする（retry_count を -1 にマーク）"""
    with get_connection() as conn:
        conn.execute(
            "UPDATE failed_queue SET retry_count = -1 WHERE id = ?", (queue_id,)
        )


def remove_from_queue(queue_id: int) -> None:
    """成功時にキューからエントリを削除"""
    with get_connection() as conn:
        conn.execute("DELETE FROM failed_queue WHERE id = ?", (queue_id,))


def get_today_posts() -> list[dict]:
    """本日の投稿一覧を取得（日次サマリー用）"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT title, wp_url, tokens_in, tokens_out
               FROM posts WHERE published_at LIKE ?
               ORDER BY published_at ASC""",
            (f"{today}%",),
        ).fetchall()
    return [dict(r) for r in rows]


def _extract_keywords(text: str) -> set[str]:
    """タイトルからキーワードを抽出（ストップワード除去）"""
    # 英数字・日本語の単語を抽出（2文字以上）
    words = set(re.findall(r"[a-zA-Z]{2,}|[\u3040-\u9fff]{2,}", text.lower()))
    # 一般的なストップワードを除去
    stop = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "has",
            "have", "will", "can", "about", "into", "its", "new", "how", "what",
            "する", "ある", "いる", "なる", "できる", "について", "における", "として"}
    return words - stop


def is_similar_title_exists(title: str, days: int = 7, threshold: float = 0.4) -> bool:
    """過去の投稿タイトルと類似するものがあるか判定

    キーワードの重複率が threshold 以上なら類似とみなす。
    posts テーブル（生成済み日本語タイトル）と articles テーブル（英語原題）の
    両方を比較対象にすることで、言語をまたぐ重複も検出する。
    """
    keywords = _extract_keywords(title)
    if not keywords:
        return False

    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        # 生成済みタイトル（日本語）との比較
        post_rows = conn.execute(
            "SELECT title FROM posts WHERE published_at >= ?", (since,)
        ).fetchall()
        # 投稿済み記事の原題（英語）との比較
        article_rows = conn.execute(
            "SELECT title FROM articles WHERE status IN ('published', 'generated') "
            "AND collected_at >= ?",
            (since,),
        ).fetchall()

    all_titles = [r["title"] for r in post_rows] + [r["title"] for r in article_rows]
    for existing_title in all_titles:
        existing_kw = _extract_keywords(existing_title)
        if not existing_kw:
            continue
        overlap = len(keywords & existing_kw) / max(len(keywords), len(existing_kw))
        if overlap >= threshold:
            return True
    return False


def get_dead_letter_entries() -> list[dict]:
    """Dead Letter Queue のエントリを取得"""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT fq.id AS queue_id, fq.article_id, fq.error_type, fq.error_msg,
                      a.url, a.title
               FROM failed_queue fq
               JOIN articles a ON a.id = fq.article_id
               WHERE fq.retry_count >= 5""",
        ).fetchall()
    return [dict(r) for r in rows]
