"""演示种子：为两个租户各注入一份示例文档（走服务层摄取管道，等价于上传端点的内部路径）。

用法：python scripts/demo_seed.py
幂等性：按 title+tenant 查重，已存在则跳过。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infra.db import get_session_factory
from app.infra.embedder import get_embedder
from app.infra.models import PLATFORM_TENANT_ID, Case, Document, Tenant
from app.services.cases import confirm_and_index
from app.services.ingest import ingest_document

DEMO_CASE = {
    "tenant_id": "t_bagshop_001",
    "original_query": "客户咬定五金掉色是假货，威胁投诉，怎么破？",
    "review_result": "鉴定正品，属正常磨损，走质保流程提供免费电镀修复，不下架不退款，仅退款诉求驳回。",
    "category": "箱包",
}

PLATFORM_DOCS = [
    {
        "title": "电商平台假货争议处理规范（2026-06 版）",
        "category": "",
        "content": """# 电商平台假货争议处理规范（2026-06 版）

## 鉴定入口
涉及假货争议的交易，买卖双方均可通过平台鉴定入口提交鉴定申请，平台鉴定结论为最终依据，商家与买家均须执行。

## 商家义务
鉴定为假货的，商家须立即全额退款、承担往返运费，并配合平台下架同批次商品；情节严重的按平台假货处罚规则执行。

## 买家义务
鉴定为正品的，买家不得以'怀疑假货'为由拒绝付款或申请仅退款，运费由买家承担。
""",
    },
    {
        "title": "平台七天无理由退货与仅退款通用规则",
        "category": "",
        "content": """# 平台七天无理由退货与仅退款通用规则

## 七天无理由退货
商品签收后七日内且不影响二次销售的，买家可申请七天无理由退货。定制类、鲜活易腐类商品除外。商家不得以'已拆封'为由拒绝，但可要求买家承担退回运费。

## 仅退款适用范围
仅退款仅适用于：未收到货、商品运输中破损、经平台判定商品与描述严重不符三类场景。其余售后诉求应走退货退款流程，商家有权拒绝不合规的仅退款申请。

## 兜底原则
本规则与商家自有售后政策冲突时，就低不就高保障消费者法定权益；商家政策严于本规则的，按商家政策执行。
""",
    },
]

SAMPLES = [
    {
        "tenant_id": "t_bagshop_001",
        "category": "箱包",
        "title": "皮具店售后处理手册 v2",
        "content": """# 皮具店售后处理手册 v2

## 假货争议处理
客户主张商品为假货时，须先提交平台鉴定入口出具的鉴定结果。鉴定为正品且客户仍拒绝收货的，按七天无理由退货处理，运费由客户承担。鉴定为假货的，立即全额退款并下架同批次商品。

## 五金件质量争议
五金掉色属正常磨损范畴，不属于质量问题。客户坚持质疑的，引导走售后质保流程，提供半年内免费电镀修复服务。仅退款诉求不予支持。

## 仅退款处理规范
仅退款仅适用于：商品在运输中破损、商品与描述严重不符且客服确认。其他场景一律引导走退货退款流程。
""",
    },
    {
        "tenant_id": "t_watchshop_002",
        "category": "手表",
        "title": "名表专营店售后政策",
        "content": """# 名表专营店售后政策

## 走时误差处理
机械表每日误差 ±30 秒内属行业标准范围，不构成质量问题，不支持退换。超出范围的，送品牌授权维修点检测，确认机芯故障的按三包规定处理。

## 表带表扣维修
表带断裂属人为损坏的，提供付费维修，费用按品牌报价。半年内非人为断裂的，免费更换同型号表带。

## 真假鉴定争议
客户质疑正品来源的，引导通过平台鉴定入口鉴定。鉴定为正品的，客户承担鉴定费用；鉴定为假货的，假一赔三并上报平台。
""",
    },
]


def main() -> None:
    db = get_session_factory()()
    embedder = get_embedder()
    try:
        # 平台保留租户（幂等）
        if not db.query(Tenant).filter(Tenant.tenant_id == PLATFORM_TENANT_ID).one_or_none():
            db.add(Tenant(tenant_id=PLATFORM_TENANT_ID, name="平台层（共享政策，保留租户）", status="active"))
            db.commit()
            print(f"seeded tenant: {PLATFORM_TENANT_ID}")

        for s in SAMPLES:
            exists = (
                db.query(Document)
                .filter(Document.tenant_id == s["tenant_id"], Document.title == s["title"])
                .one_or_none()
            )
            if exists:
                print(f"skip (exists): {s['tenant_id']} / {s['title']}")
                continue
            doc = Document(
                tenant_id=s["tenant_id"],
                layer="merchant",
                title=s["title"],
                file_name=f"{s['title']}.md",
                file_type="md",
                category=s["category"],
                status="uploaded",
            )
            db.add(doc)
            db.commit()
            ingest_document(db, doc, s["content"].encode("utf-8"), embedder)
            print(f"ingested: {s['tenant_id']} / {s['title']} ({doc.chunk_count} chunks)")

        for s in PLATFORM_DOCS:
            exists = (
                db.query(Document)
                .filter(Document.tenant_id == PLATFORM_TENANT_ID, Document.title == s["title"])
                .one_or_none()
            )
            if exists:
                print(f"skip (exists): platform / {s['title']}")
                continue
            doc = Document(
                tenant_id=PLATFORM_TENANT_ID,
                layer="platform",
                title=s["title"],
                file_name=f"{s['title']}.md",
                file_type="md",
                category=s["category"],
                status="uploaded",
            )
            db.add(doc)
            db.commit()
            ingest_document(db, doc, s["content"].encode("utf-8"), embedder)
            print(f"ingested: platform / {s['title']} ({doc.chunk_count} chunks)")

        # 演示案例：已确认入库（飞轮沉淀的最小可见样例）
        case_key = DEMO_CASE["original_query"]
        if not db.query(Case).filter(Case.original_query == case_key).one_or_none():
            case = Case(
                case_id=f"case_{__import__('uuid').uuid4().hex[:12]}",
                tenant_id=DEMO_CASE["tenant_id"],
                query_id=None,
                original_query=DEMO_CASE["original_query"],
                review_result=DEMO_CASE["review_result"],
                category=DEMO_CASE["category"],
                status="pending",
                feedback_source="kezhou",
            )
            db.add(case)
            db.commit()
            confirm_and_index(db, case)
            print(f"ingested case: {case.case_id} (confirmed, hit_count=0)")
        else:
            print("skip (exists): demo case")
    finally:
        db.close()


if __name__ == "__main__":
    main()
