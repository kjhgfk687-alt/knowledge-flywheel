"""generate：RAG 生成，强制 [n] 引用，内部"生成→校验→缺失重生成一次"循环。

引用校验是纯代码（正则 + 编号命中证据集合），不信任模型自评。
重试后仍缺引用 → citation_missing=True 放行（决策点3补充：保留标记，阶段三再决定
是否升级为强制转人工），置信度评估会因该信号降权。
LLM 两次调用均失败 → generation_failed=True → 条件边送 human_handoff(llm_error)。
"""

import logging
import re
from typing import Any

from app.config import Settings
from app.llm import LLM

logger = logging.getLogger(__name__)

_CITE_RE = re.compile(r"\[(\d+)\]")

_SOURCE_LABELS = {"platform": "平台政策", "merchant": "商户文档", "case": "历史审核案例"}

_GEN_SYSTEM = (
    "RAG_GENERATE：你是电商客服助手。只依据给出的证据回答用户问题，"
    "每个论断必须用 [编号] 标注来源证据（如 [1] 或 [1][2]），不得编造证据里没有的内容。"
)
_GEN_SYSTEM_STRICT = (
    "RAG_GENERATE（重生成）：上一次输出缺少 [编号] 引用标注，视为无效。"
    "重新回答用户问题，每个论断都必须附带 [编号] 引用，否则不可输出。"
)


def format_evidence_block(evidences: list[dict], degraded: bool) -> str:
    lines = [
        f"[{i}] ({_SOURCE_LABELS.get(ev['source_type'], ev['source_type'])}) {ev['source_title']}：{ev['content']}"
        for i, ev in enumerate(evidences, 1)
    ]
    block = "\n".join(lines)
    if degraded:
        # 契约第 5 节客舟侧消费约定：降级路径的证据可信度需下调
        block = "（注意：知识库处于降级检索状态，以下证据可信度需下调）\n" + block
    return block


def extract_citations(text: str, max_n: int) -> list[int]:
    return sorted({n for n in (int(m) for m in _CITE_RE.findall(text)) if 1 <= n <= max_n})


def make_generate(settings: Settings, llm: LLM):
    async def generate(state: dict[str, Any]) -> dict[str, Any]:
        evidences = state.get("evidences") or []
        if not evidences:
            return {"generation_failed": True}

        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in state.get("history", [])[-4:])
        base_prompt = (
            f"用户画像：{state.get('user_profile_summary') or '（无画像）'}\n"
            f"近期对话：\n{history_text}\n用户问题：{state.get('user_input', '')}\n"
            f"知识库证据：\n{format_evidence_block(evidences, state.get('retrieval_degraded', False))}"
        )

        answer, cited_ns, regenerated = "", [], False
        for attempt in (1, 2):
            system = _GEN_SYSTEM if attempt == 1 else _GEN_SYSTEM_STRICT
            try:
                raw = await llm.complete(system, base_prompt)
            except Exception as e:
                logger.warning("生成调用失败（第 %s 次）：%s", attempt, e)
                continue
            answer = raw.strip()
            cited_ns = extract_citations(answer, len(evidences))
            if cited_ns:
                break
            if attempt == 1:
                regenerated = True
                logger.info("生成缺少引用，强制重生成 session=%s", state.get("session_id"))

        if not answer:
            logger.error("生成两次调用失败 session=%s", state.get("session_id"))

        citations = [
            {
                "n": n,
                "chunk_id": evidences[n - 1]["chunk_id"],
                "source_title": evidences[n - 1]["source_title"],
                "source_type": evidences[n - 1]["source_type"],
                "from_case": evidences[n - 1]["source_type"] == "case",
            }
            for n in cited_ns
        ]
        return {
            "draft_answer": answer,
            "citations": citations,
            "citation_missing": not cited_ns,
            "regenerated": regenerated,
            "generation_failed": not answer,
        }

    return generate


def after_generate(state: dict[str, Any]) -> str:
    return "human_handoff" if state.get("generation_failed") else "assess_confidence"
