"""留样送检与检测机构签收结果导入的跨模块业务测试。

覆盖需求：
1. 留样送检文件与签收结果导入两个新模块；
2. 检测工作台只从关联样品交检账签收报告；
3. 导入仅受理原交检凭据内的留样编号与检测机构，其他茶批报告拒收；
4. 同一瓶茶样的退回/签收冲突时两条办理都先不落账；
5. 原样部分耗尽时补做可用量仅按实际退回重量；
6. 重复导入同文件不追加报告，保留原送检文件与最终受理值；
7. 再支取余样需等前一个交检结果；
8. 退回记录与报告明示实物重量依据，重复退回不增加余量。
"""
from __future__ import annotations

import unittest

from app.store import store
from app.services import service_for


class SampleDispatchTestBase(unittest.TestCase):
    """每个用例使用独立编号，避免内存仓库跨用例撞号。"""

    TAG = "BASE"

    def setUp(self) -> None:
        # 清理冻结等全局污染，保证用例相互独立
        store._frozen.clear()
        self.sample_svc = service_for("sample")
        self.dispatch_svc = service_for("sample_dispatch")
        self.return_svc = service_for("sample_return")
        self.receipt_svc = service_for("testing_receipt")
        self.testing_svc = service_for("testing")

    def register_sample(self, tag: str, weight: str = "1.0") -> str:
        sample_no = f"SAMP-{tag}"
        entry, errors = self.sample_svc.create_entry({
            "留样编号": sample_no,
            "样品形态": "散茶",
            "留样重量": weight,
            "保存期限": "2027-12-31",
        })
        self.assertIsNotNone(entry, errors)
        entry["status"] = "待确认"
        entry, message = self.sample_svc.run_action(int(entry["id"]), "登记留样")
        self.assertEqual(entry["status"], "已确认", message)
        return sample_no

    def create_dispatch(self, tag: str, sample_no: str, lab: str = "机构A",
                        weight: str = "0.6", project: str = "农残"):
        return self.dispatch_svc.create_entry({
            "送检单号": f"DISP-{tag}",
            "留样编号": sample_no,
            "检测机构": lab,
            "检测项目": project,
            "支取重量": weight,
        })

    def confirm_dispatch(self, entry) -> dict:
        entry["status"] = "待确认"
        confirmed, message = self.dispatch_svc.run_action(int(entry["id"]), "确认交检")
        self.assertIsNotNone(confirmed, message)
        self.assertEqual(confirmed["status"], "已确认")
        return confirmed


class TestSampleCabinetLedger(SampleDispatchTestBase):
    """留样柜实物台账：入库、支取、余量口径。"""

    def test_sample_registration_stocks_cabinet(self):
        sample_no = self.register_sample("CAB-1", "1.5")
        self.assertEqual(store.sample_cabinet_balance(sample_no), 1.5)
        snapshot = self.sample_svc.cabinet_snapshot(sample_no)
        self.assertEqual(snapshot["留样登记重量"], 1.5)
        self.assertEqual(snapshot["累计支取"], 0.0)

    def test_dispatch_draws_physical_weight_once(self):
        sample_no = self.register_sample("CAB-2", "1.0")
        dispatch, errors = self.create_dispatch("CAB-2", sample_no, weight="0.6")
        self.assertIsNotNone(dispatch, errors)
        self.confirm_dispatch(dispatch)
        self.assertEqual(store.sample_cabinet_balance(sample_no), 0.4)
        # 重复确认同一交检单不得重复支取
        again, _ = self.dispatch_svc.run_action(int(dispatch["id"]), "确认交检")
        self.assertEqual(store.sample_cabinet_balance(sample_no), 0.4)
        self.assertEqual(store.sample_total_drawn(sample_no), 0.6)

    def test_overdraw_is_rejected(self):
        sample_no = self.register_sample("CAB-3", "0.5")
        dispatch, errors = self.create_dispatch("CAB-3", sample_no, weight="0.8")
        self.assertIsNone(dispatch)
        self.assertTrue(any("可用实物仅 0.5" in e for e in errors))


