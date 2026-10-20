import "./globals.css";
import EnvConfigChecker from "@/components/EnvConfigChecker";
import { Providers } from "@/components/Providers";
import TauriDragBar from "@/components/layout/TauriDragBar";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh" className="atelier-dark" suppressHydrationWarning>
      <head>
        <title>LumenX Studio</title>
        <meta name="description" content="AI-Native Motion Comic Creation Platform" />
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var P=["atelier-dark","bridge-dark","brand-dark","atelier-light","brand-light"];var d=JSON.parse(localStorage.getItem("lumenx-settings")||"{}");var t=d.state&&d.state.theme;document.documentElement.className=P.indexOf(t)>=0?t:"atelier-dark";}catch(e){document.documentElement.className="atelier-dark";}})();`,
          }}
        />
        {/* Desktop app: compact font-size for embedded windows (Tauri / pywebview).
            Detection covers: Tauri protocol, Tauri global, pywebview global,
            and the production static/index.html served by backend (pywebview). */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){var p=window.location.protocol;var h=window.location.hostname;var isTauri=p==='tauri:'||window.__TAURI__||window.__TAURI_INTERNALS__||(p==='https:'&&h==='tauri.localhost');var isPywebview=!!window.pywebview||(p==='http:'&&(h==='127.0.0.1'||h==='localhost')&&window.location.pathname.indexOf('/static/')===0);if(isTauri||isPywebview){document.documentElement.style.fontSize='81.25%';}})();`,
          }}
        />
      </head>
      <body className="font-sans bg-background text-foreground antialiased">
        <Providers>
          <TauriDragBar />
          <EnvConfigChecker />
          {children}
        </Providers>
      </body>
    </html>
  );
}
