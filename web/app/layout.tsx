import type { Metadata } from "next";
import { Fraunces, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const serif = Fraunces({ subsets: ["latin"], variable: "--font-fraunces", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "ทองคำ — จังหวะขาย",
  description: "วิเคราะห์เทคนิคและปัจจัยพื้นฐานเพื่อหาจังหวะขายทองคำ (บาท) ทดสอบย้อนหลัง 20 ปี",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="th">
      <body className={`${serif.variable} ${mono.variable}`}>{children}</body>
    </html>
  );
}
