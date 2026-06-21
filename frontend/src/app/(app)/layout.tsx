import { AppShell } from "@/components/app-shell";
import { HandReviewModal } from "@/components/hand-review/HandReviewModal";
import { createClient } from "@/lib/supabase/server";

export default async function AppLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return (
    <AppShell email={user?.email ?? "Signed in"}>
      {children}
      <HandReviewModal />
    </AppShell>
  );
}
