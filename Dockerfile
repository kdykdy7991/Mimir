# SKDY RAG Server — Web API 镜像（可选容器部署，M4）
#
# 构建：  docker build -t skdy-rag-server:local .
# 运行：  docker run --rm --network host skdy-rag-server:local
#         （host 网络使容器内 localhost:8003 直达宿主机的 embedding vLLM）
#
# 注意：默认嵌入配置指向宿主机 embedding server（见 config/settings.yaml）。
#       远程 / 多机部署时通过 ${EMBEDDING_*} 环境变量指向可达的服务，并置于反向代理之后。

FROM python:3.12-slim

WORKDIR /app

# 系统依赖（pymupdf 在 slim 镜像上需要共享库）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

# 依赖清单单独成层。普通源码/config 改动不会再触发完整依赖下载；
# BuildKit cache 即使在 requirements 变化时也能复用已下载的 wheel。
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# 业务代码后置。依赖已安装，editable 安装只登记本项目，不再解析/下载依赖。
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY scripts ./scripts
COPY main.py ./
# The API is a gRPC client of DocReader and needs the generated wire stubs at
# runtime. Copy only the shared protocol package; the parser implementation and
# its heavy dependencies remain isolated in the DocReader image.
COPY services/docreader/docreader/__init__.py ./docreader/__init__.py
COPY services/docreader/docreader/proto ./docreader/proto
RUN pip install --no-build-isolation --no-deps -e .

EXPOSE 8766

# 容器内必须绑定 0.0.0.0；外部访问边界由 -p / 反向代理控制
CMD ["python", "-m", "src.web_api.main", "--host", "0.0.0.0", "--port", "8766"]
