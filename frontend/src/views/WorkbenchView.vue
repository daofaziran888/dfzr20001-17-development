<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button
          v-if="meta.key === 'testing_receipt'"
          class="btn primary"
          data-testid="open-import"
          type="button"
          @click="openImport"
        >
          导入签收结果文件
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <!-- 批次追溯链面板 -->
    <div v-if="showTrace && traceBatchNo" class="trace-panel">
      <h4>批次 {{ traceBatchNo }} 追溯链</h4>
      <div v-if="traceLoading">加载中...</div>
      <div v-else-if="traceError" class="error-text">{{ traceError }}</div>
      <div v-else class="trace-chain-inline">
        <div v-for="(node, idx) in traceChain" :key="idx" class="trace-node-inline"
          :class="{ frozen: node.frozen }">
          <div class="node-index">{{ idx + 1 }}</div>
          <div class="node-info">
            <strong>{{ getProcessName(node.module) }}</strong>
            <span>ID:{{ node.entry_id }}</span>
            <span class="node-status-tag" :class="node.status">{{ node.status }}</span>
          </div>
          <div v-if="node.data" class="node-meta">
            <span v-if="node.data['鲜叶重量'] || node.data['投入重量']">
              重量: {{ node.data['鲜叶重量'] || node.data['投入重量'] }}kg
            </span>
            <span v-if="node.data['产出重量']">→ 产出: {{ node.data['产出重量'] }}kg</span>
          </div>
          <div v-if="idx < traceChain.length - 1" class="trace-arrow">→</div>
        </div>
      </div>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`"
          :data-status="String(row.status)"
          :class="{ 'conflict-row': meta.key === 'sample_return' && row['冲突标记'] }">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="row['批次号']"
              class="link trace-link"
              type="button"
              @click="viewBatchTrace(String(row['批次号']))"
              title="查看批次追溯">
              批次追溯
            </button>
            <button
              v-if="isProcessModule && row['上游记录ID']"
              class="link upstream-link"
              type="button"
              @click="viewUpstream(row)" title="查看上游工序">
              查看上游
            </button>
            <button
              v-if="meta.key === 'sample' && row['留样编号']"
              class="link cabinet-link"
              type="button"
              :data-testid="`cabinet-${row.id}`"
              @click="openCabinet(String(row['留样编号']))"
              title="查看留样柜实物台账">
              留样柜台账
            </button>
            <button
              v-if="meta.key === 'testing_receipt'"
              class="link cabinet-link"
              type="button"
              :data-testid="`receipt-detail-${row.id}`"
              @click="openReceiptDetail(Number(row.id))"
              title="查回原送检文件与最终受理值">
              查回原文件
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 检测机构签收结果文件导入 -->
    <div v-if="showImport" class="modal-backdrop" data-testid="import-modal">
      <form class="modal import-modal" @submit.prevent="submitImport">
        <h3>导入检测机构签收结果文件</h3>
        <p class="modal-tip">只受理原交检凭据内的留样编号与所需检测机构；文件里其他茶批报告将被拒收，不进入本次样品交检账。</p>
        <label class="create-field">
          <span>文件名</span>
          <input v-model="importForm.文件名" data-testid="import-filename" required />
        </label>
        <label class="create-field">
          <span>文件指纹（去重键）</span>
          <input v-model="importForm.文件指纹" data-testid="import-hash" required />
        </label>
        <label class="create-field">
          <span>检测机构</span>
          <input v-model="importForm.检测机构" data-testid="import-lab" required />
        </label>
        <label class="create-field">
          <span>报告明细（每行一条 JSON）</span>
          <textarea
            v-model="importRowsText"
            data-testid="import-rows"
            rows="8"
            :placeholder="importRowsPlaceholder"
            required
          ></textarea>
        </label>
        <div v-if="importResult" class="import-result" :class="{ dedup: importResult.deduplicated }">
          <p>{{ importResult.message }}</p>
          <p v-if="importResult.deduplicated" class="dedup-text">重复文件未追加报告，已返回原送检文件与最终受理值。</p>
          <ul v-if="importResult.accepted?.length">
            <li v-for="item in importResult.accepted" :key="item.报告编号">
              受理：{{ item.留样编号 }} / {{ item.报告编号 }}（签收状态：{{ item.签收状态 }}）
            </li>
          </ul>
          <ul v-if="importResult.rejected?.length" class="reject-list">
            <li v-for="item in importResult.rejected" :key="`${item.报告编号}-${item.留样编号}`">
              拒收：{{ item.留样编号 }} / {{ item.报告编号 }} —— {{ item.拒收原因 }}
            </li>
          </ul>
        </div>
        <div class="page-actions">
          <button class="btn" type="button" @click="showImport = false">关闭</button>
          <button class="btn primary" data-testid="submit-import" type="submit" :disabled="importing">
            {{ importing ? '导入中…' : '确认导入' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 留样柜实物台账 -->
    <div v-if="showCabinet" class="modal-backdrop" data-testid="cabinet-modal">
      <div class="modal">
        <h3>留样柜实物台账：{{ cabinetData?.留样编号 }}</h3>
        <div v-if="cabinetData" class="cabinet-grid">
          <span>留样登记重量：<strong>{{ cabinetData.留样登记重量 }}kg</strong></span>
          <span>累计支取：<strong>{{ cabinetData.累计支取 }}kg</strong></span>
          <span>累计实际退回：<strong>{{ cabinetData.累计实际退回 }}kg</strong></span>
          <span>当前柜内可用：<strong class="balance">{{ cabinetData.当前柜内可用 }}kg</strong></span>
        </div>
        <table v-if="cabinetData" class="data-table mini-table">
          <thead>
            <tr><th>流水类型</th><th>实物重量</th><th>重量依据</th></tr>
          </thead>
          <tbody>
            <tr v-for="(m, idx) in cabinetData.流水" :key="idx">
              <td>{{ m.movement }}</td>
              <td>{{ m.weight }}kg</td>
              <td>{{ m.basis }}</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="showCabinet = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 签收文件查回 -->
    <div v-if="showReceiptDetail" class="modal-backdrop" data-testid="receipt-modal">
      <div class="modal">
        <h3>原送检文件查回：{{ receiptDetail?.文件名 }}</h3>
        <div v-if="receiptDetail">
          <p>文件指纹：{{ receiptDetail.文件指纹 }} ｜ 检测机构：{{ receiptDetail.检测机构 }}</p>
          <h4>最终受理值（{{ receiptDetail.最终受理值.length }}）</h4>
          <ul>
            <li v-for="item in receiptDetail.最终受理值" :key="item.报告编号">
              {{ item.留样编号 }} / {{ item.报告编号 }} / 受理重量 {{ item.受理重量 }}kg
            </li>
          </ul>
          <h4>拒收明细（{{ receiptDetail.拒收明细.length }}）</h4>
          <ul class="reject-list">
            <li v-for="item in receiptDetail.拒收明细" :key="`${item.报告编号}-${item.留样编号}`">
              {{ item.留样编号 }} / {{ item.报告编号 }} —— {{ item.拒收原因 }}
            </li>
          </ul>
        </div>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="showReceiptDetail = false">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import { fetchTraceChain } from '@/api/pipeline-client'
import {
  fetchCabinetSnapshot,
  fetchReceiptDetail,
  importReceiptFile,
  type CabinetSnapshot,
  type ReceiptImportResult,
  type ReceiptRow,
} from '@/api/sample-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const router = useRouter()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

// 工序模块判断
const PROCESS_ORDER = ['picking', 'withering', 'fixing', 'rolling', 'fermentation', 'drying',
  'grading', 'blending', 'packing', 'trace_code', 'shipment']
const PROCESS_NAMES: Record<string, string> = {
  picking: '鲜叶采摘', withering: '萎凋', fixing: '杀青',
  rolling: '揉捻', fermentation: '发酵', drying: '干燥',
  grading: '精制分级', blending: '拼配', packing: '包装',
  trace_code: '溯源码段', shipment: '发运交接',
}
const isProcessModule = computed(() => PROCESS_ORDER.includes(meta.key))
// 批次追溯
const showTrace = ref(false)
const traceBatchNo = ref('')
const traceChain = ref<any[]>([])
const traceLoading = ref(false)
const traceError = ref('')

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function getProcessName(module: string): string {
  return PROCESS_NAMES[module] || module
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

async function viewBatchTrace(batchNo: string) {
  traceBatchNo.value = batchNo
  showTrace.value = true
  traceLoading.value = true
  traceError.value = ''
  try {
    const result = await fetchTraceChain(batchNo)
    traceChain.value = (result.chain || []).map((node: any) => ({
      ...node,
      frozen: false,
      status: node.data?.status || '',
    }))
  } catch (err: any) {
    traceError.value = err.message || '追溯链查询失败'
    traceChain.value = []
  } finally {
    traceLoading.value = false
  }
}

function viewUpstream(row: EntryRow) {
  const upstreamModule = row['上游工序']
  const upstreamId = row['上游记录ID']
  if (upstreamModule && upstreamId) {
    void router.push({ name: 'module', params: { moduleKey: String(upstreamModule) } })
  }
}

// ── 签收结果文件导入 ──

const showImport = ref(false)
const importing = ref(false)
const importResult = ref<ReceiptImportResult | null>(null)
const importForm = ref({ 文件名: '', 文件指纹: '', 检测机构: '' })
const importRowsText = ref('')
const importRowsPlaceholder = JSON.stringify(
  [{ 留样编号: 'SAMP-0001', 报告编号: 'RPT-0001', 农残项目: '农残合格', 签发日期: '2026-11-20', 受理重量: '0.6' }],
  null,
  2,
)

function openImport() {
  importForm.value = { 文件名: '', 文件指纹: '', 检测机构: '' }
  importRowsText.value = ''
  importResult.value = null
  errorMessage.value = ''
  showImport.value = true
}

async function submitImport() {
  importing.value = true
  errorMessage.value = ''
  try {
    let rows: ReceiptRow[]
    try {
      rows = JSON.parse(importRowsText.value) as ReceiptRow[]
      if (!Array.isArray(rows)) throw new Error('not-array')
    } catch {
      throw new Error('报告明细需为 JSON 数组，每行包含留样编号、报告编号等字段')
    }
    importResult.value = await importReceiptFile({
      文件名: importForm.value.文件名,
      文件指纹: importForm.value.文件指纹,
      检测机构: importForm.value.检测机构,
      报告明细: rows,
    })
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '签收结果导入失败'
  } finally {
    importing.value = false
  }
}

// ── 留样柜实物台账 ──

const showCabinet = ref(false)
const cabinetData = ref<CabinetSnapshot | null>(null)

async function openCabinet(sampleNo: string) {
  errorMessage.value = ''
  try {
    cabinetData.value = await fetchCabinetSnapshot(sampleNo)
    showCabinet.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '留样柜台账读取失败'
  }
}

// ── 签收文件查回 ──

const showReceiptDetail = ref(false)
const receiptDetail = ref<Awaited<ReturnType<typeof fetchReceiptDetail>> | null>(null)

async function openReceiptDetail(receiptId: number) {
  errorMessage.value = ''
  try {
    receiptDetail.value = await fetchReceiptDetail(receiptId)
    showReceiptDetail.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '受理文件查回失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.trace-panel {
  margin-bottom: 12px;
  padding: 12px 16px;
  background: #f3f8ff;
  border-radius: 6px;
  border-left: 3px solid #3498db;
}
.trace-panel h4 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #2c3e50;
}
.trace-chain-inline {
  display: flex;
  align-items: flex-start;
  overflow-x: auto;
  gap: 4px;
  padding: 8px 0;
}
.trace-node-inline {
  flex: 0 0 auto;
  min-width: 130px;
  padding: 8px;
  background: white;
  border-radius: 4px;
  border: 1px solid #ddd;
  font-size: 11px;
}
.trace-node-inline.frozen {
  border-color: #e74c3c;
  background: #fce4ec;
}
.node-index {
  width: 18px;
  height: 18px;
  background: #3498db;
  color: white;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  margin-right: 4px;
}
.node-info strong {
  display: block;
  font-size: 11px;
}
.node-info span {
  display: block;
  color: #7f8c8d;
  font-size: 10px;
}
.node-status-tag {
  padding: 1px 4px;
  border-radius: 2px;
  font-size: 9px;
  display: inline-block;
}
.node-status-tag.已确认 { background: #d4edda; color: #155724; }
.node-status-tag.草稿 { background: #fff3cd; color: #856404; }
.node-status-tag.待确认 { background: #cce5ff; color: #004085; }
.node-status-tag.已撤回 { background: #f8d7da; color: #721c24; }
.node-meta {
  margin-top: 2px;
  font-size: 9px;
  color: #7f8c8d;
}
.trace-arrow {
  color: #3498db;
  font-size: 14px;
  margin: 0 2px;
  padding-top: 8px;
}
.trace-link, .upstream-link {
  color: #27ae60 !important;
  margin-left: 6px;
  padding: 2px 6px;
  font-size: 11px;
  border: 1px solid #27ae60;
  border-radius: 3px;
  text-decoration: none;
  cursor: pointer;
}
.trace-link:hover, .upstream-link:hover {
  background: #27ae60;
  color: white !important;
}
.cabinet-link {
  color: #8e44ad !important;
  margin-left: 6px;
  padding: 2px 6px;
  font-size: 11px;
  border: 1px solid #8e44ad;
  border-radius: 3px;
  cursor: pointer;
}
.cabinet-link:hover {
  background: #8e44ad;
  color: white !important;
}
.conflict-row {
  background: #fff5e6 !important;
}
.conflict-row td {
  border-top: 1px solid #f0ad4e;
}
.modal {
  max-height: 86vh;
  overflow-y: auto;
}
.import-modal textarea {
  width: 100%;
  font-family: monospace;
  font-size: 12px;
  padding: 8px;
  border: 1px solid #cfd8dc;
  border-radius: 4px;
  resize: vertical;
}
.modal-tip {
  font-size: 12px;
  color: #7f8c8d;
  background: #f3f8ff;
  padding: 8px;
  border-radius: 4px;
}
.import-result {
  margin: 10px 0;
  padding: 10px;
  border-radius: 4px;
  background: #eafaf1;
  border: 1px solid #27ae60;
  font-size: 12px;
}
.import-result.dedup {
  background: #fef9e7;
  border-color: #f1c40f;
}
.dedup-text {
  color: #b9770e;
}
.reject-list {
  color: #c0392b;
}
.cabinet-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  margin: 10px 0;
  padding: 10px;
  background: #f8f9fa;
  border-radius: 4px;
  font-size: 13px;
}
.cabinet-grid .balance {
  color: #27ae60;
  font-size: 15px;
}
.mini-table {
  font-size: 12px;
}
.mini-table th, .mini-table td {
  padding: 6px 8px;
}
</style>