import type { Metadata } from "next";

import { GeistMono } from "geist/font/mono";
import localFont from "next/font/local";

import { ThemeProvider } from "@/components/theme-provider";

const nunito = localFont({
  src: [
    { path: "../../public/fonts/Nunito-Regular.ttf", weight: "400", style: "normal" },
    { path: "../../public/fonts/Nunito-Medium.ttf", weight: "500", style: "normal" },
    { path: "../../public/fonts/Nunito-SemiBold.ttf", weight: "600", style: "normal" },
    { path: "../../public/fonts/Nunito-Bold.ttf", weight: "700", style: "normal" },
  ],
  variable: "--font-nunito",
  display: "swap",
});
import { AuthProvider } from "@/components/auth-provider";
import { GlobalLoadingBar } from "@/components/loading-bar";
import { QueryProvider } from "@/components/query-provider";
import { Toaster } from "@/components/ui/sonner";

import "./globals.css";

export const metadata: Metadata = {
  title: "Sarj Docs",
  description: "Internal knowledge base for Sarj",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={`${nunito.variable} ${GeistMono.variable}`}>
      <body className="antialiased">
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <QueryProvider>
            <AuthProvider>{children}</AuthProvider>
            <GlobalLoadingBar />
          </QueryProvider>
          <Toaster position="bottom-right" />
        </ThemeProvider>
      </body>
    </html>
  );
}
