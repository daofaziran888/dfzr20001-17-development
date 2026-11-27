/** 留样送检专用 API：签收结果文件导入、原文件查回、留样柜实物台账。 */
import { request } from './client'

export interface ReceiptRow {
  留样编号: string
  报告编号: string
  检测机构?: string
  农残项目?: string
  签发日期?: string
  受理重量?: string | number
  关联送检单号?: string
  检测报告ID?: number
  签收状态?: string
}

export interface ReceiptImportResult {
  ok: boolean
  message: string
  deduplicated?: boolean
  receipt_id?: number
  accepted: ReceiptRow[]
  rejected: { 留样编号: string; 报告编号: string; 拒收原因: string }[]
  original_file?: Record<string, unknown>
}

export async function importReceiptFile(payload: {
  文件名: string
  文件指纹: string
  检测机构: string
  文件编号?: string
  负责人?: string
  备注说明?: string
  报告明细: ReceiptRow[]
}): Promise<ReceiptImportResult> {
  const response = await request('/api/testing_receipt/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: '签收结果导入失败' }))
    throw new Error(detail.detail || '签收结果导入失败')
  }
  return response.json()
}

export async function fetchReceiptDetail(receiptId: number): Promise<{
  id: number
  文件编号: string
  文件名: string
  文件指纹: string
  检测机构: string
  原始送检文件: Record<string, unknown>
  最终受理值: ReceiptRow[]
  拒收明细: { 留样编号: string; 报告编号: string; 拒收原因: string }[]
}> {
  const response = await request(`/api/testing_receipt/${receiptId}/detail`)
  if (!response.ok) throw new Error('受理文件查回失败')
  return response.json()
}

export interface CabinetSnapshot {
  留样编号: string
  留样登记重量: number
  累计支取: number
  累计实际退回: number
  当前柜内可用: number
  流水: {
    sample_no: string
    movement: string
    weight: number
    ref_module: string
    ref_id: number
    request_id: string
    basis: string
    ts: number
  }[]
  交检账: Record<string, string | number>[]
  退回记录: Record<string, string | number>[]
}

export async function fetchCabinetSnapshot(sampleNo: string): Promise<CabinetSnapshot> {
  const response = await request(`/api/sample/${encodeURIComponent(sampleNo)}/cabinet`)
  if (!response.ok) throw new Error('留样柜实物台账读取失败')
  return response.json()
}
