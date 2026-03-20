# AINAP デプロイガイド

ConoHa VPS 512MB / Ubuntu 24.04 LTS への詳細デプロイ手順

---

## 前提条件

### 用意するもの

| 項目 | 説明 | 取得方法 |
|------|------|----------|
| ConoHa VPS | 512MBプラン / Ubuntu 24.04 LTS | ConoHa管理画面でVPS追加 |
| Anthropic API Key | `sk-ant-api03-...` 形式 | https://console.anthropic.com/settings/keys |
| WordPress サイト | HTTPS有効・REST API有効 | 自前 or レンタルサーバー |
| WP Application Password | WordPress認証用 | WP管理画面 → ユーザー → アプリケーションパスワード |
| Gmail アプリパスワード | SMTP通知用（任意） | Google アカウント → セキュリティ → アプリパスワード |

---

## Step 1: ConoHa VPS の初期設定

### 1-1. VPS作成

1. ConoHa管理画面 → 「サーバー追加」
2. **リージョン**: 東京
3. **プラン**: 512MB（1コア / SSD 30GB）
4. **イメージ**: Ubuntu 24.04 LTS
5. **rootパスワード**: 安全なパスワードを設定
6. **SSH Key**: 登録済みの公開鍵を選択（推奨）

### 1-2. SSH ログイン

```bash
# ローカルPCから接続
ssh root@<VPSのIPアドレス>
```

### 1-3. 初期セキュリティ設定

```bash
# システム更新
apt update && apt upgrade -y

# タイムゾーンを日本に設定
timedatectl set-timezone Asia/Tokyo

# 不要サービスを無効化（メモリ節約）
systemctl disable --now snapd.service snapd.socket 2>/dev/null || true
systemctl disable --now multipathd.service 2>/dev/null || true

# swap無効確認（512MBプランでは最初からswap無し）
free -m
```

### 1-4. ファイアウォール設定

```bash
# ufw をインストール・有効化
apt install -y ufw

# SSHを許可（先にこれをやらないとロックアウトされます！）
ufw allow ssh

# アウトバウンドHTTPS（WordPress投稿・API通信用）は既定で許可

# ファイアウォール有効化
ufw enable

# 状態確認
ufw status
```

---

## Step 2: GitHub からアプリケーションをダウンロード

### 2-1. Git インストール確認

```bash
# Ubuntu 24.04 には git がプリインストールされているが念のため
apt install -y git
```

### 2-2. リポジトリをクローン

```bash
# /opt/ainap にクローン（後ほど install.sh が使用するパス）
git clone https://github.com/masaspc/AIAutoWordPress.git /tmp/ainap-src

# 確認
ls /tmp/ainap-src/
# => src/ config/ systemd/ tests/ install.sh pyproject.toml ...
```

### 2-3. インストールスクリプトを実行

```bash
# install.sh に実行権限を付与して実行
# 引数にリポジトリURLを渡すと再クローン時に使用
cd /tmp/ainap-src
chmod +x install.sh
sudo bash install.sh https://github.com/masaspc/AIAutoWordPress.git
```

> **注**: install.sh は以下を自動実行します:
> - python3-venv, msmtp, git のインストール
> - `ainap` 専用ユーザーの作成
> - `/opt/ainap` へのデプロイ
> - Python仮想環境のセットアップ
> - 対話形式での環境変数入力
> - systemd timer の登録・有効化

---

## Step 3: 対話入力（install.sh 実行中）

install.sh が以下の情報を順番に聞いてきます。事前に準備しておいてください。

### 3-1. Anthropic API Key

```
Anthropic API Key (sk-ant-api03-...): sk-ant-api03-xxxxxxxxxx
```

### 3-2. WordPress 設定

```
WordPress サイトURL (https://example.com): https://your-wordpress-site.com
WordPress ユーザー名: ainap-bot
WordPress Application Password: xxxx xxxx xxxx xxxx xxxx xxxx
```

