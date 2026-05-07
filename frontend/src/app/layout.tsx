import "./globals.css";
import { zhCN } from "@/lib/i18n";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <head>
        <title>LumenX Studio</title>
        <meta name="description" content={zhCN.meta.appDescription} />
      </head>
      <body className="font-sans bg-background text-foreground antialiased">
        {children}
      </body>
    </html>
  );
}
