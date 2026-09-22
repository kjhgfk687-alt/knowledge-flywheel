"""冒烟检查：对运行中的服务（默认 127.0.0.1:8100）走一遍契约主路径。

用法：先启动 uvicorn，再 python scripts/smoke_check.py
覆盖：鉴权 401 族、参数 422 族、租户 404/403、成功检索、租户隔离、检索为空。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

BASE = "http://localhost:8100"
KEY = "zs-kz-dev-key-001"
H = {"X-API-Key": KEY, "Content-Type": "application/json"}

passed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    passed.append(f"[{mark}] {name}" + (f"  {detail}" if detail and not cond else ""))
    print(passed[-1])


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=30)

    # ---- 鉴权（契约 40100/40101）----
    r = c.post("/api/v1/retrieve", json={"tenant_id": "t_bagshop_001", "query": "五金掉色"})
    check("缺 API-Key → 40100", r.status_code == 401 and r.json()["code"] == 40100, r.text)

    r = c.post("/api/v1/retrieve", headers={"X-API-Key": "wrong"}, json={"tenant_id": "t_bagshop_001", "query": "五金掉色"})
    check("错误 API-Key → 40101", r.status_code == 401 and r.json()["code"] == 40101, r.text)

    # ---- 参数校验（契约 40001-40005）----
    r = c.post("/api/v1/retrieve", headers=H, json={"query": "五金掉色"})
    check("缺 tenant_id → 40001", r.status_code == 422 and r.json()["code"] == 40001, r.text)

    r = c.post("/api/v1/retrieve", headers=H, json={"tenant_id": "T_UPPER!", "query": "x"})
    check("非法 tenant_id → 40001", r.status_code == 422 and r.json()["code"] == 40001, r.text)

    r = c.post("/api/v1/retrieve", headers=H, json={"tenant_id": "t_bagshop_001", "query": "   "})
    check("空白 query → 40002", r.status_code == 422 and r.json()["code"] == 40002, r.text)

    r = c.post("/api/v1/retrieve", headers=H, json={"tenant_id": "t_bagshop_001", "query": "x", "top_k": 99})
    check("top_k 超限 → 40003", r.status_code == 422 and r.json()["code"] == 40003, r.text)

    r = c.post("/api/v1/retrieve", headers=H, json={"tenant_id": "t_bagshop_001", "query": "x", "filters": {"unknown": 1}})
    check("filters 未知键 → 40004", r.status_code == 422 and r.json()["code"] == 40004, r.text)

    r = c.post("/api/v1/retrieve", headers=H, content=b"not-json{")
    check("非 JSON body → 40005", r.status_code == 422 and r.json()["code"] == 40005, r.text)

    # ---- 租户（契约 40401）----
    r = c.post("/api/v1/retrieve", headers=H, json={"tenant_id": "t_nobody_404", "query": "五金掉色"})
    check("租户不存在 → 40401", r.status_code == 404 and r.json()["code"] == 40401, r.text)

    # ---- 成功检索 + 结构（契约 §3）----
    r = c.post(
        "/api/v1/retrieve", headers=H,
        json={"tenant_id": "t_bagshop_001", "query": "客户说买的包五金掉色怀疑是假货要求退款怎么处理", "top_k": 3},
    )
    d = r.json()
    ok_body = r.status_code == 200 and d["code"] == 0
    data = d.get("data") or {}
    has_fields = all(k in data for k in ("query_id", "tenant_id", "retrieved", "degraded", "degrade_reason", "total", "evidence", "latency_ms"))
    ev_ok = all(
        all(k in e for k in ("chunk_id", "content", "source_type", "source_title", "score", "metadata"))
        for e in data.get("evidence", [])
    )
    scores_sorted = all(
        data["evidence"][i]["score"] >= data["evidence"][i + 1]["score"]
        for i in range(len(data["evidence"]) - 1)
        if data["evidence"][i]["source_type"] == data["evidence"][i + 1]["source_type"]
    )  # 契约 v1.2：score 保证层内降序；商户层整体在平台层之前
    seq = [e["source_type"] for e in data.get("evidence", [])]
    layer_order_ok = "platform" not in seq or set(seq[seq.index("platform") :]) <= {"platform"}
    check("成功检索 code=0", ok_body, r.text)
    check("data 字段齐全", has_fields)
    check("evidence 字段齐全", ev_ok)
    check("score 层内降序", scores_sorted)
    check("商户层在平台层之前", layer_order_ok)
    check("命中商户层知识", any("五金" in e["content"] or "五金" in e["source_title"] for e in data.get("evidence", [])))
    print(f"       bagshop evidence: {[(e['source_title'], e['score']) for e in data.get('evidence', [])]}")

    # ---- 租户隔离：另一租户查不到皮具店的内容 ----
    r = c.post(
        "/api/v1/retrieve", headers=H,
        json={"tenant_id": "t_watchshop_002", "query": "客户说买的包五金掉色怀疑是假货要求退款怎么处理"},
    )
    ev = (r.json().get("data") or {}).get("evidence", [])
    check("租户隔离：watchshop 不含 bagshop 内容", all("皮具" not in e["source_title"] for e in ev))
    print(f"       watchshop evidence: {[(e['source_title'], e['score']) for e in ev]}")

    # ---- 检索为空（契约 retrieved=false 非错误）----
    r = c.post(
        "/api/v1/retrieve", headers=H,
        json={"tenant_id": "t_watchshop_002", "query": "月球上能不能充电"},
    )
    d2 = (r.json().get("data") or {})
    check("检索为空 retrieved=false", r.status_code == 200 and r.json()["code"] == 0 and d2.get("retrieved") is False and d2.get("total") == 0, r.text)

    n_fail = sum(1 for p in passed if p.startswith("[FAIL]"))
    print(f"\n==== smoke: {len(passed) - n_fail}/{len(passed)} passed ====")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
