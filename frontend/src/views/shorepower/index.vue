<template>
  <section class="page" data-module="shore-power">
    <header class="page-head">
      <div>
        <h2>岸电接入计量</h2>
        <p class="page-desc">
          登记靠泊船舶的接电、断电时刻，用电量优先取计量表读数差、读表失败时按接电时长与约定功率估算；
          看板数字由接电记录实时汇总，每次刷新都与记录一致。原有人工抄表方式不受影响。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记接电</button>
        <button class="btn" type="button" @click="exportRows">导出接电记录</button>
      </div>
    </header>

    <div class="tab-bar">
      <button class="tab-item" :class="{ active: tab === 'dashboard' }" type="button" @click="tab = 'dashboard'">用电量看板</button>
      <button class="tab-item" :class="{ active: tab === 'records' }" type="button" @click="tab = 'records'">接电记录</button>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="noticeMessage" class="ok-text">{{ noticeMessage }}</p>

    <!-- ============ 用电量看板 ============ -->
    <template v-if="tab === 'dashboard'">
      <div class="stat-row" style="flex-wrap: wrap">
        <article v-for="card in dashboard.cards" :key="card.label" class="stat-card" style="min-width: 150px">
          <span class="stat-label">{{ card.label }}</span>
          <strong class="stat-value">{{ card.value }}</strong>
          <span class="hint-text">{{ card.hint }}</span>
        </article>
      </div>

      <div class="panel">
        <div class="panel-title" style="display:flex;justify-content:space-between;align-items:center">
          <span>各时段用电量分布</span>
          <span class="check-row">
            <label><input v-model="groupBy" type="radio" value="day" @change="loadDashboard" />按日</label>
            <label><input v-model="groupBy" type="radio" value="hour" @change="loadDashboard" />按接电时段</label>
            <label><input v-model="groupBy" type="radio" value="company" @change="loadDashboard" />按船公司</label>
            <select v-model="companyFilter" style="padding:4px 8px;border:1px solid var(--border);border-radius:6px" @change="loadDashboard">
              <option value="">全部船公司</option>
              <option v-for="name in companies" :key="name" :value="name">{{ name }}</option>
            </select>
            <button class="btn" type="button" @click="reload">刷新</button>
          </span>
        </div>
        <div class="legend">
          <span><i style="background:#1f6feb"></i>计量表读数差电量</span>
          <span><i style="background:#f79009"></i>时长估算电量</span>
          <span class="hint-text">柱高＝该时段用电量合计(kWh)；在接未断电记录的临时匡算不计入柱内。</span>
        </div>
        <div v-if="dashboard.groups.length" class="chart">
          <div v-for="g in dashboard.groups" :key="g['时段']" class="chart-col">
            <span class="chart-value">{{ g['用电量合计(kWh)'] }}</span>
            <div class="chart-bar-wrap">
              <div
                class="chart-bar meter"
                :style="{ height: barHeight(g['表计电量(kWh)']) }"
                :title="`表计 ${g['表计电量(kWh)']} kWh`"
              ></div>
              <div
                class="chart-bar estimate"
                :style="{ height: barHeight(g['估算电量(kWh)']) }"
                :title="`时长估算 ${g['估算电量(kWh)']} kWh`"
              ></div>
            </div>
            <span class="chart-label">{{ g['时段'] }}</span>
          </div>
        </div>
        <p v-else class="empty-state">当前筛选条件下暂无用电量数据</p>
      </div>

      <div class="panel">
        <p class="panel-title">按船公司汇总（月底电费、碳排放核算取此口径）</p>
        <table class="data-table">
          <thead>
            <tr>
              <th>所属船公司</th><th>已结算艘次</th><th>用电量合计(kWh)</th>
              <th>表计电量(kWh)</th><th>时长估算电量(kWh)</th><th>接电总时长(h)</th>
              <th>在接未断电(艘次)</th><th>在接临时匡算(kWh)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in dashboard.by_company" :key="String(c['所属船公司'])">
              <td>{{ c['所属船公司'] }}</td>
              <td>{{ c['接电次数'] }}</td>
              <td><strong>{{ c['用电量合计(kWh)'] }}</strong></td>
              <td>{{ c['表计电量(kWh)'] }}</td>
              <td>{{ c['估算电量(kWh)'] }}</td>
              <td>{{ c['接电总时长(h)'] }}</td>
              <td>{{ c['在接未断电(艘次)'] }}</td>
              <td>{{ c['临时匡算电量(kWh)'] }}</td>
            </tr>
            <tr v-if="!dashboard.by_company.length"><td colspan="8" class="empty-state">暂无数据</td></tr>
          </tbody>
        </table>
      </div>

      <div class="panel" v-if="dashboard.missing_disconnect.length">
        <p class="panel-title">断电时刻缺失（单独标出，临时匡算不计入累计用电量）</p>
        <table class="data-table">
          <thead>
            <tr><th>记录编号</th><th>船舶名称</th><th>所属船公司</th><th>接电时刻</th><th>已接时长(h)</th><th>临时匡算(kWh)</th><th>计量状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in dashboard.missing_disconnect" :key="String(r.id) + '-m'">
              <td>{{ r['记录编号'] }}</td><td>{{ r['船舶名称'] }}</td><td>{{ r['所属船公司'] }}</td>
              <td>{{ r['接电时刻'] }}</td><td>{{ r['接电时长(h)'] }}</td><td>{{ r['估算用电量(kWh)'] }}</td>
              <td><span class="tag" :class="tagClass(r['计量状态'])">{{ r['计量状态'] }}</span></td>
              <td class="row-actions">
                <button class="link" type="button" @click="openDisconnect(r)">登记断电</button>
                <button v-if="r['抄表失败原因']" class="link" type="button" @click="openManual(r)">人工补录</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="panel" v-if="dashboard.meter_failures.length">
        <p class="panel-title">计量表读不到数（已报明原因，可重试或人工补录读数）</p>
        <table class="data-table">
          <thead>
            <tr><th>记录编号</th><th>船舶名称</th><th>所属船公司</th><th>接电/断电时刻</th><th>失败原因</th><th>已重试次数</th><th>当前兜底电量(kWh)</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in dashboard.meter_failures" :key="String(r.id) + '-f'">
              <td>{{ r['记录编号'] }}</td><td>{{ r['船舶名称'] }}</td><td>{{ r['所属船公司'] }}</td>
              <td>{{ r['接电时刻'] }} → {{ r['断电时刻'] || '未断电' }}</td>
              <td class="error-text">{{ r['抄表失败原因'] }}</td>
              <td>{{ r['抄表次数'] }}</td>
              <td>{{ r['用电量(kWh)'] ?? '—' }}<span v-if="r['用电量(kWh)'] != null" class="hint-text">（时长估算兜底）</span></td>
              <td class="row-actions">
                <button class="link" type="button" @click="retryMeter(r)">重试抄表</button>
                <button class="link" type="button" @click="openManual(r)">人工补录</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="panel">
        <p class="panel-title">接电时长与用电量换算口径</p>
        <ul class="rule-list">
          <li v-for="(text, key) in dashboard.conversion" :key="key"><b>{{ ruleLabel(key) }}：</b>{{ text }}</li>
        </ul>
      </div>
    </template>

    <!-- ============ 接电记录 ============ -->
    <template v-else>
      <form class="filter-bar" @submit.prevent="loadRecords">
        <label class="filter-item">
          <span>船名 / 记录编号</span>
          <input v-model="filters.keyword" placeholder="按船名或记录编号检索" />
        </label>
        <label class="filter-item">
          <span>船公司</span>
          <select v-model="filters.company">
            <option value="">全部</option>
            <option v-for="name in companies" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>
        <label class="filter-item check-row" style="align-items:center">
          <input v-model="filters.missing_disconnect" type="checkbox" /> 只看断电时刻缺失
        </label>
        <label class="filter-item check-row" style="align-items:center">
          <input v-model="filters.duplicated" type="checkbox" /> 只看重复接电
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>计量状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">
              <template v-if="column === '断电时刻'">
                <span :class="{ 'error-text': row['缺失断电时刻'] }">{{ row[column] || '未登记' }}</span>
              </template>
              <template v-else-if="column === '用电量(kWh)'">
                <span v-if="row['duplicated']" class="hint-text">不计量</span>
                <span v-else-if="row[column] == null" class="hint-text">{{ row['估算用电量(kWh)'] }}（临时匡算）</span>
                <span v-else>{{ row[column] }}</span>
              </template>
              <template v-else>{{ row[column] ?? '—' }}</template>
            </td>
            <td>
              <span class="tag" :class="tagClass(row['计量状态'])">{{ row['计量状态'] }}</span>
              <div v-if="row['抄表失败原因']" class="error-text" style="font-size:12px">{{ row['抄表失败原因'] }}</div>
            </td>
            <td class="row-actions">
              <button v-if="row['缺失断电时刻']" class="link" type="button" @click="openDisconnect(row)">登记断电</button>
              <button v-if="!row['duplicated'] && (row['抄表失败原因'] || (row.status === '已断电' && row['用电量(kWh)'] == null))" class="link" type="button" @click="retryMeter(row)">重试抄表</button>
              <button v-if="!row['duplicated'] && row['抄表失败原因']" class="link" type="button" @click="openManual(row)">人工补录</button>
              <span v-if="row['duplicated']" class="hint-text">同一时段已计最早一条</span>
            </td>
          </tr>
          <tr v-if="!rows.length"><td :colspan="columns.length + 2" class="empty-state">暂无接电记录，点击右上角「登记接电」开始</td></tr>
        </tbody>
      </table>
      <footer class="page-foot"><span>共 {{ total }} 条接电记录（同一艘船同时段重复登记只计一次）</span></footer>
    </template>

    <!-- ============ 登记接电弹窗 ============ -->
    <div v-if="createVisible" class="modal-mask" @click.self="createVisible = false">
      <div class="modal">
        <div class="modal-head"><h3>登记岸电接电</h3><button class="btn ghost" type="button" @click="createVisible = false">关闭</button></div>
        <form class="form-grid" @submit.prevent="submitCreate">
          <label><span>船舶名称 *</span><input v-model="createForm['船舶名称']" placeholder="如：远洋银河" /></label>
          <label><span>所属船公司 *</span><input v-model="createForm['所属船公司']" placeholder="如：中远海运" list="company-list" /><datalist id="company-list"><option v-for="n in companies" :key="n" :value="n" /></datalist></label>
          <label><span>接电泊位</span><input v-model="createForm['接电泊位']" placeholder="如：A03" /></label>
          <label><span>约定受电功率(kW)</span><input v-model="createForm['约定受电功率(kW)']" type="number" min="0" step="1" /></label>
          <label><span>接电时刻 *</span><input v-model="createForm['接电时刻']" type="datetime-local" /></label>
          <label><span>断电时刻（可暂不填）</span><input v-model="createForm['断电时刻']" type="datetime-local" /></label>
          <label class="full"><span>起始读数(kWh)（可留空，由计量表自动抄取）</span><input v-model="createForm['起始读数(kWh)']" type="number" min="0" step="0.1" /></label>
        </form>
        <div class="modal-foot">
          <button class="btn" type="button" @click="createVisible = false">取消</button>
          <button class="btn primary" type="button" @click="submitCreate">提交登记</button>
        </div>
      </div>
    </div>

    <!-- ============ 登记断电弹窗 ============ -->
    <div v-if="disconnectVisible" class="modal-mask" @click.self="disconnectVisible = false">
      <div class="modal">
        <div class="modal-head"><h3>登记断电时刻 — {{ activeRow?.['船舶名称'] }}</h3><button class="btn ghost" type="button" @click="disconnectVisible = false">关闭</button></div>
        <p class="section-note">记录编号 {{ activeRow?.['记录编号'] }}，接电时刻 {{ activeRow?.['接电时刻'] }}。断电后系统自动抄取止码并结算用电量。</p>
        <form class="form-grid" @submit.prevent="submitDisconnect">
          <label class="full"><span>断电时刻 *</span><input v-model="disconnectForm['断电时刻']" type="datetime-local" /></label>
          <label class="full"><span>结束读数(kWh)（可留空，由计量表自动抄取）</span><input v-model="disconnectForm['结束读数(kWh)']" type="number" step="0.1" /></label>
          <label v-if="activeRow && activeRow['起始读数(kWh)'] == null" class="full"><span>起始读数(kWh)（起码缺失时可现场补录）</span><input v-model="disconnectForm['起始读数(kWh)']" type="number" step="0.1" /></label>
        </form>
        <div class="modal-foot">
          <button class="btn" type="button" @click="disconnectVisible = false">取消</button>
          <button class="btn primary" type="button" @click="submitDisconnect">确认断电</button>
        </div>
      </div>
    </div>

    <!-- ============ 人工补录读数弹窗 ============ -->
    <div v-if="manualVisible" class="modal-mask" @click.self="manualVisible = false">
      <div class="modal">
        <div class="modal-head"><h3>人工补录计量表读数 — {{ activeRow?.['船舶名称'] }}</h3><button class="btn ghost" type="button" @click="manualVisible = false">关闭</button></div>
        <p class="section-note">失败原因：{{ activeRow?.['抄表失败原因'] || '—' }}。补录后按「结束读数 − 起始读数」重新结算；留空的读数会尝试重新自动抄取。</p>
        <form class="form-grid" @submit.prevent="submitManual">
          <label><span>起始读数(kWh)</span><input v-model="manualForm['起始读数(kWh)']" type="number" step="0.1" :placeholder="String(activeRow?.['起始读数(kWh)'] ?? '')" /></label>
          <label><span>结束读数(kWh)</span><input v-model="manualForm['结束读数(kWh)']" type="number" step="0.1" :placeholder="String(activeRow?.['结束读数(kWh)'] ?? '')" :disabled="!activeRow?.['断电时刻']" /></label>
        </form>
        <div class="modal-foot">
          <button class="btn" type="button" @click="manualVisible = false">取消</button>
          <button class="btn primary" type="button" @click="submitManual">补录并结算</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/shore-power'

