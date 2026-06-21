import { NextResponse } from "next/server";

import { createClient } from "@/lib/supabase/server";

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const next = searchParams.get("next") ?? "/dashboard";

  const oauthError = searchParams.get("error");
  const errorCode = searchParams.get("error_code");
  if (oauthError) {
    const login = new URL(`${origin}/login`);
    login.searchParams.set("error", "auth_callback");
    if (errorCode) {
      login.searchParams.set("error_code", errorCode);
    }
    return NextResponse.redirect(login);
  }

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
  }

  return NextResponse.redirect(`${origin}/login?error=auth_callback`);
}
