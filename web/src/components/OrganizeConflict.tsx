import { FileVideo, Replace, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { sizeText } from '@/lib/format';
import { confirmConflict, type ConflictChoice } from '@/lib/conflict';
import type { IConflictFile, IOrganizeConflict } from '@/lib/types';

function FileRow({ label, file }: { label: string; file: IConflictFile }) {
  return (
    <div className="flex min-w-0 gap-2">
      <FileVideo className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 text-[13px] leading-5">
        <div className="text-xs text-muted-foreground">{label}{file.size != null ? ` · ${sizeText(file.size)}` : ''}</div>
        <div className="break-all font-mono text-xs">{file.name ?? '（未知文件名）'}</div>
      </div>
    </div>
  );
}

/** 任务详情里的冲突说明：两份文件并列，下面是两种处理。 */
export function OrganizeConflict({ conflict, busy, onChoose }: {
  conflict: IOrganizeConflict; busy: boolean; onChoose: (choice: ConflictChoice) => void;
}) {
  return (
    <div className="space-y-3 rounded-lg border border-border bg-muted/40 p-3">
      <div className="text-xs text-muted-foreground">
        存放位置 <span className="break-all font-mono text-foreground">{conflict.target_dir}/</span>
      </div>
      <FileRow label="库里已有" file={conflict.existing} />
      <FileRow label="本次转存" file={conflict.incoming} />
      <ConflictButtons conflict={conflict} busy={busy} onChoose={onChoose} />
    </div>
  );
}

export function ConflictButtons({ conflict, busy, onChoose, size = 'sm' }: {
  conflict: IOrganizeConflict; busy: boolean; onChoose: (choice: ConflictChoice) => void; size?: 'sm' | 'default';
}) {
  const choose = async (choice: ConflictChoice) => { if (await confirmConflict(choice, conflict)) onChoose(choice); };
  return (
    <div className="flex flex-wrap gap-2">
      <Button type="button" size={size} variant="outline" disabled={busy} onClick={() => void choose('skip')}
        title="保留库里的文件，删除本次转存的这份" className="text-destructive">
        <Trash2 />删除转存文件
      </Button>
      <Button type="button" size={size} disabled={busy} onClick={() => void choose('replace')}
        title="删除库里的文件，换成本次转存的这份">
        <Replace />覆盖保存
      </Button>
    </div>
  );
}
