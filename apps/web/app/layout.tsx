import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "ChaseLess — Recovery Command Center",
  description: "Adaptive, policy-bounded recurring revenue recovery",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/" className="brand" aria-label="ChaseLess home">
            <span className="brandMark">C</span>
            <span>CHASELESS</span>
          </Link>
          <nav aria-label="Primary navigation">
            <Link className="navActive" href="/">Recoveries</Link>
            <Link href="/imports">Import</Link>
            <Link href="/review">Approvals</Link>
            <Link href="/evidence">Proof</Link>
          </nav>
          <div className="mode"><span /> Razorpay Test Mode</div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
