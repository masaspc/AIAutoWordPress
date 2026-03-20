.PHONY: setup test run dry-run lint clean

# Python 仮想環境
VENV := venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

setup: ## 開発環境セットアップ
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

test: ## テスト実行
	$(PYTHON) -m pytest tests/ -v

run: ## パイプライン実行
	$(PYTHON) -m src.main

clean: ## キャッシュ削除
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

help: ## ヘルプ表示
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'
