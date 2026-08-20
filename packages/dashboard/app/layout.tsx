import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Vantage",
  description: "Self-hosted evaluation and observability for LLM agents",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // `dark` is hardcoded on <html>: there is no light mode for the MVP. The
  // product identity is a dark console, and shipping one theme means one set
  // of contrast decisions to get right instead of two.
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable} dark`}>
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
