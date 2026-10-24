import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

/** 样例消息列表（来源接入向导与识别规则共用）：每条一个文本框，可以增删，最多 max 条。 */
export function SampleList({ samples, onChange, max, mono }: {
  samples: string[]; onChange: (samples: string[]) => void; max: number;
  /** 写正则时用等宽字体，方便对照。 */
  mono?: boolean;
}) {
  return (
    <>
      {samples.map((sample, i) => (
        <div key={i} className="flex gap-2">
          <Textarea value={sample} className={cn('min-h-16 bg-card text-xs', mono && 'font-mono')} placeholder={`第 ${i + 1} 条消息`}
            onChange={(e) => onChange(samples.map((s, n) => (n === i ? e.target.value : s)))} />
          {samples.length > 1 ? (
            <Button variant="ghost" size="icon" className="size-9 shrink-0 text-muted-foreground sm:size-8" aria-label="删除这条样例"
              onClick={() => onChange(samples.filter((_, n) => n !== i))}><Trash2 /></Button>
          ) : null}
        </div>
      ))}
      {samples.length < max ? (
        <Button variant="outline" size="sm" onClick={() => onChange([...samples, ''])}><Plus />再加一条</Button>
      ) : null}
    </>
  );
}
