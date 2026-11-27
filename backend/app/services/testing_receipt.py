"""检测机构签收结果导入领域服务。

品控员导入检测机构签章后回传的签收材料文件。导入规则：
1. 只接受原交检凭据（样品交检账）里的留样编号，且检测机构必须与送检单
   「所需的检测机构」完全一致；文件里多出的其他茶批报告一律拒收，
   不进入本次样品交检账。
2. 受理的报告在检测报告模块生成/刷新为「草稿」，必须重新打开草稿做实际
   签收，导入动作本身不签收报告。
3. 同一文件按文件指纹去重：重复导入只回传原送检文件与最终受理值，
   不再追加报告。
"""
from __future__ import annotations

import copy
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TestingReceiptService(BaseModuleService):
    MODULE = "testing_receipt"
    ENTITY = "签收结果文件"
    OPERATION_TERM = "确认导入"
    LIST_FIELDS = [
        "文件编号", "文件名", "文件指纹", "检测机构", "受理报告数",
        "拒收报告数", "登记编号", "负责人", "生效版本", "备注说明",
    ]
    REQUIRED_FIELDS = ["文件编号", "文件名", "检测机构"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"确认导入": "已确认", "提交确认": "待确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    # ── 文件导入主流程 ──

    def import_receipt_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        """导入一份签收材料，返回受理/拒收明细与去重结论。"""
        file_hash = str(payload.get("文件指纹") or payload.get("file_hash") or "").strip()
        file_name = str(payload.get("文件名") or payload.get("file_name") or "").strip()
        lab = str(payload.get("检测机构") or payload.get("lab") or "").strip()
        rows = payload.get("报告明细") or payload.get("rows") or []

        if not file_name:
            return {"ok": False, "message": "缺少文件名", "accepted": [], "rejected": []}
        if not lab:
            return {"ok": False, "message": "缺少检测机构", "accepted": [], "rejected": []}

        # 去重：同指纹文件重复导入不追加报告，回传原送检文件和最终受理值
        existing = self._find_by_hash(file_hash) if file_hash else None
        if existing is not None:
            return {
                "ok": True,
                "message": f"文件 {file_name} 已导入过，未重复追加报告，请查回原送检文件",
                "deduplicated": True,
                "receipt_id": existing["id"],
                "accepted": copy.deepcopy(existing.get("最终受理值", [])),
                "rejected": copy.deepcopy(existing.get("拒收明细", [])),
                "original_file": copy.deepcopy(existing.get("原始送检文件", {})),
            }

        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        report_ids: list[int] = []

        for raw in rows:
            row = dict(raw)
            sample_no = str(row.get("留样编号", "")).strip()
            row_lab = str(row.get("检测机构", lab) or lab).strip()
            reason = self._reject_reason(sample_no, row_lab, lab)
            if reason:
                rejected.append({"留样编号": sample_no, "报告编号": row.get("报告编号", ""),
                                 "拒收原因": reason})
                continue
            dispatch = self._accepted_dispatch(sample_no, lab)
            report = self._upsert_testing_draft(row, dispatch)
            report_ids.append(int(report["id"]))
            accepted.append({
                "留样编号": sample_no,
                "报告编号": report.get("报告编号", ""),
                "检测机构": lab,
                "农残项目": report.get("农残项目", ""),
                "签发日期": report.get("签发日期", ""),
                "受理重量": report.get("受理重量", ""),
                "关联送检单号": dispatch.get("送检单号", ""),
                "检测报告ID": report["id"],
                "签收状态": report.get("status", ""),
            })

        rows_table = store.rows(self.MODULE)
        new_id = max((int(r.get("id", 0)) for r in rows_table), default=0) + 1
        file_no = str(payload.get("文件编号") or f"RCPT-{new_id:04d}")
        receipt = {
            "id": new_id,
            "文件编号": file_no,
            "文件名": file_name,
            "文件指纹": file_hash,
            "检测机构": lab,
            "受理报告数": len(accepted),
            "拒收报告数": len(rejected),
            "登记编号": file_no,
            "负责人": payload.get("负责人", ""),
            "生效版本": "v1",
            "备注说明": payload.get("备注说明", ""),
            # 品控查回：保留原送检文件与最终受理值
            "原始送检文件": copy.deepcopy(payload),
            "最终受理值": accepted,
            "拒收明细": rejected,
            "检测报告IDs": report_ids,
            "status": "已确认",
            "pending": False,
            "abnormal": len(rejected) > 0,
            "frozen": False,
        }
        rows_table.append(receipt)
        store.log_event(self.MODULE, new_id, "import",
                        {"file": file_name, "accepted": len(accepted),
                         "rejected": len(rejected), "hash": file_hash})

        message = f"文件 {file_name} 导入完成：受理 {len(accepted)} 份，拒收 {len(rejected)} 份"
        if rejected:
            message += "；拒收报告未进入本次样品交检账"
        return {"ok": True, "message": message, "deduplicated": False,
                "receipt_id": new_id, "accepted": accepted, "rejected": rejected,
                "original_file": copy.deepcopy(payload)}

    # ── 校验与查回 ──

    def _find_by_hash(self, file_hash: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("文件指纹") == file_hash:
                return row
        return None

    def _reject_reason(self, sample_no: str, row_lab: str, expected_lab: str) -> str:
        if not sample_no:
            return "文件行缺少留样编号"
        if row_lab != expected_lab:
            return f"检测机构 {row_lab} 与本次导入机构 {expected_lab} 不符"
        dispatch = self._accepted_dispatch(sample_no, expected_lab)
        if dispatch is None:
            return (
                f"留样编号 {sample_no} 在 {expected_lab} 名下无实际成功交检的样品交检账，"
                "其他茶批报告不得进入本次交检账"
            )
        return ""

    def _accepted_dispatch(self, sample_no: str, lab: str) -> dict[str, Any] | None:
        for row in store.rows("sample_dispatch"):
            if (row.get("留样编号") == sample_no
                    and str(row.get("检测机构", "")).strip() == lab
                    and row.get("status") == "已确认"):
                return row
        return None

    def _upsert_testing_draft(self, row: dict[str, Any],
                              dispatch: dict[str, Any]) -> dict[str, Any]:
        """受理行写入检测报告草稿；同报告编号重复导入只刷新草稿、不新增。"""
        testing_rows = store.rows("testing")
        report_no = str(row.get("报告编号", "")).strip()
        existing = next((r for r in testing_rows if r.get("报告编号") == report_no), None)
        sample_no = str(row.get("留样编号", "")).strip()
        accepted_weight = row.get("受理重量", dispatch.get("支取重量", ""))
        if existing is not None:
            # 已签收报告不被导入覆盖；未签收草稿刷新受理值
            if existing.get("status") == "已确认":
                return existing
            existing.update({
                "农残项目": row.get("农残项目", existing.get("农残项目", "")),
                "检测机构": dispatch.get("检测机构", ""),
                "签发日期": row.get("签发日期", existing.get("签发日期", "")),
                "留样编号": sample_no,
                "关联送检单ID": int(dispatch["id"]),
                "受理重量": accepted_weight,
                "实物重量依据": dispatch.get("实物重量依据", ""),
            })
            return existing

        new_id = max((int(r.get("id", 0)) for r in testing_rows), default=0) + 1
        report = {
            "id": new_id,
            "农残项目": row.get("农残项目", ""),
            "检测机构": dispatch.get("检测机构", ""),
            "报告编号": report_no,
            "签发日期": row.get("签发日期", ""),
            "登记编号": report_no or f"TEST-{new_id:04d}",
            "负责人": row.get("负责人", ""),
            "生效版本": "v1",
            "备注说明": row.get("备注说明", "由签收结果文件导入，待实际签收"),
            "留样编号": sample_no,
            "关联送检单ID": int(dispatch["id"]),
            "受理重量": accepted_weight,
            "实物重量依据": dispatch.get("实物重量依据", ""),
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "frozen": False,
        }
        testing_rows.append(report)
        return report

    def signed_report_exists(self, dispatch_id: int) -> bool:
        """该交检账是否已经有实际签收的检测报告。"""
        for row in store.rows("testing"):
            if int(row.get("关联送检单ID", 0) or 0) == dispatch_id and row.get("status") == "已确认":
                return True
        return False

    def get_receipt_detail(self, receipt_id: int) -> dict[str, Any] | None:
        """品控查回：原送检文件 + 最终受理值 + 拒收明细。"""
        receipt = store.find(self.MODULE, receipt_id)
        if receipt is None:
            return None
        return {
            "id": receipt["id"],
            "文件编号": receipt.get("文件编号", ""),
            "文件名": receipt.get("文件名", ""),
            "文件指纹": receipt.get("文件指纹", ""),
            "检测机构": receipt.get("检测机构", ""),
            "原始送检文件": copy.deepcopy(receipt.get("原始送检文件", {})),
            "最终受理值": copy.deepcopy(receipt.get("最终受理值", [])),
            "拒收明细": copy.deepcopy(receipt.get("拒收明细", [])),
        }
