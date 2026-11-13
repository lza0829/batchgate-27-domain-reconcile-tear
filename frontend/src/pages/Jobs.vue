<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, patchJSON } from '../api'
const rows = ref([])
const crossEdges = ref([])
const msg = ref('')
const err = ref('')
async function refresh() {
  rows.value = await getJSON('/api/jobs')
  crossEdges.value = (await getJSON('/api/domains')).cross_domain_edges || []
}
function errorText(e) {
  try {
    const d = JSON.parse(String(e.message || e))
    const detail = d.detail
    if (detail?.error === 'cross_domain_dependency') {
      const edges = (detail.conflicts || [])
        .map(c => `${c.job}(${c.job_domain}) → ${c.depends_on}(${c.dep_domain})`)
        .join('；')
      return `${detail.message}：${edges}`
    }
    return detail?.message || d.detail || String(e.message || e)
  } catch { return String(e.message || e) }
}
async function saveAttempts(row) {
  err.value = ''; msg.value = ''
  try {
    await patchJSON(`/api/jobs/${row.id}/max-attempts`, { max_attempts: Number(row.max_attempts) || 1 })
    msg.value = `已保存 ${row.name} 的 max_attempts`
    await refresh()
  } catch (e) { err.value = errorText(e) }
}
async function saveDomain(row) {
  err.value = ''; msg.value = ''
  try {
    await patchJSON(`/api/jobs/${row.id}/domain`, { domain: String(row.domain).trim() })
    msg.value = `已保存 ${row.name} 的业务域`
    await refresh()
  } catch (e) { err.value = errorText(e); await refresh() }
}
onMounted(async () => {
  try { await refresh() } catch (e) { err.value = errorText(e) }
})
</script>
<template>
  <div class="page">
    <h1>作业</h1>
    <p class="muted">维护业务域与单次最大尝试次数。依赖只允许同域：把作业改到会让上下游跨域的域时，保存会被拒绝并列出冲突边。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="msg" class="tag ok">{{ msg }}</p>
    <div class="panel" v-if="crossEdges.length">
      <h3>现存跨域依赖（编排时会被拦截）</h3>
      <ul>
        <li v-for="(e, i) in crossEdges" :key="i" class="mono">
          {{ e.job }}（{{ e.job_domain }}）→ {{ e.depends_on }}（{{ e.dep_domain }}）
        </li>
      </ul>
    </div>
    <div class="panel">
      <table>
        <thead><tr><th>ID</th><th>名称</th><th>业务域</th><th>依赖</th><th>max_attempts</th><th>夹具</th><th></th></tr></thead>
        <tbody>
          <tr v-for="j in rows" :key="j.id">
            <td>{{ j.id }}</td>
            <td class="mono">{{ j.name }}</td>
            <td><input v-model="j.domain" style="width:7.5rem" /></td>
            <td class="mono">{{ j.depends_on.join(', ') || '—' }}</td>
            <td><input type="number" min="1" max="10" v-model.number="j.max_attempts" style="width:4.5rem" /></td>
            <td class="mono">{{ j.fixture_path }}</td>
            <td>
              <button class="secondary" @click="saveDomain(j)">保存域</button>
              <button class="secondary" @click="saveAttempts(j)">保存次数</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
