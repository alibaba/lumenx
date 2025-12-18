import "./globals.css";
import EnvConfigChecker from "@/components/EnvConfigChecker";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <title>AI-Manga Studio</title>
        <meta name="description" content="AI-Native Motion Comic Creation Platform" />
      </head>
      <body className="font-sans bg-background text-foreground antialiased">
        <EnvConfigChecker />
        {children}
      </body>
    </html>
  );
}
