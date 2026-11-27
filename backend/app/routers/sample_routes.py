"""留样送检专用路由：签收结果文件导入、留样柜实物台账、受理文件查回。

通用登记/动作仍走资源路由 /api/{module}，这里只承载资源路由无法表达的
文件型导入与实物台账查询。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import service_for
from app.services.sample_dispatch import SampleDispatchService
from app.services.testing_receipt import TestingReceiptService
from app.store import store

router = APIRouter(prefix="/api", tags=["留样送检"])


class ReceiptImportPayload(BaseModel):
    文件编号: str | None = None
    文件名: str
    文件指纹: str = ""
    检测机构: str
    负责人: str | None = None
    备注说明: str | None = None
    报告明细: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/testing_receipt/import")
def import_receipt_file(payload: ReceiptImportPayload) -> dict[str, Any]:
    """检测机构签收结果文件导入：按原交检凭据受理，其余茶批报告拒收。"""
    service = service_for("testing_receipt")
    assert isinstance(service, TestingReceiptService)
    result = service.import_receipt_file(payload.model_dump())
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "导入失败"))
    return result


@router.get("/testing_receipt/{receipt_id}/detail")
def get_receipt_detail(receipt_id: int) -> dict[str, Any]:
    """品控查回：原送检文件与最终受理值。"""
    service: TestingReceiptService = service_for("testing_receipt")  # type: ignore[assignment]
    detail = service.get_receipt_detail(receipt_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"签收结果文件 {receipt_id} 不存在")
    return detail


@router.get("/sample/{sample_no}/cabinet")
def get_sample_cabinet(sample_no: str) -> dict[str, Any]:
    """留样柜实物台账：期初重量、支取、实际退回与当前可用重量。"""
    sample_svc = service_for("sample")
    sample = next((r for r in store.rows("sample") if r.get("留样编号") == sample_no), None)
    if sample is None:
        raise HTTPException(status_code=404, detail=f"留样编号 {sample_no} 不存在")
    snapshot = sample_svc.cabinet_snapshot(sample_no)
    dispatch_svc = service_for("sample_dispatch")
    assert isinstance(dispatch_svc, SampleDispatchService)
    snapshot["交检账"] = dispatch_svc.get_dispatches_for_sample(sample_no)
    snapshot["退回记录"] = [
        r for r in store.rows("sample_return") if r.get("留样编号") == sample_no
    ]
    return snapshot
