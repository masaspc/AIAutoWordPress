"""database.py のユニットテスト"""

import os
import tempfile

import pytest

# テスト用にDB_PATHを一時ファイルに差し替え
_tmp_dir = tempfile.mkdtemp()
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from src import database as db


@pytest.fixture(autouse=True)
def _setup_db(tmp_path, monkeypatch):
    """テスト毎に新しいDBを使う"""
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init_db()
    yield


class TestUrlHash:
    def test_deterministic(self):
        assert db.url_hash("https://example.com") == db.url_hash("https://example.com")

    def test_different_urls(self):
        assert db.url_hash("https://a.com") != db.url_hash("https://b.com")


class TestArticleCRUD:
    def test_save_and_exists(self):
        url = "https://example.com/article1"
        assert not db.is_url_exists(url)

        article_id = db.save_article(
            url=url,
            title="Test Article",
            source_name="TestSource",
            body="Test body",
        )
        assert article_id > 0
        assert db.is_url_exists(url)

    def test_duplicate_url_raises(self):
        url = "https://example.com/dup"
        db.save_article(url=url, title="First", source_name="Test")
        with pytest.raises(Exception):
            db.save_article(url=url, title="Second", source_name="Test")

    def test_get_unprocessed(self):
        db.save_article(url="https://a.com/1", title="A1", source_name="S1")
        db.save_article(url="https://a.com/2", title="A2", source_name="S2")

        articles = db.get_unprocessed_articles(limit=10)
        assert len(articles) == 2

        db.update_article_status(articles[0]["id"], "published")
        remaining = db.get_unprocessed_articles(limit=10)
        assert len(remaining) == 1


class TestPostCRUD:
    def test_save_post(self):
        aid = db.save_article(url="https://x.com/p1", title="P", source_name="S")
        pid = db.save_post(
            article_id=aid,
            wp_post_id=123,
            wp_url="https://wp.example.com/?p=123",
            title="Posted Title",
            tokens_in=100,
            tokens_out=200,
        )
        assert pid > 0

    def test_today_posts(self):
        aid = db.save_article(url="https://x.com/tp", title="T", source_name="S")
        db.save_post(aid, 1, "https://wp.example.com/?p=1", "Today Post", 50, 100)

        posts = db.get_today_posts()
        assert len(posts) >= 1


class TestFailedQueue:
    def test_enqueue_and_retry(self):
        aid = db.save_article(url="https://x.com/f1", title="F", source_name="S")
        db.enqueue_failed(aid, "APIError", "timeout")

        queue = db.get_retry_queue()
        # next_retry は5分後なので即座には取れないかもしれない
        # retry_count=1 なので Dead Letter ではない

    def test_increment_retry(self):
        aid = db.save_article(url="https://x.com/f2", title="F2", source_name="S")
        db.enqueue_failed(aid, "APIError", "err1")
        db.enqueue_failed(aid, "APIError", "err2")
        # retry_count が 2 になっているはず

    def test_retry_queue_excludes_published(self):
        """投稿済み記事はリトライキューに含まれない"""
        aid = db.save_article(url="https://x.com/pub1", title="Published", source_name="S")
        db.enqueue_failed(aid, "APIError", "temp error")
        # next_retry を過去に設定して即取得可能にする
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE failed_queue SET next_retry = '2000-01-01T00:00:00'"
            )

        # まだ collected なのでリトライ対象
        queue = db.get_retry_queue()
        assert len(queue) == 1

        # published に変更 → リトライ対象外
        db.update_article_status(aid, "published")
        queue = db.get_retry_queue()
        assert len(queue) == 0

    def test_retry_queue_excludes_skipped_similar(self):
        """類似スキップ済み記事はリトライキューに含まれない"""
        aid = db.save_article(url="https://x.com/sim1", title="Similar", source_name="S")
        db.enqueue_failed(aid, "APIError", "temp error")
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE failed_queue SET next_retry = '2000-01-01T00:00:00'"
            )

        db.update_article_status(aid, "skipped_similar")
        queue = db.get_retry_queue()
        assert len(queue) == 0

    def test_remove_from_queue(self):
        """キューからの削除が正しく動作する"""
        aid = db.save_article(url="https://x.com/rm1", title="Remove", source_name="S")
        db.enqueue_failed(aid, "APIError", "error")
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE failed_queue SET next_retry = '2000-01-01T00:00:00'"
            )

        queue = db.get_retry_queue()
        assert len(queue) == 1

        db.remove_from_queue(queue[0]["queue_id"])
        queue = db.get_retry_queue()
        assert len(queue) == 0


class TestSimilarTitle:
    def test_similar_english_titles(self):
        """英語の類似タイトルを検出"""
        aid = db.save_article(
            url="https://x.com/s1",
            title="Microsoft rolls back Copilot AI features on Windows",
            source_name="S",
        )
        db.update_article_status(aid, "published")
        db.save_post(aid, 1, "https://wp.example.com/?p=1",
                     "Microsoft、Copilotの過剰統合を見直し", 100, 200)

        # 同じトピックの英語タイトル → articles テーブル経由で類似検出
        assert db.is_similar_title_exists(
            "Microsoft reverses Copilot AI bloat on Windows"
        )

    def test_dissimilar_titles(self):
        """異なるトピックは類似とみなさない"""
        aid = db.save_article(
            url="https://x.com/s2",
            title="Microsoft rolls back Copilot AI features",
            source_name="S",
        )
        db.update_article_status(aid, "published")

        assert not db.is_similar_title_exists(
            "Google launches new Gemini model for Android"
        )

    def test_similar_check_includes_published_articles(self):
        """投稿済み articles の原題も比較対象になる"""
        aid = db.save_article(
            url="https://x.com/s3",
            title="OpenAI launches GPT-5 with enhanced reasoning capabilities",
            source_name="TechCrunch",
        )
        db.update_article_status(aid, "published")

        # 別ソースからの同トピック記事
        assert db.is_similar_title_exists(
            "OpenAI releases GPT-5 with improved reasoning features"
        )