type Row = Record<string, string | number | boolean | null>

const columns = [
  '记录编号', '船舶名称', '所属船公司', '接电泊位', '接电时刻', '断电时刻',
  '接电时长(h)', '起始读数(kWh)', '结束读数(kWh)', '用电量(kWh)', '计量方式', '抄表次数',
]

type Dashboard = {
  cards: { label: string; value: number | string; hint: string }[]
  groups: Record<string, string | number>[]
  by_company: Record<string, string | number>[]
  missing_disconnect: Row[]
  meter_failures: Row[]
  conversion: Record<string, string>
}

const emptyDashboard = (): Dashboard => ({
  cards: [], groups: [], by_company: [], missing_disconnect: [], meter_failures: [], conversion: {},
})

const tab = ref<'dashboard' | 'records'>('dashboard')
const rows = ref<Row[]>([])
const total = ref(0)
const companies = ref<string[]>([])
const dashboard = ref<Dashboard>(emptyDashboard())
const errorMessage = ref('')
const noticeMessage = ref('')
const groupBy = ref<'day' | 'hour' | 'company'>('day')
const companyFilter = ref('')
const filters = ref<Record<string, string | boolean>>({ keyword: '', company: '', missing_disconnect: false, duplicated: false })

const createVisible = ref(false)
const disconnectVisible = ref(false)
const manualVisible = ref(false)
const activeRow = ref<Row | null>(null)
const createForm = ref<Record<string, string>>({})
const disconnectForm = ref<Record<string, string>>({})
const manualForm = ref<Record<string, string>>({})

