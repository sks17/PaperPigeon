/**
 * Shared chrome for the marketing site (landing + docs): the pigeon seal, the central two-page
 * nav, and the GitHub footer. Deliberately tiny and text-forward — the whole site is whitespace
 * and type. The graph app lives behind "Enter the graph" at /app and is untouched by this.
 */
import { NavLink, Link } from 'react-router-dom';

const REPO_URL = 'https://github.com/sks17/PaperPigeon';

/** The pigeon mark, framed like a postal seal (the favicon art sits on white, so the ring hides it). */
export function PigeonSeal({ size = 56, to = '/' as string | null }: { size?: number; to?: string | null }) {
  const seal = (
    <span
      className="inline-flex items-center justify-center rounded-full bg-white ring-1 ring-hairline shadow-[0_1px_0_rgba(28,27,25,0.04),0_8px_24px_-12px_rgba(28,27,25,0.25)]"
      style={{ width: size, height: size }}
    >
      <img
        src="/favicon.png"
        alt="Paper Pigeon"
        className="rounded-full"
        style={{ width: size - 10, height: size - 10 }}
      />
    </span>
  );
  return to ? (
    <Link to={to} aria-label="Paper Pigeon — home" className="transition-transform duration-300 hover:-translate-y-0.5">
      {seal}
    </Link>
  ) : (
    seal
  );
}

/** Central navigation: just Home and Docs, centered. The active page is inked; the other is muted. */
export function SiteNav() {
  const link = ({ isActive }: { isActive: boolean }) =>
    [
      'relative px-1 pb-1 text-[0.95rem] tracking-wide transition-colors duration-200',
      isActive ? 'text-ink' : 'text-ink-soft hover:text-ink',
      // hairline underline that fills in on the active page
      'after:absolute after:left-0 after:right-0 after:-bottom-px after:h-px after:bg-ink after:origin-center after:transition-transform after:duration-300',
      isActive ? 'after:scale-x-100' : 'after:scale-x-0',
    ].join(' ');

  return (
    <nav className="font-hanken flex items-center justify-center gap-10">
      <NavLink to="/" end className={link}>
        Home
      </NavLink>
      <NavLink to="/docs" className={link}>
        Docs
      </NavLink>
    </nav>
  );
}

function GithubMark({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" fill="currentColor" className={className}>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

/** GitHub button shown at the bottom of both pages. */
export function SiteFooter() {
  return (
    <footer className="font-hanken flex flex-col items-center gap-5 pb-16 pt-24">
      <a
        href={REPO_URL}
        target="_blank"
        rel="noreferrer"
        className="group inline-flex items-center gap-2.5 rounded-full border border-hairline bg-paper/60 px-5 py-2.5 text-[0.9rem] text-ink-soft backdrop-blur transition-all duration-300 hover:-translate-y-0.5 hover:border-ink/30 hover:text-ink hover:shadow-[0_10px_30px_-15px_rgba(28,27,25,0.4)]"
      >
        <GithubMark className="h-4 w-4 transition-transform duration-300 group-hover:scale-110" />
        <span>View source on GitHub</span>
      </a>
      <p className="text-[0.72rem] tracking-[0.18em] text-ink-faint uppercase">Paper Pigeon · Beta 0.5</p>
    </footer>
  );
}
