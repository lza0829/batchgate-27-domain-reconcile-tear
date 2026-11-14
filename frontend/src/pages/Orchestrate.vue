<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'
const history = ref([])
const last = ref(null)
const attempts = ref([])
const watermarks = ref([])
const busy = ref(false)
const err = ref('')

async function refresh() { history.value = await getJSON('/api/orchestrations') }
async function loadRunDetail(id) {
  attempts.value = await getJSON(`/api/attempts?orchestration_id=${id}`)
  watermarks.value = await getJSON('/api/watermarks')
}
async function runOnce() {
  err.value = ''; busy.value = true
  try {
    last.value = await postJSON('/api/orchestrations')
    await refresh()
    await loadRunDetail(last.value.id)
  } catch (e) { err.value = String(e.message || e) }
  finally { busy.value = false }
}
const sumKeys = [
  ['succeeded', '成功'],
  ['failed', '失败'],
  ['skipped_dep', '依赖跳过'],
  ['blocked_domain', '跨域拦截'],
  ['attempt_count', '尝试'],
  ['job_total', '作业'],
  ['watermarked_jobs', '水位'],
]
const sliceSums = computed(() => {
  const domains = last.value?.report?.domains || []
  return Object.fromEntries(sumKeys.map(([k]) => [k, domains.reduce((s, d) => s + (d[k] || 0), 0)]))
})
const sumsOk = computed(() => {
  const r = last.value?.report
  if (!r) return false
  return sumKeys.every(([k]) => sliceSums.value[k] === (r[k] || 0))
})
const blockedJobs = computed(() =>
  attempts.value.filter(a => a.status === 'blocked_domain')
)
function watermarkOf(jobName) {
  return watermarks.value.find(w => w.job_name === jobName)?.watermark_ts || null
}
onMounted(async () => {
  try {
    await refresh()
    if (history.value.length) {
      last.value = history.value[0]
      await loadRunDetail(last.value.id)
    }
  } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>编排台</h1>
    <p class="muted">一次编排覆盖全部启用域。域内依赖按层执行；跨域依赖不会执行、不抬水位，记 blocked_domain 并留下拦截原因。</p>
    <div class="panel">
      <button :disabled="busy" @click="runOnce">{{ busy ? '编排中…' : '执行编排' }}</button>
    </div>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" v-if="last">
      <h3>最近一次报告</h3>
      <p>
        #{{ last.id }} · <span class="tag ok">{{ last.status }}</span> ·
        全域 succeeded {{ last.report?.succeeded }} /
        failed {{ last.report?.failed }} /
        skipped {{ last.report?.skipped }}（dep {{ last.report?.skipped_dep }} + blocked {{ last.report?.blocked_domain }}）/
        attempts {{ last.report?.attempt_count }}
      </p>
      <p class="mono muted">watermarked_jobs={{ last.report?.watermarked_jobs }} layers={{ last.report?.layer_count }}
        · 分域合计与全域一致：<span :class="last.report?.domain_consistent && sumsOk ? 'tag ok' : 'tag danger'">{{ last.report?.domain_consistent && sumsOk ? '是' : '否' }}</span>      </p>
      <h4>分域数字</h4>
      <table>
        <thead><tr><th>域</th><th>作业</th><th>成功</th><th>失败</th><th>跳过</th><th>其中跨域拦截</th><th>层</th><th>水位</th></tr></thead>
        <tbody>
          <tr v-for="d in last.report?.domains || []" :key="d.domain">
            <td class="mono">{{ d.domain }}</td>
            <td>{{ d.job_total }}</td>
            <td>{{ d.succeeded }}</td>
            <td>{{ d.failed }}</td>
            <td>{{ d.skipped }}</td>
            <td>{{ d.blocked_domain }}</td>
            <td>{{ d.layer_count }}</td>
            <td>{{ d.watermarked_jobs }}</td>
          </tr>
          <tr>
            <td class="mono"><strong>分域合计</strong></td>
            <td>{{ sliceSums.job_total }}</td>
            <td>{{ sliceSums.succeeded }}</td>
            <td>{{ sliceSums.failed }}</td>
            <td>{{ sliceSums.skipped }}</td>
            <td>{{ sliceSums.blocked_domain }}</td>
            <td>—</td>
            <td>{{ sliceSums.watermarked_jobs }}</td>
          </tr>
          <tr>
            <td class="mono"><strong>全域</strong></td>
            <td>{{ last.report?.job_total }}</td>
            <td>{{ last.report?.succeeded }}</td>
            <td>{{ last.report?.failed }}</td>
            <td>{{ last.report?.skipped }}</td>
            <td>{{ last.report?.blocked_domain }}</td>
            <td>{{ last.report?.layer_count }}</td>
            <td>{{ last.report?.watermarked_jobs }}</td>
          </tr>
        </tbody>
      </table>
      <p class="mono" :class="sumsOk ? 'tag ok' : 'tag danger'">
        合计校验：{{ sumsOk ? '分域相加 == 全域' : '分域相加 ≠ 全域' }}
        <span class="muted">（报告标记 domain_consistent={{ last.report?.domain_consistent ? '是' : '否' }}）</span>
      </p>
      <h4>被拦截的跨域依赖</h4>
      <p v-if="!(last.report?.blocked_edges || []).length" class="muted">无</p>
      <ul v-else>
        <li v-for="(e, i) in last.report.blocked_edges" :key="i" class="mono">
          {{ e.job }}（{{ e.job_domain }}）不得依赖 {{ e.depends_on }}（{{ e.dep_domain }}）
        </li>
      </ul>
      <h4>本次尝试（带域）</h4>
      <table>
        <thead><tr><th>作业</th><th>域</th><th>状态</th><th>说明</th><th>本次后水位</th></tr></thead>
        <tbody>
          <tr v-for="a in attempts" :key="a.id">
            <td class="mono">{{ a.job_name }}</td>
            <td class="mono">{{ a.domain }}</td>
            <td><span class="tag" :class="a.status==='success'?'ok':(a.status==='failed'?'danger':'warn')">{{ a.status }}</span></td>
            <td class="mono">{{ a.detail?.reason || a.detail?.error || '—' }}</td>
            <td class="mono">{{ a.status === 'blocked_domain' ? '未抬水位' : (watermarkOf(a.job_name) || '—') }}</td>
          </tr>
        </tbody>
      </table>
      <p class="mono muted" v-if="blockedJobs.length">
        跨域拦截 {{ blockedJobs.length }} 条，均未执行成功、未写水位：{{ blockedJobs.map(b => b.job_name).join(', ') }}
      </p>
      <p>
        <router-link :to="{ path: '/attempts', query: { orchestration_id: last.id } }">查看本次尝试 →</router-link>
        ·
        <router-link to="/watermarks">产出水位 →</router-link>
      </p>
    </div>
    <div class="panel">
      <h3>历史编排</h3>
      <table>
        <thead><tr><th>ID</th><th>状态</th><th>成功</th><th>失败</th><th>跳过</th><th>跨域拦截</th><th>一致</th><th>开始</th></tr></thead>
        <tbody>
          <tr v-for="o in history" :key="o.id">
            <td>{{ o.id }}</td>
            <td>{{ o.status }}</td>
            <td>{{ o.report?.succeeded }}</td>
            <td>{{ o.report?.failed }}</td>
            <td>{{ o.report?.skipped }}</td>
            <td>{{ o.report?.blocked_domain }}</td>
            <td><span :class="o.report?.domain_consistent ? 'tag ok' : 'tag danger'">{{ o.report?.domain_consistent ? '是' : '否' }}</span></td>
            <td class="mono">{{ o.started_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