const ruleLabels: Record<string, string> = {
  duration: '接电时长', metered: '表计电量', estimated: '时长估算', dedup: '重复接电', missing: '断电缺失', retry: '抄表失败处理',
}
function ruleLabel(key: string): string {
  return ruleLabels[key] ?? key
}

function tagClass(state: string | number | boolean | null | undefined): string {
  switch (state) {
    case '已结算': return 'tag-green'
    case '已估算': return 'tag-amber'
    case '读表失败': return 'tag-red'
    case '在接未断电': return 'tag-blue'
    case '重复接电': return 'tag-gray'
    default: return 'tag-gray'
  }
}

function barHeight(value: string | number | undefined): string {
  const max = Math.max(...dashboard.value.groups.map((g) => Number(g['用电量合计(kWh)']) || 0), 0)
  const v = Number(value) || 0
  if (!max || !v) return '0px'
  return `${Math.max((v / max) * 168, 2)}px`
}

function flash(message: string, ok = false) {
  errorMessage.value = ok ? '' : message
  noticeMessage.value = ok ? message : ''
}

async function reload() {
  await Promise.all([loadDashboard(), loadRecords(), loadCompanies()])
}

async function loadDashboard() {
  try {
    const query = new URLSearchParams({ group_by: groupBy.value })
    if (companyFilter.value) query.set('company', companyFilter.value)
    const response = await request(`${ENDPOINT}/dashboard?${query.toString()}`)
    if (!response.ok) throw new Error('看板数据读取失败')
    dashboard.value = await response.json()
  } catch (error) {
    flash(error instanceof Error ? error.message : '看板数据读取失败')
  }
}

