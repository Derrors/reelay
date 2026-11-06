/** 界面外观：主题与毛玻璃。只保存在当前浏览器，立即生效，不经过页面底部的保存。 */
import { Info, Layers, Monitor, Moon, Palette, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { Notice } from '@/components/app/Notice';
import { Panel } from '@/components/app/Panel';
import { Segmented } from '@/components/app/Segmented';
import { useGlass } from '@/hooks/useGlass';
import { setGlass } from '@/lib/appearance';
import { SwitchRow } from './fields';

const THEMES = [
  { value: 'system', label: <><Monitor className="size-3.5" />跟随系统</> },
  { value: 'light', label: <><Sun className="size-3.5" />浅色</> },
  { value: 'dark', label: <><Moon className="size-3.5" />深色</> },
];

export function AppearanceTab() {
  const { theme, setTheme } = useTheme();
  const glass = useGlass();
  return (
    <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
      <Panel icon={Palette} title="界面外观" description="只保存在这个浏览器，改了立即生效，不需要点保存。" bodyClassName="space-y-4 p-4 sm:p-5">
        <div className="space-y-2">
          <p className="text-[13px] font-medium">主题</p>
          <Segmented label="主题" size="md" value={theme ?? 'system'} onChange={setTheme} options={THEMES} />
        </div>
        <SwitchRow
          title={<span className="inline-flex items-center gap-1.5"><Layers className="size-3.5 text-muted-foreground" />毛玻璃效果</span>}
          description="侧栏、导航、弹窗与提醒半透明，背景铺最近入库作品的剧照（模糊处理）；卡片与表格保持实色。"
          checked={glass.enabled} onCheckedChange={setGlass} disabled={glass.reduced}
        />
        {glass.reduced ? (
          <Notice tone="info" icon={Info}>系统开启了「减少透明度」，界面已自动使用实色；关掉系统的这个选项后毛玻璃才会生效。</Notice>
        ) : null}
      </Panel>
    </div>
  );
}
