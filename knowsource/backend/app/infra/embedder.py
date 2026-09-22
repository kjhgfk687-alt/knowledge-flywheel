"""嵌入提供者：接口化，默认 fastembed 本地 ONNX（BAAI/bge-small-zh-v1.5，512 维）。

选型理由见项目设计 §1：无 API 依赖、免 torch、中文效果好；
EmbeddingProvider 接口让降级路径测试可注入假实现，也让后续切换 OpenAI 兼容 API 不动检索代码。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

import httpx

from app.config import get_settings

# HuggingFace 直连在受限网络不可用时，走国内镜像下载模型（可被外部 env 覆盖）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


class EmbeddingProvider(ABC):
    name: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedProvider(EmbeddingProvider):
    name = "fastembed"

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding  # 延迟导入，加快应用冷启动

        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # fastembed 返回惰性生成器，逐条物化为 list
        return [e.tolist() for e in self._model.embed(texts)]


class HashEmbeddingProvider(EmbeddingProvider):
    """确定性词法嵌入：jieba 分词 + 字符 bigram 特征哈希（sign hashing），L2 归一化。

    用途：模型托管点（HF/hf-mirror/modelscope）不可达的受限网络环境下闭环开发与测试。
    给出词法级相似度（语义泛化弱于 bge 等真实向量模型）；检索链路对提供者无感，
    网络可用时改 .env 的 EMBEDDING_PROVIDER=fastembed 即可获得真实语义向量。
    """

    name = "hash"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        import math

        import jieba

        jieba.setLogLevel(60)
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            tokens = [t for t in jieba.cut(text) if t.strip()]
            feats = tokens + [text[i : i + 2] for i in range(len(text) - 1)]
            for f in feats:
                h = int(hashlib.md5(f.encode("utf-8")).hexdigest(), 16)
                vec[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vectors.append([x / norm for x in vec])
        return vectors


class OpenAICompatibleProvider(EmbeddingProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = httpx.post(
            f"{self._base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "input": texts},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


def get_embedder() -> EmbeddingProvider:
    s = get_settings()
    if s.embedding_provider == "openai_compatible":
        if not (s.openai_base_url and s.openai_api_key):
            raise RuntimeError("EMBEDDING_PROVIDER=openai_compatible 需要 OPENAI_BASE_URL / OPENAI_API_KEY")
        return OpenAICompatibleProvider(s.openai_base_url, s.openai_api_key, s.embedding_model)
    if s.embedding_provider == "hash":
        return HashEmbeddingProvider(dim=s.embedding_dim)
    return FastEmbedProvider(s.embedding_model)