async function loadCompanies() {
  try {
    const response = await request(`${ENDPOINT}/companies`)
    if (response.ok) companies.value = (await response.json()).items ?? []
  } catch {
    /* 下拉选项失败不阻塞主流程 */
  }
}

async function loadRecords() {
  try {
    const params = new URLSearchParams({ size: '200' })
    if (filters.value.keyword) params.set('keyword', String(filters.value.keyword))
    if (filters.value.company) params.set('company', String(filters.value.company))
    if (filters.value.missing_disconnect) params.set('missing_disconnect', 'true')
    if (filters.value.duplicated) params.set('duplicated', 'true')
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) throw new Error('接电记录读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    flash(error instanceof Error ? error.message : '接电记录读取失败')
  }
}

function resetFilters() {
  filters.value = { keyword: '', company: '', missing_disconnect: false, duplicated: false }
  void loadRecords()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

// ---- 登记接电 ----
function openCreate() {
  createForm.value = { '约定受电功率(kW)': '350', '接电时刻': '', '断电时刻': '' }
  createVisible.value = true
}

async function submitCreate() {
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm.value } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      flash(payload.message || '接电登记未成功')
      return
    }
    createVisible.value = false
    flash(payload.message, true)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '接电登记失败')
  }
}

// ---- 登记断电 ----
function openDisconnect(row: Row) {
  activeRow.value = row
  disconnectForm.value = { '断电时刻': '', '结束读数(kWh)': '', '起始读数(kWh)': '' }
  disconnectVisible.value = true
}

