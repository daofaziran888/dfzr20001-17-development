"""留样送检领域服务（样品交检账）。

样品管理员把留样柜中的茶样支取出来，随留样送检文件交给指定检测机构。
每一条交检账都绑定：
- 原交检凭据里的留样编号（必须来自样品留样登记）；
- 所需的检测机构（检测机构签收结果导入时只认这家机构）；
- 实物重量依据（支取重量、当前柜内余量、退回后可补做重量）。

交检账是检测工作台签收报告的唯一来源：没有成功交检的留样不能签收报告。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SampleDispatchService(BaseModuleService):
    MODULE = "sample_dispatch"
    ENTITY = "留样送检单"
    OPERATION_TERM = "确认交检"
    LIST_FIELDS = [
        "送检单号", "留样编号", "检测机构", "检测项目", "支取重量",
        "柜内余量", "交检状态", "实物重量依据", "登记编号", "负责人",
        "生效版本", "备注说明",
    ]
    REQUIRED_FIELDS = ["送检单号", "留样编号", "检测机构", "支取重量"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"确认交检": "已确认", "提交确认": "待确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    def find_sample(self, sample_no: str) -> dict[str, Any] | None:
        for row in store.rows("sample"):
            if row.get("留样编号") == sample_no:
                return row
        return None

    def validate_entry(self, values: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        sample_no = str(values.get("留样编号", "")).strip()
        sample = self.find_sample(sample_no)
        if sample is None:
            errors.append(f"留样编号 {sample_no} 不在样品留样登记中，不能开立样品交检账")
        draw_weight = store.parse_weight(values.get("支取重量", 0))
        if draw_weight <= 0:
            errors.append("支取重量必须大于 0，且需明示实物重量依据")
        elif sample is not None:
            available = store.sample_cabinet_balance(sample_no)
            if draw_weight > available + 1e-9:
                errors.append(
                    f"留样柜中 {sample_no} 可用实物仅 {available}kg，"
                    f"无法支取 {draw_weight}kg（补做检测只按实际退回柜内的重量计算）"
                )
        dispatch_no = str(values.get("送检单号", "")).strip()
        if dispatch_no:
            dup = [r for r in store.rows(self.MODULE) if r.get("送检单号") == dispatch_no]
            if dup:
                errors.append(f"送检单号 {dispatch_no} 已存在")
        if sample is not None:
            # 从留样柜再试支取余样给另一个检测项目前，
            # 必须等前一个交检结果完成实际签收，避免同一瓶样并行占用
            awaiting = self.latest_dispatch_awaiting_report(sample_no)
            if awaiting is not None:
                errors.append(
                    f"留样 {sample_no} 的送检单 {awaiting.get('送检单号', '')} "
                    "尚未完成检测报告签收，需等前一个交检结果后才能再支取余样"
                )
        return errors

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        if action != "确认交检":
            return True, None
        sample_no = str(entry.get("留样编号", "")).strip()
        draw_weight = store.parse_weight(entry.get("支取重量", 0))
        request_id = str(entry.get("request_id") or f"dispatch-{entry.get('id')}")

        # 幂等：同一交检单重复确认不能重复支取柜内实物
        already = store.has_sample_movement(request_id)
        if already is None:
            available = store.sample_cabinet_balance(sample_no)
            if draw_weight > available + 1e-9:
                return False, (
                    f"留样柜中 {sample_no} 可用实物仅 {available}kg，"
                    f"无法支取 {draw_weight}kg；原样已部分耗尽时补做量只按实际退回重量"
                )
            store.add_sample_movement(
                sample_no, "支取", draw_weight, self.MODULE, int(entry["id"]),
                request_id=request_id,
                basis=f"送检单{entry.get('送检单号', '')}支取实物{draw_weight}kg",
            )

        entry["交检状态"] = "已交检"
        entry["柜内余量"] = store.sample_cabinet_balance(sample_no)
        entry["实物重量依据"] = self.weight_basis_text(sample_no)
        return True, None

    # ── 业务查询 ──

    def weight_basis_text(self, sample_no: str) -> str:
        """生成可追溯的实物重量依据说明，退回记录与报告都引用同一口径。"""
        initial = store.sample_initial_weight(sample_no)
        drawn = store.sample_total_drawn(sample_no)
        returned = store.sample_total_returned(sample_no)
        balance = store.sample_cabinet_balance(sample_no)
        return (
            f"留样登记 {initial}kg；累计支取 {drawn}kg；累计实际退回 {returned}kg；"
            f"当前柜内可用 {balance}kg（退回以留样柜实收重量为准）"
        )

    def get_dispatches_for_sample(self, sample_no: str) -> list[dict[str, Any]]:
        """同一瓶留样的全部交检账（用于判断前一个交检结果是否已签收）。"""
        return [
            r for r in store.rows(self.MODULE)
            if r.get("留样编号") == sample_no
        ]

    def get_confirmed_dispatch(self, dispatch_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("送检单号") == dispatch_no and row.get("status") == "已确认":
                return row
        return None

    def get_accepted_dispatch(self, sample_no: str, lab: str) -> dict[str, Any] | None:
        """按原交检凭据（留样编号 + 所需检测机构）找到实际成功交检的账。"""
        for row in store.rows(self.MODULE):
            if (row.get("留样编号") == sample_no
                    and str(row.get("检测机构", "")).strip() == str(lab).strip()
                    and row.get("status") == "已确认"):
                return row
        return None

    def latest_dispatch_awaiting_report(self, sample_no: str,
                                        exclude_id: int | None = None) -> dict[str, Any] | None:
        """该瓶留样最近一笔已交检但检测报告尚未签收的交检账。"""
        from app.services.testing_receipt import TestingReceiptService
        receipt_svc = TestingReceiptService()
        candidates = [
            r for r in self.get_dispatches_for_sample(sample_no)
            if r.get("status") == "已确认" and (exclude_id is None or int(r["id"]) != exclude_id)
        ]
        for dispatch in reversed(candidates):
            if not receipt_svc.signed_report_exists(int(dispatch["id"])):
                return dispatch
        return None
