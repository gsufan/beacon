// Small colored elements: avatars with initials, Scrum role badges and labels. The color is stable per text.
const PALETTE = [1, 2, 3, 4, 5, 6];

export function colorOf(text: string): number {
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export function Avatar({ name, size = 28 }: { name: string; size?: number }) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
  const c = colorOf(name);
  return (
    <span
      aria-hidden="true"
      className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white"
      style={{ width: size, height: size, fontSize: size * 0.4, background: `var(--c${c})` }}
    >
      {initials || "?"}
    </span>
  );
}

const ROLE_COLOR: Record<string, number> = { admin: 5, product_owner: 2, scrum_master: 3, tech_lead: 4, developer: 1 };

export function RoleBadge({ role, label }: { role: string; label: string }) {
  const c = ROLE_COLOR[role] ?? 6;
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-px text-[11px] font-semibold"
      style={{ background: `var(--c${c}-tint)`, color: `var(--c${c})` }}
    >
      {label}
    </span>
  );
}

export function LabelChip({ text }: { text: string }) {
  const c = colorOf(text);
  return (
    <span className="rounded-full px-2 py-px text-[11px] font-medium" style={{ background: `var(--c${c}-tint)`, color: `var(--c${c})` }}>
      {text}
    </span>
  );
}
