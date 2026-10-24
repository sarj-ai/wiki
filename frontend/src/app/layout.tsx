import "./globals.css";

import { GeistMono } from "geist/font/mono";
import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";

import { ThemeProvider } from "@/components/theme-provider";
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
  preload: false,
  src: [
    { path: "../../public/fonts/Nunito-Regular.woff2", style: "normal", weight: "400" },
    { path: "../../public/fonts/Nunito-Medium.woff2", style: "normal", weight: "500" },
    { path: "../../public/fonts/Nunito-SemiBold.woff2", style: "normal", weight: "600" },
    { path: "../../public/fonts/Nunito-Bold.woff2", style: "normal", weight: "700" },
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
          <link href={origin} key={origin} rel="dns-prefetch" />
        ))}

        <script
          // eslint-disable-next-line react/no-danger -- This fixed SWC name helper must run before hydration.
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
          disableTransitionOnChange
          enableSystem
        >
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
