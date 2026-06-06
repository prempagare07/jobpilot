import type { Metadata } from "next";

import { Sidebar } from "@/components/Sidebar";

import "./globals.css";

export const metadata: Metadata = {
  title: "JobPilot",
  description: "Local-first job application automation dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-background text-foreground">
        <div className="min-h-screen lg:grid lg:grid-cols-[240px_minmax(0,1fr)]">
          <Sidebar />
          <main className="min-w-0 bg-white px-4 py-5 sm:px-6 lg:px-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
