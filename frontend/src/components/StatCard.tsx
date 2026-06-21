import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <Card className="border-zinc-800 bg-zinc-950/50">
      <CardContent className="p-4">
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </p>
        <p className={cn("mt-1 text-2xl font-semibold tabular-nums tracking-tight", valueClassName)}>
          {value}
        </p>
      </CardContent>
    </Card>
  );
}
