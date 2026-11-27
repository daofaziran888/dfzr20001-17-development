"""样品留样领域服务：登记留样记录，关联具体工序节点，跟踪保存期限与留样柜实物重量。"""
from __future__ import annotations
from typing import Any
from datetime import datetime
from app.services.base import BaseModuleService
from app.store import store

class SampleService(BaseModuleService):
    MODULE = "sample"
    ENTITY = "留样记录"
    OPERATION_TERM = "登记留样"
    LIST_FIELDS = ["留样编号", "取样节点", "样品形态", "留样重量", "柜内余量",
                   "保存期限", "登记编号", "负责人", "生效版本", "备注说明"]
    REQUIRED_FIELDS = ["留样编号", "样品形态", "保存期限"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"登记留样": "已确认", "提交确认": "待确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    def validate_entry(self, values: dict[str, Any]) -> list[str]:
        errors = []
        sample_no = str(values.get("留样编号", "")).strip()
        existing = [r for r in store.rows(self.MODULE)
                    if r.get("留样编号") == sample_no]
        if existing:
            errors.append(f"留样编号 {sample_no} 已存在")
        weight = store.parse_weight(values.get("留样重量", 0))
        if values.get("留样重量") not in (None, "") and weight <= 0:
            errors.append("留样重量必须为正数（公斤），作为留样柜实物台账期初重量")
        shelf_life = str(values.get("保存期限", ""))[:10]
        try:
            dt = datetime.strptime(shelf_life, "%Y-%m-%d")
            if dt < datetime.now():
                errors.append("保存期限不能早于当前日期")
        except ValueError:
            pass
        # 校验关联的工序节点
        process_module = values.get("process_module")
        process_entry_id = values.get("process_entry_id")
        if process_module and process_entry_id is not None:
            process_entry = store.find(process_module, int(process_entry_id))
            if process_entry is None:
                errors.append(f"关联工序记录 {process_module}/{process_entry_id} 不存在")
        return errors

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        if action == "登记留样":
            process_module = entry.get("process_module")
            process_entry_id = entry.get("process_entry_id")
            if process_module and process_entry_id is not None:
                from app.services.pipeline import pipeline as pl
                samp, msg = pl.link_sample_to_node(
                    int(entry["id"]), process_module, int(process_entry_id))
                if samp:
                    entry["取样节点"] = samp.get("取样节点", "")
            # 登记确认后把留样实物入库，建立留样柜期初台账（种子数据已建流水则不重复）
            sample_no = str(entry.get("留样编号", "")).strip()
            weight = store.parse_weight(entry.get("留样重量", 0))
            if weight > 0 and store.has_sample_movement(f"sample-in-{entry['id']}") is None:
                store.add_sample_movement(
                    sample_no, "入库", weight, self.MODULE, int(entry["id"]),
                    request_id=f"sample-in-{entry['id']}",
                    basis=f"留样登记{sample_no}期初入库{weight}kg",
                )
            entry["柜内余量"] = store.sample_cabinet_balance(sample_no)
        return True, None

    def cabinet_snapshot(self, sample_no: str) -> dict[str, Any]:
        """留样柜实物快照：期初、支取、实际退回、当前可用与流水。"""
        return {
            "留样编号": sample_no,
            "留样登记重量": store.sample_initial_weight(sample_no),
            "累计支取": store.sample_total_drawn(sample_no),
            "累计实际退回": store.sample_total_returned(sample_no),
            "当前柜内可用": store.sample_cabinet_balance(sample_no),
            "流水": store.sample_movements(sample_no),
        }

    def is_expired(self, entry: dict[str, Any]) -> bool:
        """判断样品是否超过保存期限。"""
        try:
            shelf_life = str(entry.get("保存期限", ""))[:10]
            return datetime.strptime(shelf_life, "%Y-%m-%d") < datetime.now()
        except ValueError:
            return False

    def get_samples_by_node(self, process_module: str, process_entry_id: int) -> list[dict[str, Any]]:
        """获取关联到指定工序节点的留样记录。"""
        rows = store.rows(self.MODULE)
        node_label = f"{process_module}#{process_entry_id}"
        return [r for r in rows if node_label in str(r.get("取样节点", ""))]

    def get_samples_by_process(self, process_module: str) -> list[dict[str, Any]]:
        """获取关联到指定工序模块的留样记录。"""
        rows = store.rows(self.MODULE)
        results = []
        for r in rows:
            node = str(r.get("取样节点", ""))
            if node:
                from app.services.pipeline import PROCESS_NAMES
                if process_module in node or PROCESS_NAMES.get(process_module, "") in node:
                    results.append(r)
        return results
