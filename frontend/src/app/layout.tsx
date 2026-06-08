import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: 'ClaimDrift — Catch Scientific Claim Drifts Before They Become Citations',
  description:
    'When preprints become peer-reviewed papers, their core claims often shift. ClaimDrift uses a Vertex AI multi-agent pipeline to detect semantic drift the moment a preprint is published and notify every researcher whose work depends on the original findings.',
  openGraph: {
    title: 'ClaimDrift',
    description: 'Multi-agent scientific integrity system.',
    type: 'website',
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} font-sans h-full antialiased`}
    >
      <body className="min-h-full m-0 p-0 selection:bg-black selection:text-white">
        {children}
      </body>
    </html>
  );
}
