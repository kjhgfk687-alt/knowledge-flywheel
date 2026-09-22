"""ID 生成：契约 §3 的 ret_ / chk_ 前缀 ID 与案例 case_ 前缀 ID。"""
from uuid import uuid4


def new_query_id() -> str:
    return f"ret_{uuid4().hex[:8]}"


def new_chunk_id() -> str:
    return f"chk_{uuid4().hex[:12]}"


def new_case_id() -> str:
    return f"case_{uuid4().hex[:12]}"
