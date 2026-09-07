import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "DocuFlow",
  description: "Lectura y revisión de facturas escaneadas",
};

const NAV = [
  { href: "/", label: "Facturas" },
  { href: "/chat", label: "Preguntar" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen">
        {/* La barra oscura enmarca la superficie de trabajo clara, como el
            borde de una mesa de luz. */}
        <header className="bg-ink text-paper">
          <div className="mx-auto flex max-w-6xl items-center gap-8 px-6 py-3">
            <Link href="/" className="font-semibold tracking-tight">
              DocuFlow
            </Link>
            <nav className="flex gap-6 text-sm">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="text-paper/70 transition-colors hover:text-paper"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
