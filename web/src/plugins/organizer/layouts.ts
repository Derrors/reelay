/** 目录方式：转存前决定放进哪个文件夹（plugins.organizer.layout），示例按「漫长的季节」第 1 季。 */
export const LAYOUTS = [
  { value: 'category', label: '二级分类', description: '按类型、题材与地区分到「一级/二级」文件夹', example: '剧集/国产剧集/漫长的季节 (2023)' },
  { value: 'media_type', label: '按媒体类型', description: '电影、剧集、动漫各放一个文件夹', example: '剧集/漫长的季节 (2023)' },
  { value: 'none', label: '不分类', description: '直接放在来源的保存目录下', example: '漫长的季节 (2023)' },
] as const;

export const LAYOUT_LABELS: Record<string, string> = Object.fromEntries(LAYOUTS.map(x => [x.value, x.label]));