class TestDispatchReceiptImport(SampleDispatchTestBase):
    """签收结果文件导入：受理范围、拒收、去重、草稿。"""

    def test_import_accepts_only_original_credential(self):
        sample_no = self.register_sample("IMP-1")
        dispatch, errors = self.create_dispatch("IMP-1", sample_no)
        self.assertIsNotNone(dispatch, errors)
        self.confirm_dispatch(dispatch)

        result = self.receipt_svc.import_receipt_file({
            "文件名": "rcpt-imp-1.json",
            "文件指纹": "hash-imp-1",
            "检测机构": "机构A",
            "报告明细": [
                {"留样编号": sample_no, "报告编号": "RPT-IMP-1",
                 "农残项目": "农残合格", "签发日期": "2026-11-20", "受理重量": "0.6"},
                {"留样编号": "SAMP-NOT-HERE", "报告编号": "RPT-IMP-X",
                 "农残项目": "其他茶批", "签发日期": "2026-11-20"},
                {"留样编号": sample_no, "报告编号": "RPT-IMP-Y",
                 "检测机构": "机构B", "农残项目": "机构不符", "签发日期": "2026-11-20"},
            ],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(len(result["rejected"]), 2)
        # 其他茶批与机构不符的报告不能进入交检账
        self.assertTrue(any("交检账" in r["拒收原因"] for r in result["rejected"]))
        self.assertTrue(any("机构" in r["拒收原因"] for r in result["rejected"]))

        report = next(r for r in store.rows("testing") if r["报告编号"] == "RPT-IMP-1")
        # 导入只生成草稿，实际签收仍需在检测工作台办理
        self.assertEqual(report["status"], "草稿")
        self.assertEqual(report["关联送检单ID"], dispatch["id"])

    def test_duplicate_import_does_not_append_reports(self):
        sample_no = self.register_sample("IMP-2")
        dispatch, errors = self.create_dispatch("IMP-2", sample_no)
        self.assertIsNotNone(dispatch, errors)
        self.confirm_dispatch(dispatch)

        payload = {
            "文件名": "rcpt-imp-2.json", "文件指纹": "hash-imp-2", "检测机构": "机构A",
            "报告明细": [
                {"留样编号": sample_no, "报告编号": "RPT-IMP-2",
                 "农残项目": "农残合格", "签发日期": "2026-11-20"},
            ],
        }
        first = self.receipt_svc.import_receipt_file(payload)
        self.assertFalse(first["deduplicated"])
        before = len(store.rows("testing"))

        second = self.receipt_svc.import_receipt_file(payload)
        self.assertTrue(second["deduplicated"])
        self.assertEqual(len(store.rows("testing")), before)
        self.assertEqual(second["receipt_id"], first["receipt_id"])

        # 原送检文件与最终受理值可供品控查回
        detail = self.receipt_svc.get_receipt_detail(first["receipt_id"])
        self.assertEqual(detail["原始送检文件"]["文件名"], "rcpt-imp-2.json")
        self.assertEqual(detail["最终受理值"][0]["报告编号"], "RPT-IMP-2")


class TestReportSignoffFromDispatch(SampleDispatchTestBase):
    """检测工作台签收报告：仅适用实际成功交检的样品。"""

    def test_signoff_requires_confirmed_dispatch(self):
        # 无交检账的留样直接登记报告应被拒
        sample_no = self.register_sample("SIGN-1")
        report, errors = self.testing_svc.create_entry({
            "农残项目": "农残", "检测机构": "机构A", "报告编号": "RPT-SIGN-BAD",
            "签发日期": "2026-11-20", "留样编号": sample_no,
        })
        self.assertIsNone(report)
        self.assertTrue(any("交检账" in e for e in errors))

    def test_imported_draft_can_be_signed_with_weight_basis(self):
        sample_no = self.register_sample("SIGN-2")
        dispatch, errors = self.create_dispatch("SIGN-2", sample_no, weight="0.6")
        self.assertIsNotNone(dispatch, errors)
        self.confirm_dispatch(dispatch)

        self.receipt_svc.import_receipt_file({
            "文件名": "rcpt-sign-2.json", "文件指纹": "hash-sign-2", "检测机构": "机构A",
            "报告明细": [
                {"留样编号": sample_no, "报告编号": "RPT-SIGN-2",
                 "农残项目": "农残合格", "签发日期": "2026-11-20", "受理重量": "0.6"},
            ],
        })
        report = next(r for r in store.rows("testing") if r["报告编号"] == "RPT-SIGN-2")
        signed, message = self.testing_svc.run_action(int(report["id"]), "签收报告")
        self.assertIsNotNone(signed, message)
        self.assertEqual(signed["status"], "已确认")
        # 报告明示实物重量依据与受理重量
        self.assertEqual(signed["受理重量"], "0.6")
        self.assertIn("累计支取", signed["实物重量依据"])
        self.assertTrue(self.receipt_svc.signed_report_exists(int(dispatch["id"])))


class TestReturnConflictAndPhysicalWeight(SampleDispatchTestBase):
    """同一瓶茶样的冲突办理与实物重量口径。"""

    def _prepare_two_dispatches(self, tag: str) -> str:
        sample_no = self.register_sample(tag)
        first, errors = self.create_dispatch(f"{tag}-1", sample_no, weight="0.6")
        self.assertIsNotNone(first, errors)
        self.confirm_dispatch(first)
        # 第一张报告签收后才能再支取
        self.receipt_svc.import_receipt_file({
            "文件名": f"rcpt-{tag}-1.json", "文件指纹": f"hash-{tag}-1", "检测机构": "机构A",
            "报告明细": [
                {"留样编号": sample_no, "报告编号": f"RPT-{tag}-1",
                 "农残项目": "合格", "签发日期": "2026-11-20", "受理重量": "0.6"},
            ],
        })
        report = next(r for r in store.rows("testing") if r["报告编号"] == f"RPT-{tag}-1")
        self.testing_svc.run_action(int(report["id"]), "签收报告")

        second, errors = self.create_dispatch(f"{tag}-2", sample_no, weight="0.2",
                                              project="重金属")
        self.assertIsNotNone(second, errors)
        self.confirm_dispatch(second)
        return sample_no

    def test_conflicting_return_waits_and_uses_actual_returned_weight(self):
        tag = "CONF-1"
        sample_no = self._prepare_two_dispatches(tag)
        self.assertEqual(store.sample_cabinet_balance(sample_no), 0.2)

        # 品控员导入第二份签章结果（草稿，未签收）
        self.receipt_svc.import_receipt_file({
            "文件名": f"rcpt-{tag}-2.json", "文件指纹": f"hash-{tag}-2", "检测机构": "机构A",
            "报告明细": [
                {"留样编号": sample_no, "报告编号": f"RPT-{tag}-2",
                 "农残项目": "重金属合格", "签发日期": "2026-11-21", "受理重量": "0.2"},
            ],
        })

        # 样品管理员同时提交余样退回：两条办理均未落实物账，退回挂冲突
        ret, errors = self.return_svc.create_entry({
            "退回单号": f"RET-{tag}", "留样编号": sample_no,
            "关联送检单号": f"DISP-{tag}-2", "实际退回重量": "0.15",
        })
        self.assertIsNotNone(ret, errors)
        ret, message = self.return_svc.run_action(int(ret["id"]), "提交退回")
        self.assertEqual(ret["status"], "待确认")
        self.assertTrue(ret["冲突标记"])
        # 尚未落账，柜内余量不变
        self.assertEqual(store.sample_cabinet_balance(sample_no), 0.2)

        # 实际办理退回后，仅按留样柜实收 0.15kg 增加余量
        ret, message = self.return_svc.run_action(int(ret["id"]), "办理退回")
        self.assertIsNotNone(ret, message)
        self.assertEqual(ret["status"], "已确认")
        self.assertEqual(store.sample_cabinet_balance(sample_no), 0.35)
        self.assertIn("留样柜实收", ret["实物重量依据"])

    def test_repeat_return_is_idempotent_and_cannot_exceed_initial(self):
        tag = "CONF-2"
        sample_no = self._prepare_two_dispatches(tag)

        ret, errors = self.return_svc.create_entry({
            "退回单号": f"RET-{tag}-1", "留样编号": sample_no,
            "关联送检单号": f"DISP-{tag}-2", "实际退回重量": "0.1",
        })
        self.assertIsNotNone(ret, errors)
        ret["status"] = "待确认"
        ret, _ = self.return_svc.run_action(int(ret["id"]), "办理退回")
        self.assertEqual(store.sample_cabinet_balance(sample_no), 0.3)

        # 重复办理同一退回单不重复增加余量
        self.return_svc.run_action(int(ret["id"]), "办理退回")
        self.assertEqual(store.sample_cabinet_balance(sample_no), 0.3)

        # 另一入口想把余量抬过留样登记重量，必须拒绝
        bad, errors = self.return_svc.create_entry({
            "退回单号": f"RET-{tag}-2", "留样编号": sample_no,
            "关联送检单号": f"DISP-{tag}-2", "实际退回重量": "0.9",
        })
        self.assertIsNotNone(bad, errors)
        bad["status"] = "待确认"
        bad, message = self.return_svc.run_action(int(bad["id"]), "办理退回")
        self.assertIsNone(bad)
        self.assertIn("超过留样登记重量", message)
        self.assertEqual(store.sample_cabinet_balance(sample_no), 0.3)

    def test_return_cannot_exceed_dispatch_remaining(self):
        tag = "CONF-3"
        sample_no = self._prepare_two_dispatches(tag)
        # DISP-CONF-3-2 只支取 0.2，试图退回 0.25 应被拒（原样已部分耗尽）
        ret, errors = self.return_svc.create_entry({
            "退回单号": f"RET-{tag}", "留样编号": sample_no,
            "关联送检单号": f"DISP-{tag}-2", "实际退回重量": "0.25",
        })
        self.assertIsNotNone(ret, errors)
        ret["status"] = "待确认"
        ret, message = self.return_svc.run_action(int(ret["id"]), "办理退回")
        self.assertIsNone(ret)
        self.assertIn("最多再退回", message)


class TestRedrawWaitsForPriorResult(SampleDispatchTestBase):
    """再试支取余样给另一个检测项目：需等前一个交检结果。"""

    def test_redraw_blocked_until_prior_report_signed(self):
        sample_no = self.register_sample("WAIT-1")
        first, errors = self.create_dispatch("WAIT-1", sample_no, weight="0.4")
        self.assertIsNotNone(first, errors)
        self.confirm_dispatch(first)

        # 前单未签收：再支取直接被拦
        second, errors = self.create_dispatch("WAIT-1-2", sample_no, weight="0.1",
                                              project="重金属")
        self.assertIsNone(second)
        self.assertTrue(any("等前一个交检结果" in e for e in errors))

        # 导入并签收前单报告
        self.receipt_svc.import_receipt_file({
            "文件名": "rcpt-wait-1.json", "文件指纹": "hash-wait-1", "检测机构": "机构A",
            "报告明细": [
                {"留样编号": sample_no, "报告编号": "RPT-WAIT-1",
                 "农残项目": "合格", "签发日期": "2026-11-20", "受理重量": "0.4"},
            ],
        })
        report = next(r for r in store.rows("testing") if r["报告编号"] == "RPT-WAIT-1")
        self.testing_svc.run_action(int(report["id"]), "签收报告")

        # 签收后可按实际柜内余量再支取
        second, errors = self.create_dispatch("WAIT-1-2", sample_no, weight="0.1",
                                              project="重金属")
        self.assertIsNotNone(second, errors)
        self.confirm_dispatch(second)
        self.assertEqual(store.sample_cabinet_balance(sample_no), 0.5)


if __name__ == "__main__":
    unittest.main()
