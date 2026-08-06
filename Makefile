# SKDY RAG Server — 统一开发 / 启动 / 验收入口（M4 收口）
#
# 常用：
#   make install        # 一次性装依赖（含 local 可选段）
#   make api            # 只启动 Web API（127.0.0.1:8766）
#   make web            # 只启动 Next.js 前端（http://localhost:3000）
#   make mcp            # 只启动 MCP Streamable HTTP（0.0.0.0:8765/mcp）
#   make dev            # 同时启动 MCP + Web API + 前端
#   make dashboard      # 启动 Streamlit 内部调试面板
#   make test           # 全量测试（unit + integration + contract）
#   make test-e2e       # 端到端 CLI 测试
#   make smoke          # 本地冒烟（ingest → query → trace）
#   make benchmark      # 查询延迟性能基线（需后端在跑 + embedding 可达）
#   make docker-up      # 可选容器部署（后端）
#   make docker-down    # 停止容器

SHELL := /bin/bash

.PHONY: install mcp api web dev dashboard test test-e2e smoke benchmark gen-types docker-build docker-up docker-down

PYTHON   := .venv/bin/python
UVICORN  := .venv/bin/uvicorn
API_PORT := 8766
WEB_PORT := 3000
MCP_PORT := 8765
API_HOST := 127.0.0.1
WEB_HOST := 0.0.0.0
MCP_HOST := 0.0.0.0
MCP_PATH := /mcp

# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------
install:
	python -m pip install -e ".[local]"
	cd web && npm install

# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
mcp:
	$(PYTHON) main.py --transport streamable-http --host $(MCP_HOST) --port $(MCP_PORT) --mcp-path $(MCP_PATH)

api:
	$(PYTHON) -m src.web_api.main --host $(API_HOST) --port $(API_PORT)

web:
	cd web && npm run dev -- --hostname $(WEB_HOST) --port $(WEB_PORT)

dev:
	@echo ">>> 启动 MCP :$(MCP_PORT)$(MCP_PATH) + Web API :$(API_PORT) + 前端 :$(WEB_PORT)"
	@set -e; \
		$(PYTHON) main.py --transport streamable-http --host $(MCP_HOST) --port $(MCP_PORT) --mcp-path $(MCP_PATH) & mcp_pid=$$!; \
		$(PYTHON) -m src.web_api.main --host $(API_HOST) --port $(API_PORT) --reload & api_pid=$$!; \
		(cd web && npm run dev -- --hostname $(WEB_HOST) --port $(WEB_PORT)) & web_pid=$$!; \
		cleanup() { \
			trap - INT TERM EXIT; \
			kill "$$mcp_pid" "$$api_pid" "$$web_pid" 2>/dev/null || true; \
			wait "$$mcp_pid" "$$api_pid" "$$web_pid" 2>/dev/null || true; \
		}; \
		trap cleanup INT TERM EXIT; \
		wait -n "$$mcp_pid" "$$api_pid" "$$web_pid"

dashboard:
	$(PYTHON) -m scripts.start_dashboard

# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
test:
	$(PYTHON) -m pytest tests/unit tests/integration tests/contract -q

test-e2e:
	$(PYTHON) -m pytest tests/e2e -q

smoke:
	$(PYTHON) tests/smoke/run_smoke.py

benchmark:
	$(PYTHON) -m scripts.benchmark --base-url http://127.0.0.1:$(API_PORT)

gen-types:
	cd web && npm run gen:types

# ---------------------------------------------------------------------------
# 容器（可选）
# ---------------------------------------------------------------------------
docker-build:
	docker build -t skdy-rag-server:local .

docker-up:
	docker compose up -d

docker-down:
	docker compose down
