import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "TW Automation", template: "%s · TW Automation" },
  description: "OS 배포 · FOTA · 문서 · 라이선스",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
