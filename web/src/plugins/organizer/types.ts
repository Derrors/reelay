/** 二级分类规则的展示形式（动作 category_rules）：按文件顺序，条件值附中文名；conditions 为空的分类接住其余作品。 */
export interface ICategoryGroup {
  media: 'movie' | 'tv';
  categories: Array<{
    path: string;
    conditions: Array<{ field: string; label: string; values: Array<{ code: string; name: string }> }>;
  }>;
}

/** 整理预览（动作 preview_plan）的一项：文件整理后的目录与文件名。 */
export interface IOrganizePreviewItem {
  src_id: string;
  src_name: string;
  target_dir: string;
  target_name: string;
}
