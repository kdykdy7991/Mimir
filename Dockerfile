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

# 先装依赖以利用层缓存
COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
COPY config ./config
COPY scripts ./scripts
RUN pip install --no-cache-dir -e .

# 其余源码（data 目录留空，运行时挂载 volume 持久化）
COPY . .

EXPOSE 8766

# 容器内必须绑定 0.0.0.0；外部访问边界由 -p / 反向代理控制
CMD ["python", "-m", "src.web_api.main", "--host", "0.0.0.0", "--port", "8766"]