> **WordPress側の事前準備**（Step 3の前に実施）:
>
> 1. WP管理画面 → ユーザー → 新規追加
>    - ユーザー名: `ainap-bot`
>    - 権限グループ: **投稿者**（管理者にしない）
> 2. ainap-bot でログイン → プロフィール
>    - 「アプリケーションパスワード」セクション
>    - 名前: `AINAP` → 「新しいアプリケーションパスワードを追加」
>    - 表示されたパスワードをコピー（一度しか表示されません）
> 3. REST API 動作確認:
>    ```bash
>    curl -s https://your-site.com/wp-json/wp/v2/ | head -c 200
>    ```

### 3-3. メール通知設定（Gmail の場合）

```
SMTP ホスト [smtp.gmail.com]: (Enterでデフォルト)
SMTP ポート [587]: (Enterでデフォルト)
SMTP ユーザー (メールアドレス): your-email@gmail.com
SMTP パスワード (アプリパスワード推奨): xxxx xxxx xxxx xxxx
通知先メールアドレス: your-email@gmail.com
```

> **Gmail アプリパスワードの取得方法**:
> 1. https://myaccount.google.com/security
> 2. 「2段階認証」を有効化（まだの場合）
> 3. 「アプリ パスワード」→ アプリ名: `AINAP` → 生成
> 4. 表示された16文字のパスワードを入力

---

## Step 4: 動作確認

### 4-1. systemd timer の状態確認

```bash
# タイマーが登録されているか確認
sudo systemctl status ainap.timer

# 出力例:
# ● ainap.timer - AINAP scheduled execution
#      Loaded: loaded (/etc/systemd/system/ainap.timer; enabled)
#      Active: active (waiting)
#     Trigger: Thu 2026-03-21 07:00:00 JST
```

### 4-2. 手動テスト実行

```bash
# 即時実行
sudo systemctl start ainap.service

# リアルタイムでログを確認
sudo journalctl -u ainap.service -f
```

### 4-3. ログ確認

```bash
# アプリケーションログ
sudo cat /opt/ainap/logs/ainap.log

# systemd ジャーナル
sudo journalctl -u ainap.service --since today
```

### 4-4. テスト実行（ユニットテスト）

```bash
sudo -u ainap bash -c 'cd /opt/ainap && venv/bin/python -m pytest tests/ -v'
```

---

## Step 5: WordPress 側の追加設定

### 5-1. カテゴリー作成

WP管理画面 → 投稿 → カテゴリー で以下を作成:

| カテゴリー名 | スラッグ |
|---|---|
| AIニュース | ai-news |
| テックニュース | tech-news |

### 5-2. カスタムフィールド登録（任意）

`functions.php` に追加（テーマのfunctions.phpまたはプラグイン経由）:

```php
// AI生成フラグ・元記事URLをREST APIで更新可能にする
register_meta('post', 'ai_generated', [
    'show_in_rest' => true,
    'single'       => true,
    'type'         => 'boolean',
]);
register_meta('post', 'source_url', [
    'show_in_rest' => true,
    'single'       => true,
    'type'         => 'string',
]);
```

---

## 運用コマンド集

### 基本操作

```bash
# 即時実行
sudo systemctl start ainap.service

# タイマー停止（一時停止）
sudo systemctl stop ainap.timer

# タイマー再開
sudo systemctl start ainap.timer

# タイマー無効化（再起動後も停止したまま）
sudo systemctl disable ainap.timer
```

### ログ確認

```bash
# 直近のログ
sudo journalctl -u ainap.service -n 50

# 本日のログ
sudo journalctl -u ainap.service --since today

# エラーのみ
sudo journalctl -u ainap.service -p err

# リアルタイム監視
sudo journalctl -u ainap.service -f
```

### データベース確認

