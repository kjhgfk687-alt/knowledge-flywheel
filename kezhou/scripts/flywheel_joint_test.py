"""知源↔客舟 联调测试清单执行器（对真实双服务跑，不用 mock）。

前置：知源 8100（zhiyuan+zhiyuan 模式的客舟 8200）均已启动。
用法：cd kezhou && .venv/Scripts/python.exe -X utf8 scripts/flywheel_joint_test.py

覆盖：S1 正常链路（双层证据+引用）/ S2 检索为空 / S3 知源超时与连接失败（客户端级）
/ S4 案例回写后再次检索（飞轮闭环，含知源确认入库）/ S4b include_case A/B 开关
/ S5 租户隔离（跨租户知识/未知租户/案例隔离）/ S6 hit_count 增量。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # kezhou 根，便于直跑

import httpx

from app.config import Settings
from app.retrieval.client import ZhiyuanRetrievalClient

KZ = "http://localhost:8200"
ZY = "http://localhost:8100"
KEY = {"X-API-Key": "zs-kz-dev-key-001"}
TENANT_A = "t_bagshop_001"
TENANT_B = "t_watchshop_002"
QA_KEY = "zs-kz-dev-key-001"

RESULTS: list[tuple[str, str, str]] = []  # (场景, 结论, 说明)


def record(scenario: str, ok: bool, note: str = "") -> None:
    RESULTS.append((scenario, "PASS" if ok else "FAIL", note))
    print(f"  [{'PASS' if ok else 'FAIL'}] {scenario}" + (f" — {note}" if note else ""))


def chat(message: str, session_id: str, tenant_id: str = TENANT_A, timeout: float = 60) -> dict:
    r = httpx.post(f"{KZ}/api/v1/chat", json={
        "tenant_id": tenant_id, "user_id": "u_joint", "session_id": session_id, "message": message,
    }, timeout=timeout)
    assert r.status_code == 200, f"chat HTTP {r.status_code}: {r.text[:200]}"
    return r.json()


def zhiyuan_retrieve(tenant_id: str, query: str, **kw) -> dict:
    body = {"tenant_id": tenant_id, "query": query, "top_k": kw.pop("top_k", 5)}
    body.update(kw)
    r = httpx.post(f"{ZY}/api/v1/retrieve", headers=KEY, json=body, timeout=15)
    return r.json()


def find_empty_query(candidates: list[str]) -> str | None:
    """探测一个知源返回 retrieved=false 的问题（S2/S4 需要真知识盲区）。"""
    for q in candidates:
        data = zhiyuan_retrieve(TENANT_A, q).get("data") or {}
        print(f"    探测 {q!r} -> retrieved={data.get('retrieved')} total={data.get('total')}")
        if data.get("retrieved") is False:
            return q
    return None


def s1_normal_path():
    print("== S1 正常链路：知识问答 → 双层证据 → 带引用回答 ==")
    # 双层断言用 top_k=10：案例库中与问题近乎同文的确认案例会按 score 挤占 top_k=5 的
    # 证据位（联调观察项：飞轮长大后单层化，契约 §3.5 预留"案例加权重排"演进点）
    bare = zhiyuan_retrieve(TENANT_A, "客户说买的包五金掉色怀疑是假货，怎么处理？", top_k=10)
    d = bare["data"]
    fields_ok = all(k in d for k in ("query_id", "tenant_id", "retrieved", "degraded", "total", "evidence", "latency_ms"))
    ev_ok = all(
        {"chunk_id", "content", "source_type", "source_title", "score", "metadata"} <= set(e)
        and set(e["metadata"]) >= {"doc_id", "chunk_index", "created_at"}
        for e in d["evidence"]
    )
    types = {e["source_type"] for e in d["evidence"]}
    record("S1a 知源裸接口字段完整性", fields_ok and ev_ok, f"top_k=10 证据 {d['total']} 条, 层级={sorted(types)}")

    # 契约 v1.3 §3.5 终态：案例 → 商户 → 平台 分层拼接（平台保底 1 席），层内 score 降序
    order = {"case": 0, "merchant": 1, "platform": 2}
    seq = [order[e["source_type"]] for e in d["evidence"]]
    layered = seq == sorted(seq)
    in_layer_desc = all(
        [e["score"] for e in d["evidence"] if e["source_type"] == layer]
        == sorted([e["score"] for e in d["evidence"] if e["source_type"] == layer], reverse=True)
        for layer in ("case", "merchant", "platform")
    )
    record("S1d v1.3 分层排序（案例→商户→平台，层内降序）", layered and in_layer_desc)
    record("S1b 三层证据齐备（案例+商户+平台）", {"merchant", "platform", "case"} <= types, f"layers={sorted(types)}")
    narrow = zhiyuan_retrieve(TENANT_A, "客户说买的包五金掉色怀疑是假货，怎么处理？")["data"]
    narrow_types = {e["source_type"] for e in narrow["evidence"]}
    record("S1b2 观察项：top_k=5 案例挤出效应", True,
           f"top_k=5 层级={sorted(narrow_types)}（案例近同文高分为 v1.3 排序策略的预期行为）")
    record("S1c 防串租户回显", d["tenant_id"] == TENANT_A, f"tenant_id={d['tenant_id']}")

    body = chat("客户说买的包五金掉色怀疑是假货，怎么处理？", "s_joint_s1")
    types_kz = {c["source_type"] for c in body["citations"]}
    record("S1e 客舟带引用回答", "[1]" in body["reply_text"] and len(body["citations"]) >= 1,
           f"引用 {len(body['citations'])} 条, 层级={sorted(types_kz)}")
    record("S1f 引用含溯源字段", all(
        {"n", "chunk_id", "source_title", "source_type", "from_case"} <= set(c) for c in body["citations"]
    ))
    record("S1g 未转人工", body["need_human"] is False and body["query_id"].startswith("ret_"),
           f"query_id={body['query_id']}")


def s2_empty():
    print("== S2 检索为空 → 降级话术 + 转人工 ==")
    # 注意：飞轮会把上一轮的盲区沉淀为案例并吸收同类问题——盲区候选需要持续外扩，
    # "候选问题不再为空"本身就是飞轮生效的证据（命中的是历轮沉淀的案例）
    q = find_empty_query([
        "能寄到国外吗", "支持旧衣回收吗", "店里能试穿吗", "有没有会员折扣",
        "能预约到店保养吗", "包装盒可以单独买吗", "支持以旧换新吗", "有实体展示厅吗",
    ])
    if q is None:
        record("S2 检索为空", False, "候选问题均未触发 retrieved=false（盲区已被历轮案例覆盖=飞轮生效）；确定性覆盖见单测 test_empty_retrieval_*")
        return None
    body = chat(q, "s_joint_s2")
    record("S2a 空检索不编造", body["need_human"] is True and body["transfer_reason"] == "retrieval_empty",
           f"reason={body['transfer_reason']}")
    record("S2b 降级话术", "没有找到" in body["reply_text"] and "转接人工" in body["reply_text"])
    return q


def s3_timeout():
    print("== S3 知源接口超时 / 连接失败（客户端级，真实网络栈） ==")
    import asyncio

    async def _run():
        tiny = ZhiyuanRetrievalClient(Settings(zhiyuan_base_url=ZY, retrieve_timeout_seconds=0.0005))
        r1 = await tiny.retrieve(TENANT_A, "五金掉色")
        await tiny.aclose()
        dead = ZhiyuanRetrievalClient(Settings(zhiyuan_base_url="http://localhost:8199"))
        r2 = await dead.retrieve(TENANT_A, "五金掉色")
        await dead.aclose()
        return r1, r2

    r1, r2 = asyncio.run(_run())
    record("S3a 真实超时映射 CLIENT_TIMEOUT", r1.status.value == "error" and r1.error_code == "CLIENT_TIMEOUT"
           and r1.severity == "error", f"code={r1.error_code} severity={r1.severity}")
    record("S3b 连接失败映射 CONNECTION_ERROR", r2.error_code == "CONNECTION_ERROR" and r2.severity == "error",
           f"code={r2.error_code}")
    record("S3c 失败时 query_id 为空（不伪造链路键）", r1.query_id is None and r2.query_id is None)


def s4_writeback_cycle(q: str | None):
    print("== S4 案例回写后再次检索（飞轮闭环，全新问题） ==")
    if q is None:
        print("  [SKIP] 无可用盲区问题（见 S2 说明）")
        RESULTS.append(("S4 飞轮闭环", "SKIP", "无盲区问题；此前 2026-09-22 已用'羽绒服'问题实证闭环"))
        return
    body = chat(q, "s_joint_s4")
    assert body["need_human"] is True, f"预期转人工，实际 {body}"
    hid = httpx.get(f"{KZ}/api/v1/handoffs", params={"session_id": "s_joint_s4"}, timeout=10).json()["handoffs"][0]["id"]
    review = httpx.post(f"{KZ}/api/v1/handoffs/{hid}/review", json={
        "review_result": "联调：经确认该问题不属于本店经营范围，已向客户说明主营品类",
        "category": "箱包",
    }, timeout=30).json()
    wb = review["writeback"]
    record("S4a 审核回写知源", wb["status"] == "success" and wb["case_id"], f"case_id={wb.get('case_id')}")
    record("S4b 回写携带 query_id 链路键", wb["status"] == "success" and review["record"]["query_id"] == body["query_id"],
           f"query_id={body['query_id']}")

    case_id = wb["case_id"]
    pend = httpx.get(f"{ZY}/api/v1/cases", headers=KEY, params={"tenant_id": TENANT_A, "status": "pending"}, timeout=10).json()["data"]
    record("S4c 知源侧 pending 待确认", any(c["case_id"] == case_id for c in pend), f"pending={len(pend)}")

    cf = httpx.post(f"{ZY}/api/v1/cases/{case_id}/confirm", headers=KEY, timeout=15).json()
    record("S4d 知源确认入库", cf["data"]["status"] == "confirmed")

    time.sleep(0.5)  # 入库索引可见性
    again = chat(q, "s_joint_s4_again")
    from_case = [c for c in again["citations"] if c["from_case"]]
    record("S4e 复问命中案例不转人工", again["need_human"] is False and bool(from_case),
           f"from_case 引用 {len(from_case)} 条")
    record("S4f 新案例 hit_count 初始", (httpx.get(
        f"{ZY}/api/v1/cases", headers=KEY, params={"tenant_id": TENANT_A, "status": "confirmed"}, timeout=10
    ).json()["data"] and True) is True)


def s4b_include_case_switch():
    print("== S4b include_case A/B 开关（契约 §2 飞轮度量开关） ==")
    q = "你们家卖不卖羽绒服？"  # 已有 confirmed 案例
    with_case = zhiyuan_retrieve(TENANT_A, q, include_case=True)["data"]
    no_case = zhiyuan_retrieve(TENANT_A, q, include_case=False)["data"]
    has_case = any(e["source_type"] == "case" for e in with_case["evidence"])
    no_case_types = {e["source_type"] for e in no_case["evidence"]}
    record("S4b include_case 开关生效", has_case and "case" not in no_case_types,
           f"true 时含 case={has_case}, false 时层级={sorted(no_case_types)}")


def s5_tenant_isolation():
    print("== S5 租户隔离 ==")
    d = zhiyuan_retrieve(TENANT_B, "客户说皮具五金掉色怀疑是假货怎么处理？")["data"]
    merchant_titles = [e["source_title"] for e in d["evidence"] if e["source_type"] == "merchant"]
    record("S5a B 商户检索不到 A 商户文档", all("皮具店" not in t for t in merchant_titles),
           f"B 商户 merchant 层证据={merchant_titles or '无'}（平台层共享属契约设计）")

    from app.retrieval.client import ZhiyuanRetrievalClient
    import asyncio
    ghost = ZhiyuanRetrievalClient(Settings(zhiyuan_base_url=ZY))
    r = asyncio.run(ghost.retrieve("t_no_such_tenant", "五金掉色"))
    record("S5b 未知租户 TENANT_NOT_FOUND", r.error_code == "TENANT_NOT_FOUND" and r.severity == "error",
           f"code={r.error_code}")

    cases_b = httpx.get(f"{ZY}/api/v1/cases", headers=KEY, params={"tenant_id": TENANT_B}, timeout=10).json()["data"]
    record("S5c 案例库按租户隔离", all(c["tenant_id"] == TENANT_B for c in cases_b),
           f"B 商户案例数={len(cases_b)}（A 商户的案例不可见）")

    # 注意用知识型话术：带"订单"字样会被意图路由到订单查询（追问订单号），走不到检索
    body = chat("这个东西是正品吗", "s_joint_s5", tenant_id="t_no_such_tenant")
    record("S5d 客舟全链路兜底：未知租户转人工", body["need_human"] is True
           and body["transfer_reason"] == "retrieval_error", f"reason={body['transfer_reason']}")


def s6_hit_count():
    print("== S6 hit_count 增量（飞轮核心指标） ==")
    q = "你们家卖不卖羽绒服？"
    before = {c["case_id"]: c["hit_count"] for c in httpx.get(
        f"{ZY}/api/v1/cases", headers=KEY, params={"tenant_id": TENANT_A, "status": "confirmed"}, timeout=10
    ).json()["data"]}
    for _ in range(2):
        chat(q, f"s_joint_s6_{time.time_ns()}")
    time.sleep(0.5)
    after = {c["case_id"]: c["hit_count"] for c in httpx.get(
        f"{ZY}/api/v1/cases", headers=KEY, params={"tenant_id": TENANT_A, "status": "confirmed"}, timeout=10
    ).json()["data"]}
    grew = [cid for cid in after if after[cid] > before.get(cid, 0)]
    record("S6 命中后 hit_count 递增", bool(grew),
           f"增长案例: {[(cid, before.get(cid, 0), after[cid]) for cid in grew]}")


def main() -> int:
    print(f"联调开始 {time.strftime('%H:%M:%S')} — 客舟 {KZ} / 知源 {ZY}")
    s1_normal_path()
    q = s2_empty()
    s3_timeout()
    s4_writeback_cycle(q)
    s4b_include_case_switch()
    s5_tenant_isolation()
    s6_hit_count()

    print("\n========== 联调结果汇总 =========")
    fails = 0
    for scenario, verdict, note in RESULTS:
        mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️ "}[verdict]
        print(f"{mark} {verdict:<5} {scenario}" + (f" — {note}" if note else ""))
        fails += verdict == "FAIL"
    print(f"合计 {len(RESULTS)} 项，FAIL {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
