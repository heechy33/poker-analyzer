import type { Metadata } from "next";
import { ThemeProvider } from "next-themes";

import { Providers } from "@/components/providers";

import "./globals.css";

export const metadata: Metadata = {
  title: "CoinPoker Hand History Analyzer",
  description: "Upload CoinPoker hand histories, review stats, and study GTO lines.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
          <Providers>{children}</Providers>
        </ThemeProvider>
      </body>
    </html>
  );
}
