import { cn } from "@/lib/utils";

const SUITS: Record<string, { glyph: string; className: string }> = {
  d: { glyph: "♦", className: "text-red-500" },
  h: { glyph: "♥", className: "text-red-500" },
  c: { glyph: "♣", className: "text-zinc-950" },
  s: { glyph: "♠", className: "text-zinc-950" },
};

export function CardFace({ card, small = false }: { card: string; small?: boolean }) {
  const rank = card?.slice(0, -1).toUpperCase() || "?";
  const suit = card?.slice(-1).toLowerCase();
  const suitMeta = SUITS[suit] ?? { glyph: suit.toUpperCase(), className: "text-zinc-950" };

  return (
    <span
      className={cn(
        "inline-flex shrink-0 flex-col items-center justify-center rounded border border-zinc-300 bg-zinc-100 font-bold leading-none text-zinc-950 shadow-sm",
        small ? "h-8 w-6 text-xs" : "h-10 w-8 text-sm",
      )}
      aria-label={card}
      title={card}
    >
      <span>{rank}</span>
      <span className={cn("mt-0.5", suitMeta.className)}>{suitMeta.glyph}</span>
    </span>
  );
}

export function CardStrip({ cards, small = false }: { cards: readonly string[]; small?: boolean }) {
  if (cards.length === 0) {
    return <span className="text-xs text-muted-foreground">No cards</span>;
  }

  return (
    <span className="inline-flex items-center gap-1">
      {cards.map((card, index) => (
        <CardFace key={`${card}-${index}`} card={card} small={small} />
      ))}
    </span>
  );
}
