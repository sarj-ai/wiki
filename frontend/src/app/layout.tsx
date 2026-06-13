import type { Metadata, Viewport } from "next";

import { GeistMono } from "geist/font/mono";
import localFont from "next/font/local";

import { GlobalLoadingBar } from "@/components/loading-bar";
import { QueryProvider } from "@/components/query-provider";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { API_URL } from "@/lib/env";

import "./globals.css";

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

export const metadata: Metadata = {
  title: "Sarj Wiki",
  description: "Internal knowledge base for Sarj",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,

  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6efe7" },
    { media: "(prefers-color-scheme: dark)", color: "#161618" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={`${nunito.variable} ${GeistMono.variable}`}>
      <head>
        <link rel="preconnect" href="https://docs.google.com" />
        <link rel="dns-prefetch" href="https://docs.google.com" />
        <link rel="preconnect" href={API_URL} crossOrigin="use-credentials" />
        <link rel="dns-prefetch" href={API_URL} />

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
            {children}
            <GlobalLoadingBar />
          </QueryProvider>
          <Toaster position="bottom-right" />
        </ThemeProvider>
      </body>
    </html>
  );
}
