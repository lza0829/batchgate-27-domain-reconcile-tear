<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const stats = ref(null)
const err = ref('')
onMounted(async () => {
  try { stats.value = await getJSON('/api/dashboard') } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>总览</h1>
    <p class="muted">离线作业依赖编排：全域计数与按业务域切片，分域合计始终等于全域。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" v-if="stats">
      <p>作业 {{ stats.job_count }}（启用 {{ stats.enabled_jobs }}）· 域 {{ stats.domains?.length }}</p>
      <p>编排 {{ stats.orchestration_count }} · 尝试 {{ stats.attempt_count }}</p>
      <p>成功 {{ stats.succeeded }} · 失败 {{ stats.failed }} · 跳过 {{ stats.skipped }}（跨域拦截 {{ stats.blocked_domain }}）</p>
      <p>已有水位的作业 {{ stats.watermarked_jobs }}</p>
      <p class="mono muted">分域合计与全域一致：<span :class="stats.domain_consistent ? 'tag ok' : 'tag danger'">{{ stats.domain_consistent ? '是' : '否' }}</span></p>
      <h4>分域数字</h4>
      <table>
        <thead><tr><th>域</th><th>作业</th><th>启用</th><th>尝试</th><th>成功</th><th>失败</th><th>跳过</th><th>跨域拦截</th><th>水位</th></tr></thead>
        <tbody>
          <tr v-for="d in stats.domains" :key="d.domain">
            <td class="mono">{{ d.domain }}</td>
            <td>{{ d.job_count }}</td>
            <td>{{ d.enabled_jobs }}</td>
            <td>{{ d.attempt_count }}</td>
            <td>{{ d.succeeded }}</td>
            <td>{{ d.failed }}</td>
            <td>{{ d.skipped }}</td>
            <td>{{ d.blocked_domain }}</td>
            <td>{{ d.watermarked_jobs }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
