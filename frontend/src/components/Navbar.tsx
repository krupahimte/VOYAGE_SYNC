import { MapPin, Settings, Compass } from "lucide-react";

const NAV_LINKS = [
  { label: "Trips", href: "#trips" },
  { label: "Planning", href: "#planning" },
] as const;

/**
 * Top navigation bar — wordmark, primary links, and minimal settings area.
 */
export default function Navbar() {
  return (
    <header
      className="sticky top-0 z-20"
      style={{
        backgroundColor: "var(--color-bg)",
        borderBottom: "1px solid var(--color-border)",
      }}
    >
      <div className="max-w-6xl mx-auto flex items-center justify-between px-5 sm:px-8 h-14">
        {/* Wordmark */}
        <a
          href="/"
          className="flex items-center gap-2 font-semibold tracking-tight text-[15px]"
          style={{ color: "var(--color-text-primary)" }}
          aria-label="VoyageSync home"
        >
          <div
            className="flex items-center justify-center w-7 h-7 rounded-md"
            style={{ backgroundColor: "var(--color-accent)" }}
            aria-hidden="true"
          >
            <MapPin size={14} strokeWidth={2.5} color="#fff" />
          </div>
          <span>VoyageSync</span>
        </a>

        {/* Centre nav */}
        <nav aria-label="Primary navigation" className="hidden sm:block">
          <ul className="flex items-center gap-1 list-none m-0 p-0">
            {NAV_LINKS.map((link) => (
              <li key={link.href}>
                <a
                  href={link.href}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors"
                  style={{ color: "var(--color-text-secondary)" }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.color = "var(--color-text-primary)")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.color = "var(--color-text-secondary)")
                  }
                >
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        {/* Right — settings area */}
        <div className="flex items-center gap-2">
          <button
            aria-label="Open settings"
            title="Settings (coming soon)"
            className="inline-flex items-center justify-center w-8 h-8 rounded-md transition-colors"
            style={{ color: "var(--color-text-muted)", background: "transparent", border: "none", cursor: "pointer" }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.backgroundColor = "var(--color-bg-alt)")
            }
            onMouseLeave={(e) =>
              (e.currentTarget.style.backgroundColor = "transparent")
            }
          >
            <Settings size={16} strokeWidth={1.75} aria-hidden="true" />
          </button>
          <div
            className="hidden sm:flex items-center gap-2 pl-3"
            style={{ borderLeft: "1px solid var(--color-border)" }}
          >
            <div
              className="w-7 h-7 rounded-full flex items-center justify-center"
              style={{ backgroundColor: "var(--color-accent-light)", border: "1px solid var(--color-border)" }}
              aria-label="Profile"
            >
              <Compass size={14} style={{ color: "var(--color-accent)" }} aria-hidden="true" />
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
