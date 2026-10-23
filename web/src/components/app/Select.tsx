import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { cn } from '@/lib/utils';

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

/** 值为空时实际生效的选项（如来源沿用的默认值、按分类自动选出的目录）。 */
export interface SelectFallback {
  value: string;
  /** 替换该选项的文字；不给时在原文字后标「 · note」 */
  label?: string;
  note?: string;
}

interface SelectProps extends Omit<React.ComponentProps<'select'>, 'onChange' | 'value'> {
  value: string;
  options: SelectOption[];
  onValueChange: (value: string) => void;
  placeholder?: string;
  fallback?: SelectFallback;
}

/** 统一样式的下拉框（原生 select，保证键盘与移动端可用）；只展示中文标签，不暴露内部值。
 *
 *  有 fallback 时不再另加一项「默认 / 自动」：值为空就直接选中实际生效的那个选项并标注说明，
 *  选它等于清空（继续跟随默认值或自动结果）。fallback 不在选项里时才用 placeholder 占位。 */
export function Select({ value, options, onValueChange, placeholder, fallback, className, ...props }: SelectProps) {
  const inherited = fallback && options.some(o => o.value === fallback.value) ? fallback : undefined;
  return (
    <div className={cn('[&>[data-slot=native-select-wrapper]]:w-full', className)}>
      <NativeSelect
        {...props}
        value={value || inherited?.value || ''}
        onChange={(event) => onValueChange(inherited && event.target.value === inherited.value ? '' : event.target.value)}
        className="h-9 w-full bg-card shadow-xs"
      >
        {placeholder && !inherited ? <NativeSelectOption value="">{placeholder}</NativeSelectOption> : null}
        {options.map((option) => (
          <NativeSelectOption key={option.value} value={option.value} disabled={option.disabled}>
            {option.value === inherited?.value ? inherited.label ?? `${option.label} · ${inherited.note ?? '默认'}` : option.label}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </div>
  );
}
