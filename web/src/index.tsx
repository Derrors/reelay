import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ThemeProvider } from 'next-themes';
import { ThemeColor } from '@/components/app/ThemeColor';
import App from './app';
import './index.css';

const root = document.getElementById('root');
if (!root) throw new Error('页面缺少 #root 节点');

createRoot(root).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange storageKey="reelay-theme">
      <ThemeColor />
      <App />
    </ThemeProvider>
  </StrictMode>,
);
