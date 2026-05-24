import { BarChart3, Upload } from "lucide-react";
import Link from "next/link";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-10 px-6 py-16">
      <header className="space-y-3">
        <p className="text-sm font-medium uppercase tracking-wider text-emerald-400">
          CoinPoker Analyzer
        </p>
        <h1 className="text-4xl font-semibold tracking-tight text-white">
          Hand History Analyzer
        </h1>
        <p className="text-lg text-zinc-400">
          Upload CoinPoker <code className="text-zinc-300">.txt</code> files, track
          VPIP / PFR / 3-bet / BB per 100, and review hands with WASM GTO solving
          plus Claude coaching.
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2">
        <article className="rounded-xl border border-zinc-800 bg-zinc-950 p-6">
          <Upload className="mb-3 h-6 w-6 text-emerald-400" />
          <h2 className="text-lg font-medium text-white">Upload histories</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Presign and complete uploads through the FastAPI backend once you are
            signed in with Supabase magic link auth.
          </p>
        </article>
        <article className="rounded-xl border border-zinc-800 bg-zinc-950 p-6">
          <BarChart3 className="mb-3 h-6 w-6 text-emerald-400" />
          <h2 className="text-lg font-medium text-white">Stats dashboard</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Aggregate VPIP, PFR, 3-bet%, and BB/100 from parsed hands stored in
            Supabase Postgres.
          </p>
        </article>
      </section>

      <footer className="text-sm text-zinc-500">
        API health:{" "}
        <Link
          className="text-emerald-400 underline-offset-4 hover:underline"
          href={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/health`}
          rel="noreferrer"
          target="_blank"
        >
          {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/health
        </Link>
      </footer>
    </main>
  );
}
