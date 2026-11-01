import type { ReactNode } from "react";
import Navbar from "./Navbar";

interface LayoutProps {
  children: ReactNode;
}

/**
 * Application shell — full-width navbar + page content + footer.
 * Content area uses max-w-6xl to give the richer layout room to breathe.
 */
export default function Layout({ children }: LayoutProps) {
  return (
    <div className="min-h-screen flex flex-col" style={{ backgroundColor: "var(--color-bg)" }}>
      <Navbar />

      <main
        id="main-content"
        className="flex-1 w-full"
      >
        {children}
      </main>

      <footer
        className="py-5 px-5 sm:px-8"
        style={{ borderTop: "1px solid var(--color-border)" }}
      >
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-2">
          <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
            VoyageSync · Phase 0 · Planning engine not yet active
          </p>
          <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
            No real bookings, itineraries or travel data are generated.
          </p>
        </div>
      </footer>
    </div>
  );
}
