<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';
import { useData } from 'vitepress';

const props = defineProps<{ code: string }>();
const { isDark, lang } = useData();
const labels = computed(() => lang.value === 'en' ? {
  error: 'The diagram could not be displayed. Expand the source below to read it.',
  diagram: 'Architecture flowchart', loading: 'Loading diagram…', source: 'View Mermaid source',
} : {
  error: '图表暂时无法显示，可以展开下方源码阅读。',
  diagram: '架构流程图', loading: '正在加载流程图…', source: '查看 Mermaid 源码',
});
const diagram = ref('');
const error = ref(false);
const id = `reelay-diagram-${useId().replace(/[^a-z\d-]/gi, '')}`;
let mounted = false;
let revision = 0;

async function render() {
  if (!mounted) return;
  const current = ++revision;
  error.value = false;
  try {
    // 只在浏览器需要图时加载，首页、搜索与服务端渲染不加载 Mermaid。
    const { default: mermaid } = await import('mermaid');
    if (current !== revision) return;
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: isDark.value ? 'dark' : 'default' });
    const result = await mermaid.render(`${id}-${current}`, props.code);
    if (current === revision) diagram.value = result.svg;
  } catch {
    if (current === revision) error.value = true;
  }
}

onMounted(() => { mounted = true; void render(); });
watch([isDark, () => props.code], render);
onBeforeUnmount(() => { mounted = false; revision++; });
</script>

<template>
  <figure class="reelay-diagram">
    <p v-if="error" role="status">{{ labels.error }}</p>
    <div v-else-if="diagram" class="reelay-diagram-scroll" role="img" :aria-label="labels.diagram" tabindex="0" v-html="diagram" />
    <p v-else role="status">{{ labels.loading }}</p>
    <details>
      <summary>{{ labels.source }}</summary>
      <pre><code>{{ code }}</code></pre>
    </details>
  </figure>
</template>
