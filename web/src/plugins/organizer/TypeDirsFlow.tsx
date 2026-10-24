import { at, text } from '@/lib/values';
import type { PluginEditorProps } from '../types';
import { Branch, FolderInputNode, GraphCanvas, RootNode, RuleNode } from './FlowGraph';

const CHIP = 'rounded bg-muted px-1.5 py-px text-[11px] text-foreground/85';
const LABEL = 'mr-1 text-muted-foreground';

/** 按媒体类型（plugins.organizer.type_dirs）的流程图：与后端 type_dir 的判断顺序一致——
 *  先看是不是动漫（类型为动漫或题材含动画），再按 TMDB 类型分到电影 / 剧集；文件夹名在图上直接改。 */
export function TypeDirsFlow({ section, setSection }: PluginEditorProps) {
  const dirs = at(section, 'type_dirs');
  const set = (key: string) => (value: string) =>
    setSection(s => ({ ...s, type_dirs: { ...at(s, 'type_dirs'), [key]: value } }));
  const anime = text(dirs.anime).trim();
  return (
    <div className="space-y-3">
      <p className="max-w-3xl text-xs leading-5 text-muted-foreground">
        从左往右：按编号依次判断，归入第一个满足的文件夹；文件夹名直接在右侧修改，只放一层。动漫的文件夹留空时不单独分，动漫作品按电影 / 剧集归入。
      </p>
      <GraphCanvas>
        <Branch
          node={<RootNode />}
          children={[
            <Branch
              key="anime"
              node={(
                <RuleNode order={1} hint="先判断是不是动漫" dashed={!anime}>
                  {anime ? (
                    <>
                      <span className={CHIP}><span className={LABEL}>类型</span>动漫</span>
                      <span className="py-px text-[11px] text-muted-foreground">或</span>
                      <span className={CHIP}><span className={LABEL}>题材</span>动画</span>
                    </>
                  ) : <span className="py-px text-xs text-muted-foreground">动漫不单独分，按电影 / 剧集归入</span>}
                </RuleNode>
              )}
              children={[
                <FolderInputNode key="dir" label="动漫放进的文件夹" value={text(dirs.anime)} placeholder="留空不单独分" onChange={set('anime')} />,
              ]}
            />,
            <Branch
              key="movie"
              node={<RuleNode order={2} hint="再按 TMDB 类型"><span className={CHIP}><span className={LABEL}>TMDB 类型</span>电影</span></RuleNode>}
              children={[<FolderInputNode key="dir" label="电影放进的文件夹" value={text(dirs.movie)} placeholder="文件夹名" onChange={set('movie')} />]}
            />,
            <Branch
              key="tv"
              node={<RuleNode order={3} hint="再按 TMDB 类型"><span className={CHIP}><span className={LABEL}>TMDB 类型</span>剧集</span></RuleNode>}
              children={[<FolderInputNode key="dir" label="剧集放进的文件夹" value={text(dirs.tv)} placeholder="文件夹名" onChange={set('tv')} />]}
            />,
          ]}
        />
      </GraphCanvas>
    </div>
  );
}
