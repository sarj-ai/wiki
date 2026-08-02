import "./globals.css";

import { GeistMono } from "geist/font/mono";
import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";

import { GlobalLoadingBar } from "@/components/loading-bar";
import { QueryProvider } from "@/components/query-provider";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { API_URL } from "@/lib/env";

const GOOGLE_DOC_ORIGINS = [
  "https://accounts.google.com",
  "https://apis.google.com",
  "https://clients6.google.com",
  "https://ssl.gstatic.com",
  "https://fonts.gstatic.com",
  "https://lh3.googleusercontent.com",
  "https://lh4.googleusercontent.com",
  "https://lh5.googleusercontent.com",
  "https://lh6.googleusercontent.com",
];

const nunito = localFont({
  display: "swap",
  src: [
    { path: "../../public/fonts/Nunito-Regular.ttf", style: "normal", weight: "400" },
    { path: "../../public/fonts/Nunito-Medium.ttf", style: "normal", weight: "500" },
    { path: "../../public/fonts/Nunito-SemiBold.ttf", style: "normal", weight: "600" },
    { path: "../../public/fonts/Nunito-Bold.ttf", style: "normal", weight: "700" },
  ],
  variable: "--font-nunito",
});

export const metadata: Metadata = {
  description: "Internal knowledge base for Sarj",
  metadataBase: new URL("https://wiki.sarj.ai"),
  openGraph: {
    description: "Internal knowledge base for Sarj",
    images: ["/icon.png"],
    siteName: "Sarj Wiki",
    title: "Sarj Wiki",
    type: "website",
  },
  robots: { follow: false, index: false },
  title: { default: "Sarj Wiki", template: "%s · Sarj Wiki" },
  twitter: {
    card: "summary",
    description: "Internal knowledge base for Sarj",
    images: ["/icon.png"],
    title: "Sarj Wiki",
  },
};

export const viewport: Viewport = {
  initialScale: 1,
  themeColor: [
    { color: "#f6efe7", media: "(prefers-color-scheme: light)" },
    { color: "#161618", media: "(prefers-color-scheme: dark)" },
  ],

  width: "device-width",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html className={`${nunito.variable} ${GeistMono.variable}`} lang="en" suppressHydrationWarning>
      <head>
        <link href="https://docs.google.com" rel="preconnect" />
        <link href="https://docs.google.com" rel="dns-prefetch" />
        <link crossOrigin="use-credentials" href={API_URL} rel="preconnect" />
        <link href={API_URL} rel="dns-prefetch" />

        {GOOGLE_DOC_ORIGINS.map((origin) => (
          <link href={origin} key={origin} rel="preconnect" />
        ))}

        {/* eslint-disable react/no-danger -- The SWC name helper must exist before hydration. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              'window.__name=function(t,n){try{Object.defineProperty(t,"name",{value:n,configurable:true})}catch(e){}return t;}',
          }}
        />
        {/* eslint-enable react/no-danger -- The exception is limited to the bootstrap script. */}
      </head>
      <body className="antialiased">
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          disableTransitionOnChange
          enableSystem
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
