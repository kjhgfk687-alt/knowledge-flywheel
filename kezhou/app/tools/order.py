"""订单查询工具：只读查询协议 + 阶段三 Mock 实现。

结构性安全约定：客舟图内不存在任何订单写操作工具（退款/改单只能产生审批请求）。
阶段四接真实订单 API 时实现同一协议替换即可。
"""

import asyncio
import re
from typing import Protocol

ORDER_ID_RE = re.compile(r"ORD\d+", re.IGNORECASE)


class OrderNotFound(Exception):
    """订单号不存在（业务性未命中，不是基础设施错误）。"""


class OrderTool(Protocol):
    async def get(self, order_id: str) -> dict: ...


def extract_order_id(*texts: str | None) -> str | None:
    """从一段或多段文本里提取第一个订单号（如 ORD1001），无则 None。"""
    for text in texts:
        if not text:
            continue
        m = ORDER_ID_RE.search(text)
        if m:
            return m.group(0).upper()
    return None


# 演示数据：覆盖风控三维（鉴定状态 × 品类 × 金额）的典型组合
_ORDERS = {
    "ORD1001": {"order_id": "ORD1001", "tenant_id": "t_bagshop_001", "user_id": "u1",
                "item": "真皮手提包", "category": "箱包", "amount": 1280.0,
                "auth_status": "authentic", "status": "已签收", "created_at": "2026-09-01"},
    "ORD1002": {"order_id": "ORD1002", "tenant_id": "t_bagshop_001", "user_id": "u1",
                "item": "限量款邮差包", "category": "箱包", "amount": 15800.0,
                "auth_status": "unauthenticated", "status": "已签收", "created_at": "2026-09-12"},
    "ORD1003": {"order_id": "ORD1003", "tenant_id": "t_bagshop_001", "user_id": "u1",
                "item": "帆布托特包", "category": "箱包", "amount": 520.0,
                "auth_status": "authentic", "status": "运输中", "created_at": "2026-09-18"},
    "ORD1004": {"order_id": "ORD1004", "tenant_id": "t_watchshop_002", "user_id": "u2",
                "item": "机械腕表", "category": "手表", "amount": 8800.0,
                "auth_status": "authentic", "status": "已签收", "created_at": "2026-08-30"},
}


class MockOrderTool:
    async def get(self, order_id: str) -> dict:
        await asyncio.sleep(0.01)  # 模拟网络耗时
        order = _ORDERS.get(order_id.upper())
        if order is None:
            raise OrderNotFound(order_id)
        return dict(order)
