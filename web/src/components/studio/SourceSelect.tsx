import type { SourceOption } from "../../lib/sources";

/** A "current source" placeholder followed by every selectable source. */
export function SourceSelect({
  options,
  currentLabel,
  disabled,
  onChoose,
  ariaLabel,
}: {
  options: SourceOption[];
  currentLabel: string;
  disabled: boolean;
  onChoose: (option: SourceOption) => void;
  ariaLabel?: string;
}) {
  return (
    <select
      aria-label={ariaLabel}
      value="__current"
      disabled={disabled}
      onChange={(event) => {
        const chosen = options.find((o) => o.id === event.target.value);
        if (chosen) onChoose(chosen);
      }}
    >
      <option value="__current">{currentLabel}</option>
      {options.map((option) => (
        <option key={option.id} value={option.id}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
