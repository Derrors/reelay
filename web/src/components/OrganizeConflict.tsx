import { FileVideo, FolderTree, Replace, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
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

/** 冲突说明：存放位置与两份文件并列。 */
export function OrganizeConflict({ conflict }: { conflict: IOrganizeConflict }) {
  return (
    <div className="space-y-3 rounded-lg border border-border bg-muted/40 p-3">
      <div className="text-xs text-muted-foreground">
        存放位置 <span className="break-all font-mono text-foreground">{conflict.target_dir}/</span>
      </div>
      <FileRow label="库里已有" file={conflict.existing} />
      <FileRow label="本次转存" file={conflict.incoming} />
    </div>
  );
}

/** 卡片上的两个处理按钮；执行前用确认弹窗核对文件。 */
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

/**
 * 有整理冲突时的「重新整理」：直接重新整理只会再撞上同一个冲突，所以先选怎么处理——
 * 删除本次转存的文件、覆盖保存，或者（改过命名、目录设置后）按当前设置重新规划位置。
 * 弹窗里已列出两份文件，选择即执行，不再二次确认。
 */
export function ConflictDialog({ conflict, open, onOpenChange, onChoose, canReplan }: {
  conflict: IOrganizeConflict; open: boolean; onOpenChange: (open: boolean) => void;
  /** undefined 表示不处理冲突、按当前设置重新整理。 */
  onChoose: (choice: ConflictChoice | undefined) => void;
  /** 服务端是否允许不带选择的重新整理。 */
  canReplan: boolean;
}) {
  const pick = (choice: ConflictChoice | undefined) => { onOpenChange(false); onChoose(choice); };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-4 p-5 sm:max-w-lg">
        <DialogHeader className="text-left">
          <DialogTitle className="text-base">库里已有同一份资源</DialogTitle>
          <DialogDescription className="text-[13px] leading-5">
            整理到的位置已经有这一集（或这部电影）的文件。选一种处理方式；被替换或删除的那份会移进 115 回收站。
          </DialogDescription>
        </DialogHeader>
        <OrganizeConflict conflict={conflict} />
        {canReplan ? (
          <p className="text-xs leading-5 text-muted-foreground">
            改过命名模板或目录设置的话，也可以按当前设置重新规划位置；新位置仍然冲突时会再请你选择。
          </p>
        ) : null}
        <DialogFooter className="gap-2 sm:justify-between">
          {canReplan ? (
            <Button variant="ghost" onClick={() => pick(undefined)}><FolderTree />按当前设置重新规划</Button>
          ) : <span />}
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <Button variant="outline" className="text-destructive" onClick={() => pick('skip')}><Trash2 />删除转存文件</Button>
            <Button variant="destructive" onClick={() => pick('replace')}><Replace />覆盖保存</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