async function submitDisconnect() {
  if (!activeRow.value) return
  const values: Record<string, string> = { action: '登记断电', ...stripEmpty(disconnectForm.value) }
  try {
    const response = await request(`${ENDPOINT}/${activeRow.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      flash(payload.message || '断电登记未成功')
      return
    }
    disconnectVisible.value = false
    flash(`${payload.message}${payload.entry?.['抄表失败原因'] ? `；${payload.entry['抄表失败原因']}，可重试或人工补录` : ''}`, true)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '断电登记失败')
  }
}

// ---- 重试抄表 / 人工补录 ----
async function retryMeter(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action: '重试抄表' } }),
    })
    const payload = await response.json()
    flash(payload.message, payload.ok)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '重试抄表失败')
  }
}

function openManual(row: Row) {
  activeRow.value = row
  manualForm.value = { '起始读数(kWh)': '', '结束读数(kWh)': '' }
  manualVisible.value = true
}

async function submitManual() {
  if (!activeRow.value) return
  const values: Record<string, string> = { action: '重试抄表', ...stripEmpty(manualForm.value) }
  try {
    const response = await request(`${ENDPOINT}/${activeRow.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      flash(payload.message || '补录未成功')
      return
    }
    manualVisible.value = false
    flash(payload.message, true)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '人工补录失败')
  }
}

function stripEmpty(form: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(form).filter(([, v]) => String(v ?? '').trim() !== ''))
}

onMounted(reload)
</script>
