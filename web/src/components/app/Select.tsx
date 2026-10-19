import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { cn } from '@/lib/utils';

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends Omit<React.ComponentProps<'select'>, 'onChange'> {
  options: SelectOption[];
  onValueChange: (value: string) => void;
  placeholder?: string;
}

/** 统一样式的下拉框（原生 select，保证键盘与移动端可用）；只展示中文标签，不暴露内部值。 */
export function Select({ options, onValueChange, placeholder, className, ...props }: SelectProps) {
  return (
    <div className={cn('[&>[data-slot=native-select-wrapper]]:w-full', className)}>
      <NativeSelect
        {...props}
        onChange={(event) => onValueChange(event.target.value)}
        className="h-9 w-full bg-card shadow-xs"
      >
        {placeholder ? <NativeSelectOption value="">{placeholder}</NativeSelectOption> : null}
        {options.map((option) => (
          <NativeSelectOption key={option.value} value={option.value}>{option.label}</NativeSelectOption>
        ))}
      </NativeSelect>
    </div>
  );
}
