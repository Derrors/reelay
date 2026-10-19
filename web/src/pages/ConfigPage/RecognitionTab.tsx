import { Clock3, Gauge } from 'lucide-react';
import { Panel } from '@/components/app/Panel';
import { Select } from '@/components/app/Select';
import { Input } from '@/components/ui/input';
import { llmFormatLabels, timeoutActionLabels } from '@/lib/status';
import { Field } from './fields';
import { obj, text, type Obj } from './shared';

const numberOrBlank = (raw: string) => (raw === '' ? '' : Number(raw));

export function RecognitionTab({ config, setField }: {
  config: Obj; setField: (key: string, name: string, value: unknown) => void;
}) {
  const defaults = obj(config.defaults);
  const approval = obj(config.approval);
  const threshold = typeof defaults.confidence_threshold === 'number' ? defaults.confidence_threshold : 0;

  return (
    <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
      <Panel icon={Gauge} title="识别准确度" description="AI 多有把握才自动处理；没把握的资源会转给你确认。" bodyClassName="p-4 sm:p-5">
        <div className="space-y-5">
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <label htmlFor="confidence-threshold" className="text-[13px] font-medium leading-5">最低置信度</label>
              <span className="rounded-md bg-primary/10 px-2 py-0.5 text-xs font-semibold tabular-nums text-primary">
                {Math.round(threshold * 100)}%
              </span>
            </div>
            <input
              id="confidence-threshold" type="range" min={0} max={1} step={0.05} value={threshold}
              onChange={e => setField('defaults', 'confidence_threshold', Number(e.target.value))}
              className="h-9 w-full cursor-pointer accent-primary"
            />
            <div className="flex justify-between text-[11px] text-muted-foreground">
              <span>更积极</span><span>建议 60% 左右</span><span>更谨慎</span>
            </div>
          </div>
          <Field label="AI 返回格式" hint="老模型不支持结构化输出时再切换。">
            <Select
              value={text(defaults.llm_response_format)}
              onValueChange={v => setField('defaults', 'llm_response_format', v)}
              options={Object.entries(llmFormatLabels).map(([value, label]) => ({ value, label }))}
            />
          </Field>
        </div>
      </Panel>

      <Panel icon={Clock3} title="确认超时" description="一条资源等你确认太久时怎么处理。" bodyClassName="p-4 sm:p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="等待多久算超时">
            <div className="relative">
              <Input
                type="number" min="0.5" step="0.5" className="bg-card pr-12 tabular-nums"
                value={text(approval.timeout_hours)}
                onChange={e => setField('approval', 'timeout_hours', numberOrBlank(e.target.value))}
              />
              <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">小时</span>
            </div>
          </Field>
          <Field label="超时后">
            <Select
              value={text(approval.on_timeout)}
              onValueChange={v => setField('approval', 'on_timeout', v)}
              options={Object.entries(timeoutActionLabels).map(([value, label]) => ({ value, label }))}
            />
          </Field>
        </div>
      </Panel>
    </div>
  );
}
