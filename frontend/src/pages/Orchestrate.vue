<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'
const history = ref([])
const last = ref(null)
const busy = ref(false)
const err = ref('')
async function refresh() { history.value = await getJSON('/api/orchestrations') }
async function runOnce() {
  err.value = ''; busy.value = true
  try {
    last.value = await postJSON('/api/orchestrations')
    await refresh()
  } catch (e) { err.value = String(e.message || e) }
  finally { busy.value = false }
}
onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>编排台</h1>
    <p class="muted">一次编排覆盖全部启用域。域内依赖按层执行；跨域依赖不会执行，记 blocked_domain 并留下拦截原因。</p>
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
        · 分域合计与全域一致：<span :class="last.report?.domain_consistent ? 'tag ok' : 'tag danger'">{{ last.report?.domain_consistent ? '是' : '否' }}</span>
      </p>
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
        </tbody>
      </table>
      <h4>被拦截的跨域依赖</h4>
      <p v-if="!(last.report?.blocked_edges || []).length" class="muted">无</p>
      <ul v-else>
        <li v-for="(e, i) in last.report.blocked_edges" :key="i" class="mono">
          {{ e.job }}（{{ e.job_domain }}）不得依赖 {{ e.depends_on }}（{{ e.dep_domain }}）
        </li>
      </ul>
      <p>
        <router-link :to="{ path: '/attempts', query: { orchestration_id: last.id } }">查看本次尝试 →</router-link>
        ·
        <router-link to="/watermarks">产出水位 →</router-link>
      </p>
    </div>
    <div class="panel">
      <h3>历史编排</h3>
      <table>
        <thead><tr><th>ID</th><th>状态</th><th>成功</th><th>失败</th><th>跳过</th><th>跨域拦截</th><th>开始</th></tr></thead>
        <tbody>
          <tr v-for="o in history" :key="o.id">
            <td>{{ o.id }}</td>
            <td>{{ o.status }}</td>
            <td>{{ o.report?.succeeded }}</td>
            <td>{{ o.report?.failed }}</td>
            <td>{{ o.report?.skipped }}</td>
            <td>{{ o.report?.blocked_domain }}</td>
            <td class="mono">{{ o.started_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
