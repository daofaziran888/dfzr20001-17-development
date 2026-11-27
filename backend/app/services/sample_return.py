"""余样退回领域服务。

样品管理员把检测后未用完的余样退回留样柜。关键约束：
1. 退回是一笔独立申请，提交时若同一瓶茶样的检测结果刚导入但尚未签收，
   两条办理都先不落账，只标注冲突，由人工核对实物后再办理。
2. 落账重量以留样柜实收的实物重量为准（原样已部分耗尽时，补做可用量
   只等于实际退回重量），不允许照抄支取重量。
3. 同一笔退回重复办理是幂等的，且累计退回不能把柜内余量抬过留样登记重量——
   一瓶样的余量不会因为两个入口都认为退回完成而增加。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SampleReturnService(BaseModuleService):
    MODULE = "sample_return"
    ENTITY = "余样退回单"
    OPERATION_TERM = "办理退回"
    LIST_FIELDS = [
        "退回单号", "留样编号", "关联送检单号", "申报退回重量", "实际退回重量",
        "柜内余量", "冲突标记", "实物重量依据", "登记编号", "负责人",
        "生效版本", "备注说明",
    ]
    REQUIRED_FIELDS = ["退回单号", "留样编号", "关联送检单号", "实际退回重量"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"提交退回": "待确认", "办理退回": "已确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    def _find_dispatch(self, dispatch_no: str) -> dict[str, Any] | None:
        for row in store.rows("sample_dispatch"):
            if row.get("送检单号") == dispatch_no:
                return row
        return None

    def validate_entry(self, values: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        sample_no = str(values.get("留样编号", "")).strip()
        sample = next((r for r in store.rows("sample") if r.get("留样编号") == sample_no), None)
        if sample is None:
            errors.append(f"留样编号 {sample_no} 不在样品留样登记中")
        dispatch_no = str(values.get("关联送检单号", "")).strip()
        dispatch = self._find_dispatch(dispatch_no)
        if dispatch is None:
            errors.append(f"送检单号 {dispatch_no} 不存在，余样退回必须挂在原样品交检账下")
        elif dispatch.get("留样编号") != sample_no:
            errors.append("送检单与退回单的留样编号不一致，不能跨瓶退回余样")
        elif dispatch.get("status") != "已确认":
            errors.append("关联送检单尚未实际成功交检，不能办理余样退回")
        return_no = str(values.get("退回单号", "")).strip()
        if return_no and any(r.get("退回单号") == return_no for r in store.rows(self.MODULE)):
            errors.append(f"退回单号 {return_no} 已存在")
        return errors

    def _detect_conflict(self, entry: dict[str, Any]) -> str:
        """同一瓶茶样：检测结果已导入但报告尚未签收，退回申请先挂起并标冲突。"""
        sample_no = str(entry.get("留样编号", "")).strip()
        for testing in store.rows("testing"):
            if testing.get("留样编号") != sample_no:
                continue
            if testing.get("status") != "已确认":
                return "检测机构签章结果已导入待签收，余样退回暂不落账，请核对实物重量"
        return ""

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        # 提交退回时做冲突探测；冲突只标记、不阻断提交，两条办理都保持未落实物账
        entry = store.find(self.MODULE, entry_id)
        if entry is not None and action == "提交退回" and entry.get("status") == "草稿":
            conflict = self._detect_conflict(entry)
            entry["冲突标记"] = conflict
        return super().run_action(entry_id, action)

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        if action != "办理退回":
            return True, None
        sample_no = str(entry.get("留样编号", "")).strip()
        actual = store.parse_weight(entry.get("实际退回重量", 0))
        if actual <= 0:
            return False, "实际退回重量必须大于 0，并以留样柜实收称重为准"

        initial = store.sample_initial_weight(sample_no)
        balance = store.sample_cabinet_balance(sample_no)
        # 上限：退回后柜内余量不能超过留样登记的原始重量
        if actual > initial - balance + 1e-9:
            return False, (
                f"退回 {actual}kg 后柜内余量将超过留样登记重量 {initial}kg；"
                f"当前柜内已有 {balance}kg，本次最多退回 {round(initial - balance, 3)}kg"
            )

        # 关联送检单维度：退回不能超过该单实际支取且尚未退回的部分
        dispatch = self._find_dispatch(str(entry.get("关联送检单号", "")))
        if dispatch is not None:
            drawn = store.parse_weight(dispatch.get("支取重量", 0))
            returned_for_dispatch = round(sum(
                store.parse_weight(r.get("实际退回重量", 0))
                for r in store.rows(self.MODULE)
                if r.get("关联送检单号") == dispatch.get("送检单号")
                and r.get("status") == "已确认"
                and int(r.get("id", -1)) != int(entry.get("id", -1))
            ), 3)
            if actual > drawn - returned_for_dispatch + 1e-9:
                return False, (
                    f"该送检单支取 {drawn}kg、已退回 {returned_for_dispatch}kg，"
                    f"最多再退回 {round(drawn - returned_for_dispatch, 3)}kg；"
                    "原样已部分耗尽，不能按原支取重量退回"
                )

        request_id = str(entry.get("request_id") or f"return-{entry.get('id')}")
        already = store.has_sample_movement(request_id)
        if already is None:
            store.add_sample_movement(
                sample_no, "退回", actual, self.MODULE, int(entry["id"]),
                request_id=request_id,
                basis=f"退回单{entry.get('退回单号', '')}留样柜实收{actual}kg",
            )

        entry["柜内余量"] = store.sample_cabinet_balance(sample_no)
        entry["冲突标记"] = entry.get("冲突标记") or ""
        entry["实物重量依据"] = (
            f"留样柜实收退回 {actual}kg；{self.weight_basis_text(sample_no)}"
        )
        return True, None

    def weight_basis_text(self, sample_no: str) -> str:
        initial = store.sample_initial_weight(sample_no)
        drawn = store.sample_total_drawn(sample_no)
        returned = store.sample_total_returned(sample_no)
        balance = store.sample_cabinet_balance(sample_no)
        return (
            f"留样登记 {initial}kg；累计支取 {drawn}kg；累计实际退回 {returned}kg；"
            f"当前柜内可用 {balance}kg（补做检测可用量仅按实际退回重量计）"
        )

    def get_returns_for_dispatch(self, dispatch_no: str) -> list[dict[str, Any]]:
        return [
            r for r in store.rows(self.MODULE)
            if r.get("关联送检单号") == dispatch_no
        ]
