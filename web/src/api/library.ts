import { get, query } from './request';

/** 媒体库里的一部作品：同一片名的已入库任务汇总。 */
export interface ITitleGroup {
  title: string;
  media_type: string | null;
  year: number | null;
  tmdbid: number | null;
  episodes: number[];
  /** 跨多季时按（季, 集）列出已收录的集；单季为 null。 */
  episode_pairs: number[][] | null;
  tasks: number;
  bytes: number;
  /** 典型清晰度，如 2160p。 */
  resolution: string | null;
  /** 版本标签，如「简体中字」「杜比视界」。 */
  release: string[];
  origin_country: string[];
  genre_ids: number[];
  poster_path: string | null;
  save_dir_alias: string | null;
  files: string[];
  updated_at: number;
}

export interface ILibraryResp {
  groups: ITitleGroup[];
  total_done: number;
  estimated_gb: number;
}

export const libraryApi = {
  /** 按全部已入库任务汇总，不限条数；排序在前端做。 */
  list: (q?: string, mediaType?: string) => get<ILibraryResp>(`library${query({ q, media_type: mediaType })}`),
};
