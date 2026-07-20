"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { OfflineStudyNotice } from "@/components/OfflineStudyNotice";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { createClient } from "@/lib/supabase/client";

export function LoginForm() {
  const searchParams = useSearchParams();
  const authError = searchParams.get("error") === "auth_callback";
  const errorCode = searchParams.get("error_code");

  const authErrorMessage =
    errorCode === "otp_expired"
      ? "That link has expired or was already used. Request a new magic link and open it once, right away."
      : "Sign-in link expired or invalid. Request a new magic link.";

  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(
    authError ? { type: "error", text: authErrorMessage } : null,
  );

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setMessage(null);

    try {
      const supabase = createClient();
      const origin = window.location.origin;
      const { error } = await supabase.auth.signInWithOtp({
        email,
        options: {
          emailRedirectTo: `${origin}/auth/callback`,
        },
      });

      if (error) {
        setMessage({ type: "error", text: error.message });
      } else {
        setMessage({
          type: "success",
          text: "Check your email for the magic link to sign in.",
        });
      }
    } catch (err) {
      setMessage({
        type: "error",
        text: err instanceof Error ? err.message : "Something went wrong",
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="w-full max-w-md border-zinc-800 bg-zinc-950">
      <CardHeader>
        <p className="text-sm font-medium uppercase tracking-wider text-emerald-400">
          CoinPoker Analyzer
        </p>
        <CardTitle className="text-2xl">Sign in</CardTitle>
        <CardDescription>
          Enter your email and we&apos;ll send you a magic link. No password required.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <OfflineStudyNotice compact />
        <form className="space-y-4" onSubmit={handleSubmit}>
          <div className="space-y-2">
            <label htmlFor="email" className="text-sm font-medium text-zinc-300">
              Email
            </label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={loading}
            />
          </div>
          <Button type="submit" className="w-full" disabled={loading || !email}>
            {loading ? "Sending…" : "Send magic link"}
          </Button>
        </form>
        {message && (
          <p
            className={`rounded-lg border px-3 py-2 text-sm ${
              message.type === "success"
                ? "border-emerald-800 bg-emerald-950/50 text-emerald-300"
                : "border-red-800 bg-red-950/50 text-red-300"
            }`}
            role="status"
          >
            {message.text}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