```bash
# 収集記事の一覧
sudo -u ainap sqlite3 /opt/ainap/data/ainap.db \
  "SELECT id, title, status, collected_at FROM articles ORDER BY id DESC LIMIT 10;"

# 投稿履歴
sudo -u ainap sqlite3 /opt/ainap/data/ainap.db \
  "SELECT title, wp_url, tokens_in, tokens_out, published_at FROM posts ORDER BY id DESC LIMIT 10;"

# 失敗キュー
sudo -u ainap sqlite3 /opt/ainap/data/ainap.db \
  "SELECT * FROM failed_queue WHERE retry_count >= 0;"

# 統計
sudo -u ainap sqlite3 /opt/ainap/data/ainap.db \
  "SELECT status, COUNT(*) FROM articles GROUP BY status;"
```

### メモリ使用量確認

```bash
# VPS全体
free -m

# AINAP実行中のメモリ（実行中に実行）
sudo systemctl show ainap.service --property=MemoryCurrent
```

### アップデート

```bash
# 最新版を取得
cd /opt/ainap
sudo -u ainap git pull origin main

# 依存パッケージ更新
sudo -u ainap bash -c 'cd /opt/ainap && venv/bin/pip install -e . -q'

# systemdユニット更新（変更があった場合）
sudo cp /opt/ainap/systemd/ainap.service /etc/systemd/system/
sudo cp /opt/ainap/systemd/ainap.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

### 設定変更

```bash
# スクレイピング対象の追加・変更
sudo -u ainap vim /opt/ainap/config/sources.yaml

# Claude モデル・投稿設定の変更
sudo -u ainap vim /opt/ainap/config/settings.yaml

# APIキー等の変更
sudo -u ainap vim /opt/ainap/.env

# プロンプトの調整
sudo -u ainap vim /opt/ainap/config/prompts/article_gen.txt
```

---

## トラブルシューティング

### 症状: サービスが起動しない

```bash
# 詳細ログ
sudo journalctl -u ainap.service -n 100 --no-pager

# .env の権限確認
ls -la /opt/ainap/.env
# => -rw------- 1 ainap ainap ... .env （600であること）
```

### 症状: メモリ不足 (OOM Kill)

```bash
# OOM確認
sudo dmesg | grep -i oom

# 不要プロセスの確認
ps aux --sort=-rss | head -10

# swap追加（緊急措置）
sudo fallocate -l 256M /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### 症状: WordPress 投稿失敗

```bash
# WP REST API 接続テスト
curl -s -o /dev/null -w "%{http_code}" \
  -u "ainap-bot:YOUR_APP_PASSWORD" \
  https://your-site.com/wp-json/wp/v2/posts?per_page=1

# 200 → OK / 401 → 認証エラー / 000 → 接続不可
```

### 症状: スクレイピングが0件

```bash
# 手動でソースサイトにアクセスできるか確認
curl -s -o /dev/null -w "%{http_code}" \
  -A "AINAP/1.0" \
  https://techcrunch.com/category/artificial-intelligence/

# CSSセレクタが変わっている場合は sources.yaml を更新
```

### 完全リセット

```bash
# DBとキューをクリア（記事データがすべて消えます）
sudo -u ainap rm -f /opt/ainap/data/ainap.db
sudo -u ainap rm -f /opt/ainap/data/queue/*.json

# 次回実行時にDBが自動再作成されます
```

---

## セキュリティチェックリスト

- [ ] SSH ポートをデフォルト(22)から変更 or fail2ban を導入
- [ ] root の SSH パスワードログインを無効化
- [ ] `.env` が `chmod 600` / 所有者 `ainap` であること
- [ ] WordPress の `ainap-bot` ユーザーが「投稿者」権限のみであること
- [ ] WordPress が HTTPS 有効であること
- [ ] ufw でインバウンドは SSH のみ許可

---

## 月額コスト概算

| 項目 | 月額 |
|------|------|
| ConoHa VPS 512MB（まとめトク1ヶ月） | ¥460 |
| Claude API (Sonnet, ~150記事/月) | ¥2,200〜3,700 ($15〜25) |
| Gmail SMTP | ¥0 |
| **合計** | **¥2,700〜4,200** |
