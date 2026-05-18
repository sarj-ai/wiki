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
  title: "Sarj Wiki",
  description: "Internal knowledge base for Sarj",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={`${nunito.variable} ${GeistMono.variable}`}>
      <head>
        {/*
          Polyfill the esbuild ``__name`` helper for inline scripts.
          Turbopack (Next.js 16) emits ``__name(fn, "fn")`` calls inside
          inline ``<script>`` blocks — e.g. the ``next-themes`` no-flash
          script — but the helper itself is only declared inside ES
          modules, not in the global scope inline scripts run in.
          Result: ``ReferenceError: __name is not defined`` on first
          paint and the whole document fails to hydrate. Declaring the
          helper here, ahead of any subsequent inline scripts, makes
          those calls a cheap no-op.
        */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              'window.__name=function(t,n){try{Object.defineProperty(t,"name",{value:n,configurable:true})}catch(e){}return t;}',
          }}
        />
      </head>
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
