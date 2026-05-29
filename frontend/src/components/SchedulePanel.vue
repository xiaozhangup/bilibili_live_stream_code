<script setup>
import { ref, onMounted, inject } from 'vue';
import { useBridge } from '@/api/bridge';

const showModal = inject('showModal');
const { getScheduleConfig, setScheduleConfig } = useBridge();

const loading = ref(false);
const enabled = ref(false);
const periods = ref([{ start: '08:00', end: '10:00' }]);

const load = async () => {
  loading.value = true;
  try {
    const res = await getScheduleConfig();
    if (res.code === 0 && res.data) {
      enabled.value = !!res.data.enabled;
      periods.value = Array.isArray(res.data.periods) && res.data.periods.length
        ? res.data.periods.map(p => ({ start: p.start || '08:00', end: p.end || '10:00' }))
        : [];
    } else {
      periods.value = [];
    }
  } catch (e) {
    showModal?.('错误', `加载失败: ${e}`, 'error');
  } finally {
    loading.value = false;
  }
};

onMounted(load);

const addPeriod = () => {
  periods.value.push({ start: '08:00', end: '10:00' });
};

const removePeriod = (idx) => {
  periods.value.splice(idx, 1);
};

const save = async () => {
  loading.value = true;
  try {
    const res = await setScheduleConfig(enabled.value, periods.value);
    if (res.code === 0) showModal?.('成功', '已保存定时开播设置', 'success');
    else showModal?.('保存失败', res.msg || '未知错误', 'error');
  } catch (e) {
    showModal?.('保存失败', String(e), 'error');
  } finally {
    loading.value = false;
  }
};
</script>

<template>
  <div class="panel fade-in">
    <div class="header-section">
      <h2 class="title">定时开播</h2>
      <p class="subtitle">支持设置多个时段，到时间自动开播/停播</p>
    </div>

    <div class="card config-card">
      <div class="row" style="justify-content: space-between;">
        <label class="switch">
          <input type="checkbox" v-model="enabled">
          <span class="slider"></span>
        </label>
        <div class="switch-label">
          {{ enabled ? '已启用' : '已关闭' }}
        </div>
      </div>

      <div class="periods">
        <div v-if="!periods.length" class="empty-tip">
          尚未添加时段
        </div>

        <div v-for="(p, idx) in periods" :key="idx" class="period-row">
          <input type="time" v-model="p.start" class="gemini-input time-input">
          <span class="dash">-</span>
          <input type="time" v-model="p.end" class="gemini-input time-input">
          <button class="btn btn-secondary small-btn" @click="removePeriod(idx)" title="删除">
            删除
          </button>
        </div>

        <div class="actions">
          <button class="btn btn-secondary" @click="addPeriod" :disabled="loading">添加时段</button>
          <button class="btn btn-primary" @click="save" :disabled="loading">
            {{ loading ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <p class="hint">
      <span class="info-icon">i</span> 若跨天请设置如 23:00 - 01:00
    </p>
  </div>
</template>

<style scoped>
.panel {
  max-width: 600px;
  margin: 0 auto;
}

.header-section {
  margin-bottom: 24px;
}

.title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-main);
  margin: 0 0 4px 0;
}

.subtitle {
  font-size: 14px;
  color: var(--text-sub);
  margin: 0;
}

.config-card {
  padding: 24px;
  border: 1px solid #e0e3e7;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
}

.row {
  display: flex;
  gap: 12px;
  align-items: center;
}

.switch-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-sub);
}

.periods {
  margin-top: 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.empty-tip {
  font-size: 13px;
  color: var(--text-sub);
  padding: 10px 0;
}

.period-row {
  display: grid;
  grid-template-columns: 1fr auto 1fr auto;
  align-items: center;
  gap: 10px;
}

.time-input {
  padding: 10px 12px;
}

.dash {
  color: var(--text-sub);
  font-weight: 600;
}

.small-btn {
  padding: 10px 14px;
  border-radius: 8px;
  white-space: nowrap;
  font-size: 13px;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 6px;
}

.hint {
  margin-top: 12px;
  font-size: 13px;
  color: var(--text-sub);
  text-align: center;
}

.info-icon {
  display: inline-block;
  width: 16px;
  height: 16px;
  line-height: 16px;
  border-radius: 50%;
  text-align: center;
  background: rgba(0, 0, 0, 0.08);
  margin-right: 6px;
  font-size: 12px;
}

/* 简单开关样式 */
.switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
}
.switch input { display: none; }
.slider {
  position: absolute;
  cursor: pointer;
  inset: 0;
  background-color: #cfd8dc;
  transition: .2s;
  border-radius: 999px;
}
.slider:before {
  position: absolute;
  content: "";
  height: 18px;
  width: 18px;
  left: 3px;
  top: 3px;
  background-color: white;
  transition: .2s;
  border-radius: 50%;
}
input:checked + .slider { background-color: var(--primary-color); }
input:checked + .slider:before { transform: translateX(20px); }
</style>

