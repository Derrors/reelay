import { h } from 'vue';
import { useData } from 'vitepress';
import DefaultTheme from 'vitepress/theme';
import type { Theme } from 'vitepress';
import logo from '../../assets/logo.svg';
import MermaidDiagram from './MermaidDiagram.vue';
import './custom.css';

export default {
  extends: DefaultTheme,
  Layout: () => {
    const { lang } = useData();
    return h(DefaultTheme.Layout, null, {
      'home-hero-image': () => h('div', { class: 'reelay-hero-mark' }, [
        h('img', { src: logo, alt: 'Reelay', width: 208, height: 208 }),
        h('p', lang.value === 'en' ? 'From messages to your media library' : '从消息流，到媒体库'),
      ]),
    });
  },
  enhanceApp({ app }) {
    app.component('MermaidDiagram', MermaidDiagram);
  },
} satisfies Theme;
