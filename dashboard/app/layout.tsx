import { ClerkProvider, SignedIn, SignedOut, UserButton } from "@clerk/nextjs";
import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Jaxfor Invoice Dashboard",
  description: "Processed invoices, review queue, and summary stats",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider>
      <html lang="en">
        <body>
          <header className="topbar">
            <Link href="/dashboard" className="brand">
              Jaxfor Invoice Dashboard
            </Link>
            <nav className="nav">
              <SignedIn>
                <Link href="/dashboard">Processed</Link>
                <Link href="/dashboard/review">Review Queue</Link>
                <UserButton afterSignOutUrl="/sign-in" />
              </SignedIn>
              <SignedOut>
                <Link href="/sign-in">Sign in</Link>
              </SignedOut>
            </nav>
          </header>
          <main className="main">{children}</main>
        </body>
      </html>
    </ClerkProvider>
  );
}
