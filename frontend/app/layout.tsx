import type { Metadata } from "next";
import { Nav } from "@/components/nav";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "RecoverAI — Revenue Recovery Intelligence",
  description:
    "AI-powered recovery of failed payments, abandoned checkouts and failed subscriptions.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/*
          Apply the stored theme before first paint so a dark-mode user never sees a
          white flash. Wrapped in try/catch because storage throws in some contexts.
        */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem('recoverai-theme');if(t==='dark'||t==='light'){document.documentElement.dataset.theme=t;}}catch(e){}`,
          }}
        />
      </head>
      <body className="min-h-screen bg-page text-ink antialiased">
        <Providers>
          <Nav />
          <main className="mx-auto max-w-[1400px] px-5 py-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
