import { BrainCircuit, Clock3, Gauge, Layers } from 'lucide-react';
import { Panel, PanelColumns } from '@/components/app/Panel';
import { Select } from '@/components/app/Select';
import { Input } from '@/components/ui/input';
import { llmFormatLabels, timeoutActionLabels } from '@/lib/status';
import { cn } from '@/lib/utils';
import { Field, SwitchRow } from './fields';
import { obj, strings, text, type Obj } from './shared';

const numberOrBlank = (raw: string) => (raw === '' ? '' : Number(raw));

type Option = { value: string; label: string };
const SUBTITLES: Option[] = [
  { value: 'chs', label: '简体中字' }, { value: 'cht', label: '繁体中字' }, { value: 'eng', label: '英字' },
  { value: 'jpn', label: '日字' }, { value: 'kor', label: '韩字' },
];
const AUDIO: Option[] = [
  { value: 'mandarin', label: '国语' }, { value: 'cantonese', label: '粤语' }, { value: 'english', label: '英语' },
  { value: 'japanese', label: '日语' }, { value: 'korean', label: '韩语' },
];
const SOURCES: Option[] = [
  { value: 'remux', label: 'Remux' }, { value: 'bluray', label: '蓝光' }, { value: 'web-dl', label: 'WEB-DL' },
  { value: 'webrip', label: 'WEBRip' }, { value: 'hdtv', label: 'HDTV' }, { value: 'dvd', label: 'DVD' },
];
const HDR: Option[] = [
  { value: 'any', label: '不在意' }, { value: 'prefer', label: '优先 HDR / 杜比视界' }, { value: 'avoid', label: '避开 HDR（设备不支持）' },
];

/** 有序多选：点选的顺序就是优先级，再点一次取消。 */
function OrderedChips({ label, options, value, onChange }: {
  label: string; options: Option[]; value: string[]; onChange: (value: string[]) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((option) => {
        const rank = value.indexOf(option.value);
        return (
          <button key={option.value} type="button" aria-pressed={rank >= 0}
            onClick={() => onChange(rank >= 0 ? value.filter((v) => v !== option.value) : [...value, option.value])}
            className={cn('inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-xs transition-colors',
              rank >= 0 ? 'border-primary/50 bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:bg-muted/60')}>
            {rank >= 0 ? <span className="grid size-4 place-items-center rounded-full bg-primary text-[10px] text-primary-foreground">{rank + 1}</span> : null}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function RecognitionTab({ config, setField }: {
  config: Obj; setField: (key: string, name: string, value: unknown) => void;
}) {
  const defaults = obj(config.defaults);
  const approval = obj(config.approval);
  const threshold = typeof defaults.confidence_threshold === 'number' ? defaults.confidence_threshold : 0;
  const aiSection = obj(config.ai);
  const suggest = obj(aiSection.suggest_auto);
  const release = obj(config.release);
  const setSuggest = (name: string, value: unknown) => setField('ai', 'suggest_auto', { ...suggest, [name]: value });
  const pct = (v: unknown) => (typeof v === 'number' ? String(Math.round(v * 100)) : '');

  return (
    <PanelColumns
      left={<>
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

        <Panel icon={Layers} title="版本偏好"
          description="同一集有多个版本时，待审批推荐哪一个、洗版时是否替换，都按这里比较：字幕 → 清晰度 → HDR → 音轨 → 片源 → 体积。"
          bodyClassName="p-4 sm:p-5">
          <div className="space-y-4">
            <Field label="优先的字幕" hint="按点选顺序排优先级；都不选表示不看字幕。">
              <OrderedChips label="优先的字幕" options={SUBTITLES} value={strings(release.prefer_subtitles)}
                onChange={v => setField('release', 'prefer_subtitles', v)} />
            </Field>
            <Field label="优先的音轨">
              <OrderedChips label="优先的音轨" options={AUDIO} value={strings(release.prefer_audio)}
                onChange={v => setField('release', 'prefer_audio', v)} />
            </Field>
            <Field label="优先的片源">
              <OrderedChips label="优先的片源" options={SOURCES} value={strings(release.prefer_sources)}
                onChange={v => setField('release', 'prefer_sources', v)} />
            </Field>
            <Field label="HDR">
              <Select value={text(release.hdr) || 'any'} onValueChange={v => setField('release', 'hdr', v)} options={HDR} />
            </Field>
          </div>
        </Panel>
      </>}
      right={<>
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

        <Panel icon={BrainCircuit} title="AI 能力"
          description="AI 提议，事实源校验，人工裁决，裁决结果反馈给 AI：你的批准、忽略与修正会让 AI 越来越懂你。"
          bodyClassName="p-4 sm:p-5">
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="识别时参考的修正示例" hint="同一来源最近修正过的识别结果，作为示例交给 AI；0 关闭。">
                <Input type="number" min={0} max={10} className="bg-card tabular-nums" value={text(aiSection.examples)}
                  onChange={e => setField('ai', 'examples', numberOrBlank(e.target.value))} />
              </Field>
              <Field label="偏好判断参考的近期裁决" hint="「AI 按偏好判断」时附上同一来源最近的批准与忽略；0 关闭。">
                <Input type="number" min={0} max={100} className="bg-card tabular-nums" value={text(aiSection.history)}
                  onChange={e => setField('ai', 'history', numberOrBlank(e.target.value))} />
              </Field>
            </div>
            <SwitchRow title="转人工前先让 AI 试一次"
              description="分享里的文件名认不出集号或季号、同一集有多个文件、多个正片候选时，先让 AI 给方案；方案按同一套规则校验，不过才转人工。"
              checked={aiSection.assist !== false} onCheckedChange={v => setField('ai', 'assist', v)} />
            <SwitchRow title="待审批显示建议"
              description="每条待审批附上建议（批准 / 忽略 / 需要你看一下）与理由；建议由规则决定，AI 只负责把理由写通顺。"
              checked={aiSection.advice !== false} onCheckedChange={v => setField('ai', 'advice', v)} />
            <div>
              <p className="mb-1.5 text-[13px] font-medium">建议改为自动转存的门槛</p>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="至少确认过（次）">
                  <Input type="number" min={5} max={500} className="bg-card tabular-nums" value={text(suggest.min_decisions)}
                    onChange={e => setSuggest('min_decisions', numberOrBlank(e.target.value))} />
                </Field>
                <Field label="批准率不低于（%）">
                  <Input type="number" min={50} max={100} className="bg-card tabular-nums" value={pct(suggest.approve_rate)}
                    onChange={e => setSuggest('approve_rate', e.target.value === '' ? '' : Number(e.target.value) / 100)} />
                </Field>
                <Field label="修正率不高于（%）">
                  <Input type="number" min={0} max={100} className="bg-card tabular-nums" value={pct(suggest.max_correct_rate)}
                    onChange={e => setSuggest('max_correct_rate', e.target.value === '' ? '' : Number(e.target.value) / 100)} />
                </Field>
              </div>
              <p className="mt-1.5 text-xs leading-5 text-muted-foreground">达到门槛的来源会在「监听来源」和总览里提示；只是建议，由你决定是否切换。</p>
            </div>
          </div>
        </Panel>
      </>}
    />
  );
}
