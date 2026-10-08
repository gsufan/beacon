export default function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
        checked ? "bg-accent" : "bg-line-strong"
      }`}
      aria-label={label}
      aria-pressed={checked}
    >
      <span
        className={`absolute left-0 top-0.5 h-4 w-4 rounded-full transition-transform ${
          checked ? "translate-x-[18px] bg-accent-on" : "translate-x-0.5 bg-ink-2"
        }`}
      />
    </button>
  );
}
