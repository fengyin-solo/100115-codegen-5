<template>
  <section class="page" data-module="shorepower">
    <header class="page-head">
      <div>
        <h2>岸电接入计量</h2>
        <p class="page-desc">
          登记接电船舶与接电/断电时刻，按船公司与接电时段统计用电量与碳排放；
          同一船舶同一时段重复接电只算一次，缺断电时刻单独标出。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记接电</button>
        <button class="btn" type="button" @click="exportRows">导出接电记录</button>
      </div>
    </header>

    <!-- 看板卡片：数字全部来自 /dashboard，与下方用电记录同源 -->
    <div class="stat-row">
      <article v-for="card in board.cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ formatNum(card.value) }}</strong>
      </article>
    </div>

    <p v-if="check.ok" class="consistency-banner ok">
      对账一致：看板合计 {{ formatNum(check.dashboardKwh) }} kWh ＝ {{ check.meteredCount }} 笔用电记录合计
      {{ formatNum(check.recordsKwh) }} kWh（刷新后同步重算）
    </p>
    <p v-else-if="checked" class="consistency-banner bad">
      对账异常：看板合计 {{ formatNum(check.dashboardKwh) }} kWh ≠ 用电记录合计
      {{ formatNum(check.recordsKwh) }} kWh，请刷新重试
    </p>

    <div class="board-grid">
      <!-- 按接电时段分布 -->
      <article class="panel">
        <h3 class="panel-title">各接电时段用电量分布（按接电时刻归入 6 小时时段）</h3>
        <div class="bars">
          <div v-for="slot in board.bySlot" :key="slot['时段']" class="bar-item">
            <div class="bar-track">
              <div class="bar-fill" :style="{ height: barHeight(slot['用电量(kWh)']) }"></div>
            </div>
            <span class="bar-value">{{ formatNum(slot['用电量(kWh)']) }}</span>
            <span class="bar-label">{{ slot['时段'] }}</span>
            <span class="bar-sub">{{ slot['接电记录数'] }} 次接电</span>
          </div>
        </div>
      </article>

      <!-- 按日期分布 -->
      <article class="panel">
        <h3 class="panel-title">按日期用电量分布（kWh）</h3>
        <div class="line-chart">
          <div v-for="day in board.byDate" :key="day['日期']" class="line-col">
            <div class="line-bar" :style="{ height: lineHeight(day['用电量(kWh)']) }"></div>
            <span class="line-value">{{ formatNum(day['用电量(kWh)']) }}</span>
            <span class="line-label">{{ day['日期'].slice(5) }}</span>
          </div>
        </div>
      </article>
    </div>

    <!-- 按船公司汇总 -->
    <article class="panel" style="margin: 12px 0">
      <h3 class="panel-title">按船公司用电量明细</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="col in companyColumns" :key="col">{{ col }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in board.byCompany" :key="row['船公司']">
            <td v-for="col in companyColumns" :key="col">{{ formatNum(row[col]) }}</td>
          </tr>
          <tr v-if="!board.byCompany.length">
            <td :colspan="companyColumns.length" class="empty-state">该月份暂无已计量记录</td>
          </tr>
        </tbody>
      </table>
    </article>

    <!-- 换算口径 -->
    <article class="panel rules-panel" style="margin-bottom: 12px">
      <h3 class="panel-title">接电时长与用电量换算口径</h3>
      <ul class="rules-list">
        <li v-for="rule in orderedRules" :key="rule.label">
          <span class="rule-tag">{{ rule.label }}</span>{{ rule.text }}
        </li>
      </ul>
    </article>

    <form class="filter-bar" @submit.prevent="reloadAll">
      <label class="filter-item">
        <span>接电月份</span>
        <input v-model="month" placeholder="YYYY-MM" />
      </label>
      <label class="filter-item">
        <span>船公司</span>
        <input v-model="filters.company" placeholder="按船公司检索" />
      </label>
      <label class="filter-item">
        <span>关键字</span>
        <input v-model="filters.keyword" placeholder="船舶/接电单号/表计" />
      </label>
      <label class="filter-item">
        <span>计量状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="st in statuses" :key="st" :value="st">{{ st }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      <button class="btn ghost" type="button" @click="reloadAll">刷新看板</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="col in columns" :key="col">{{ col }}</th>
          <th>标记 / 表计说明</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'dup-row': row.duplicate }">
          <td v-for="col in columns" :key="col">
            <template v-if="col === '断电时刻' && row['缺断电时刻']">
              <span class="badge warn">缺断电时刻</span>
            </template>
            <template v-else-if="col === '计量状态'">
              <span class="badge" :class="badgeClass(row)">{{ row[col] }}</span>
            </template>
            <template v-else>{{ row[col] ?? '—' }}</template>
          </td>
          <td class="note-cell">{{ row['表计说明'] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="openAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!availableActions(row).length" class="muted-text">—</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无岸电接电记录，可先登记接电</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条接电记录 · 本页已计量用电量合计 {{ formatNum(pageKwh) }} kWh</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记 / 断电 / 手工抄录 共用表单 -->
    <div v-if="modal.open" class="modal-mask" @click.self="closeModal">
      <form class="modal" @submit.prevent="submitModal">
        <h3 class="modal-title">{{ modal.title }}</h3>

        <template v-if="modal.mode === 'create'">
          <label class="modal-field">
            <span>船舶名称 *</span>
            <input v-model="modal.form['船舶名称']" placeholder="如：中远海运狮子座" />
          </label>
          <label class="modal-field">
            <span>船公司 *</span>
            <input v-model="modal.form['船公司']" placeholder="如：中远海运集运" />
          </label>
          <label class="modal-field">
            <span>接电时刻 *</span>
            <input v-model="modal.form['接电时刻']" placeholder="YYYY-MM-DD HH:MM" />
          </label>
          <label class="modal-field">
            <span>断电时刻</span>
            <input v-model="modal.form['断电时刻']" placeholder="可后补；缺失时单独标出" />
          </label>
          <label class="modal-field">
            <span>计量表编号</span>
            <input v-model="modal.form['表计编号']" placeholder="如：MTR-101，可留空" />
          </label>
          <details class="modal-optional">
            <summary>已有人工抄表读数时补填（沿用原抄表方式）</summary>
            <label class="modal-field">
              <span>起始读数（kWh）</span>
              <input v-model="modal.form['起始读数']" placeholder="数字" />
            </label>
            <label class="modal-field">
              <span>结束读数（kWh）</span>
              <input v-model="modal.form['结束读数']" placeholder="需先填断电时刻" />
            </label>
          </details>
        </template>

        <template v-else-if="modal.mode === 'disconnect'">
          <p class="modal-tip">接电单 {{ modal.row?.['接电单号'] }}（{{ modal.row?.['船舶名称'] }}）</p>
          <label class="modal-field">
            <span>断电时刻 *</span>
            <input v-model="modal.form['断电时刻']" placeholder="YYYY-MM-DD HH:MM" />
          </label>
        </template>

        <template v-else-if="modal.mode === 'manual'">
          <p class="modal-tip">
            接电单 {{ modal.row?.['接电单号'] }}（{{ modal.row?.['船舶名称'] }}）· 手工抄录补数
          </p>
          <label class="modal-field">
            <span>起始读数（kWh）</span>
            <input v-model="modal.form['起始读数']" :placeholder="`原值 ${modal.row?.['起始读数'] ?? '无'}`" />
          </label>
          <label class="modal-field">
            <span>结束读数（kWh）*</span>
            <input v-model="modal.form['结束读数']" placeholder="数字，按读数差结算" />
          </label>
        </template>

        <template v-else>
          <p class="modal-tip">
            接电单 {{ modal.row?.['接电单号'] }}（{{ modal.row?.['船舶名称'] }}）· {{ modal.title }}
          </p>
          <p v-if="modal.mode === 'remote'" class="modal-hint">
            正在读取计量表 {{ modal.row?.['表计编号'] || '（未绑定）' }}；
            读不到数时会报明原因，可点下方按钮重试，也可改用手工抄录或时长折算。
          </p>
        </template>

        <div v-if="modal.message" class="modal-message" :class="{ ok: modal.ok }">{{ modal.message }}</div>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeModal">关闭</button>
          <button class="btn primary" type="submit">{{ submitLabel }}</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type Board = {
  cards: { label: string; value: number }[]
  totalKwh: number
  byCompany: Record<string, string | number>[]
  byDate: { '日期': string; '用电量(kWh)': number }[]
  bySlot: { '时段': string; '用电量(kWh)': number; '接电记录数': number }[]
  byCaliber: { '计量口径': string; '用电量(kWh)': number }[]
}

const ENDPOINT = '/api/shorepower'
const columns = [
  '接电单号', '船舶名称', '船公司', '接电时刻', '断电时刻', '接电时长(h)',
  '起始读数', '结束读数', '用电量(kWh)', '碳排放(kgCO2)', '计量口径', '计量状态',
]
const companyColumns = ['船公司', '记录数', '接电时长(h)', '用电量(kWh)', '碳排放(kgCO2)']
const statuses = ['接电中', '待抄表', '已计量', '重复接电']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const month = ref('2026-09')
const filters = ref({ keyword: '', company: '', status: '' })

const emptyBoard: Board = {
  cards: [], totalKwh: 0, byCompany: [], byDate: [], bySlot: [], byCaliber: [],
}
const board = ref<Board>(emptyBoard)
const check = ref({ ok: false, dashboardKwh: 0, recordsKwh: 0, meteredCount: 0 })
const checked = ref(false)
const rules = ref<Record<string, string>>({})

const modal = ref({
  open: false,
  mode: 'create',
  title: '',
  action: '',
  row: null as Row | null,
  form: {} as Record<string, string>,
  message: '',
  ok: false,
})

const orderedRules = computed(() => [
  { label: '接电时长', text: rules.value.duration ?? '' },
  { label: '用电量', text: rules.value.consumption ?? '' },
  { label: '兜底折算', text: rules.value.estimate ?? '' },
  { label: '碳排放', text: rules.value.carbon ?? '' },
  { label: '重复接电', text: rules.value.dedup ?? '' },
])

const submitLabel = computed(() => {
  switch (modal.value.mode) {
    case 'create':
      return '登记'
    case 'remote':
      return '重试抄表'
    case 'estimate':
      return '按时长折算'
    case 'manual':
      return '保存读数'
    default:
      return '提交'
  }
})

const pageKwh = computed(() =>
  Math.round(
    rows.value.reduce(
      (sum, row) => sum + (row.duplicate ? 0 : Number(row['用电量(kWh)'] ?? 0)),
      0,
    ) * 100,
  ) / 100,
)

const slotMax = computed(() =>
  Math.max(1, ...board.value.bySlot.map((item) => item['用电量(kWh)'])),
)
const dateMax = computed(() =>
  Math.max(1, ...board.value.byDate.map((item) => item['用电量(kWh)'])),
)

function formatNum(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  return Number.isFinite(num) ? num.toLocaleString() : String(value)
}

function barHeight(value: number): string {
  return `${Math.max(4, Math.round((value / slotMax.value) * 160))}px`
}
function lineHeight(value: number): string {
  return `${Math.max(3, Math.round((value / dateMax.value) * 120))}px`
}

function badgeClass(row: Row): string {
  if (row.duplicate) return 'dup'
  if (row['计量状态'] === '已计量') return 'ok'
  if (row['计量状态'] === '接电中') return 'warn'
  if (row.abnormal) return 'bad'
  return 'info'
}

function availableActions(row: Row): string[] {
  if (row.duplicate) return []
  if (!row['断电时刻']) return ['登记断电']
  if (row['计量状态'] === '待抄表') return ['远程抄表', '手工抄录', '时长折算']
  return []
}

function resetFilters() {
  filters.value = { keyword: '', company: '', status: '' }
  void reloadAll()
}

function exportRows() {
  const query = new URLSearchParams()
  if (month.value) query.set('month', month.value)
  window.open(`${ENDPOINT}/export?${query.toString()}`, '_blank')
}

function openCreate() {
  modal.value = {
    open: true, mode: 'create', title: '登记岸电接电', action: '',
    row: null, form: {}, message: '', ok: false,
  }
}

function openAction(action: string, row: Row) {
  const form: Record<string, string> = {}
  if (action === '登记断电') {
    form['断电时刻'] = ''
  } else if (action === '手工抄录') {
    form['起始读数'] = row['起始读数'] != null ? String(row['起始读数']) : ''
    form['结束读数'] = ''
  }
  modal.value = {
    open: true,
    mode:
      action === '登记断电'
        ? 'disconnect'
        : action === '手工抄录'
          ? 'manual'
          : action === '时长折算'
            ? 'estimate'
            : 'remote',
    title: action,
    action,
    row,
    form,
    message: '',
    ok: false,
  }
  // 远程抄表/时长折算没有表单，直接触发一次
  if (action === '远程抄表' || action === '时长折算') {
    void submitModal()
  }
}

function closeModal() {
  modal.value.open = false
}

async function submitModal() {
  const m = modal.value
  const finish = (ok: boolean, message: string) => {
    m.message = message
    m.ok = ok
    if (ok) {
      void reloadAll().then(() => window.setTimeout(closeModal, 800))
    }
  }

  if (m.mode === 'remote' || m.mode === 'estimate') {
    // 远程抄表失败不关弹窗，原因留在弹窗里，可点「提交」反复重试
    const action = m.mode === 'remote' ? '远程抄表' : '时长折算'
    const response = await postAction(Number(m.row?.id), { action })
    finish(response.ok, response.message)
    return
  }

  if (m.mode === 'disconnect') {
    const response = await postAction(Number(m.row?.id), {
      action: '登记断电',
      断电时刻: m.form['断电时刻'] ?? '',
    })
    finish(response.ok, response.message)
    return
  }

  if (m.mode === 'manual') {
    const values: Record<string, string> = { action: '手工抄录' }
    if (m.form['起始读数']?.trim()) values['起始读数'] = m.form['起始读数'].trim()
    values['结束读数'] = m.form['结束读数'] ?? ''
    const response = await postAction(Number(m.row?.id), values)
    finish(response.ok, response.message)
    return
  }

  // 登记接电
  const payload: Record<string, string> = {}
  for (const [key, value] of Object.entries(m.form)) {
    if (String(value ?? '').trim() !== '') payload[key] = String(value).trim()
  }
  const response = await postJson('', payload)
  finish(response.ok, response.message)
}

async function postAction(id: number, values: Record<string, string>) {
  return postJson(`/${id}/actions`, values)
}

async function postJson(path: string, values: Record<string, string>) {
  try {
    const response = await request(`${ENDPOINT}${path}`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    if (!response.ok) throw new Error(`接口返回 ${response.status}，操作未生效`)
    return (await response.json()) as { ok: boolean; message: string }
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : '操作失败，请重试',
    }
  }
}

async function reloadList() {
  const query = new URLSearchParams({ size: '200' })
  if (month.value) query.set('month', month.value.trim())
  if (filters.value.company) query.set('company', filters.value.company.trim())
  if (filters.value.keyword) query.set('keyword', filters.value.keyword.trim())
  if (filters.value.status) query.set('status', filters.value.status)
  const response = await request(`${ENDPOINT}?${query.toString()}`)
  if (!response.ok) throw new Error('岸电接电记录读取失败')
  const payload = await response.json()
  rows.value = payload.items ?? []
  total.value = payload.total ?? rows.value.length
}

async function reloadAll() {
  errorMessage.value = ''
  checked.value = false
  try {
    const monthParam = month.value.trim()
    const query = monthParam ? `?month=${encodeURIComponent(monthParam)}` : ''
    const [, boardResult, checkResult, rulesResult] = await Promise.all([
      reloadList(),
      request(`${ENDPOINT}/dashboard${query}`).then((res) => {
        if (!res.ok) throw new Error('用电量看板读取失败')
        return res.json()
      }),
      request(`${ENDPOINT}/consistency${query}`).then((res) => {
        if (!res.ok) throw new Error('对账结果读取失败')
        return res.json()
      }),
      request(`${ENDPOINT}/rules`).then((res) => res.json()),
    ])
    board.value = boardResult as Board
    check.value = checkResult
    checked.value = true
    rules.value = rulesResult as Record<string, string>
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '看板数据读取失败'
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.consistency-banner {
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  margin: 0 0 12px;
}
.consistency-banner.ok {
  background: #ecfdf3;
  border: 1px solid #73e2a3;
  color: #05603a;
}
.consistency-banner.bad {
  background: #fef3f2;
  border: 1px solid #fda29b;
  color: #b42318;
}
.board-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
}
.panel-title {
  margin: 0 0 10px;
  font-size: 14px;
}
.bars {
  display: flex;
  align-items: flex-end;
  justify-content: space-around;
  gap: 8px;
  height: 230px;
}
.bar-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
  flex: 1;
}
.bar-track {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  flex: 1;
  width: 100%;
}
.bar-fill {
  width: 60%;
  max-width: 64px;
  background: linear-gradient(180deg, #2e90fa, #1f6feb);
  border-radius: 4px 4px 0 0;
}
.bar-value,
.line-value {
  font-size: 12px;
  color: #1f2937;
  margin-top: 4px;
}
.bar-label,
.line-label {
  font-size: 12px;
  color: var(--muted);
}
.bar-sub {
  font-size: 11px;
  color: #94a3b8;
}
.line-chart {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 190px;
  overflow-x: auto;
}
.line-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  min-width: 52px;
  height: 100%;
}
.line-bar {
  width: 28px;
  background: #84caff;
  border-radius: 3px 3px 0 0;
}
.rules-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: #334155;
}
.rule-tag {
  display: inline-block;
  background: #eff8ff;
  border: 1px solid #b2ddff;
  color: #175cd3;
  border-radius: 4px;
  padding: 0 6px;
  margin-right: 8px;
  font-size: 12px;
}
.badge {
  display: inline-block;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
  background: #e2e8f0;
  color: #334155;
}
.badge.ok { background: #d1fadf; color: #05603a; }
.badge.warn { background: #fef0c7; color: #b54708; }
.badge.bad { background: #fee4e2; color: #b42318; }
.badge.info { background: #e0efff; color: #175cd3; }
.badge.dup { background: #f2f4f7; color: #475467; }
.dup-row { background: #fafafa; color: #667085; }
.note-cell { color: var(--muted); max-width: 280px; }
.muted-text { color: #94a3b8; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.modal {
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  width: 420px;
  max-width: calc(100vw - 32px);
}
.modal-title { margin: 0 0 12px; font-size: 16px; }
.modal-field { display: block; margin-bottom: 10px; }
.modal-field span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.modal-field input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
}
.modal-optional { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
.modal-optional summary { cursor: pointer; margin-bottom: 8px; }
.modal-tip { font-size: 13px; color: #334155; margin: 0 0 10px; }
.modal-hint { font-size: 12px; color: var(--muted); margin: 0 0 10px; }
.modal-message {
  font-size: 13px;
  margin-bottom: 10px;
  color: #b42318;
  background: #fef3f2;
  border: 1px solid #fda29b;
  border-radius: 6px;
  padding: 6px 8px;
}
.modal-message.ok {
  color: #05603a;
  background: #ecfdf3;
  border-color: #73e2a3;
}
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
.filter-item select {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
}
</style>
