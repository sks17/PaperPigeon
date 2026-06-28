/**
 * Landing — the front door. Uber-minimal, uber-spacious: a pigeon seal, the wordmark, one line of
 * intent, and a single button into the graph app. Everything else is whitespace.
 */
import { Link } from 'react-router-dom';
import { SiteNav, SiteFooter } from '../components/site/SiteChrome';

export default function Landing() {
  return (
    <div className="site-page font-hanken">
      <div className="mx-auto flex min-h-full max-w-3xl flex-col px-6">
        <header className="site-rise pt-9" style={{ animationDelay: '0ms' }}>
          <SiteNav />
        </header>

        <main className="flex flex-1 flex-col items-center justify-center py-28 text-center">
          <h1
            className="site-rise font-instrument font-normal tracking-[-0.01em] text-ink"
            style={{ animationDelay: '180ms', fontSize: 'clamp(3.75rem, 12vw, 8rem)', lineHeight: 0.92 }}
          >
            Paper&nbsp;Pigeon
          </h1>

          <p
            className="site-rise mt-6 font-instrument italic text-ink-soft"
            style={{ animationDelay: '300ms', fontSize: 'clamp(1.4rem, 3.6vw, 2rem)' }}
          >
            Ideas take flight.
          </p>

          <p
            className="site-rise mt-7 max-w-xl text-[1.05rem] leading-relaxed text-ink-soft"
            style={{ animationDelay: '400ms' }}
          >
            An interactive 3&#8209;D map of a research ecosystem — the researchers, the labs, and the
            papers that quietly connect them. Point it at any institution and watch the network draw
            itself.
          </p>

          <div className="site-rise mt-12" style={{ animationDelay: '520ms' }}>
            <Link
              to="/app"
              className="group inline-flex items-center gap-2.5 rounded-full bg-ink px-8 py-4 text-[1rem] tracking-wide text-paper transition-all duration-300 hover:-translate-y-0.5 hover:shadow-[0_18px_40px_-18px_rgba(28,27,25,0.7)]"
            >
              Enter the graph
              <span className="transition-transform duration-300 group-hover:translate-x-1">&rarr;</span>
            </Link>
          </div>

          <p
            className="site-rise mt-6 text-[0.8rem] tracking-wide text-ink-faint"
            style={{ animationDelay: '620ms' }}
          >
            No sign-in · drag, zoom, search · or step inside in VR
          </p>
        </main>

        <SiteFooter />
      </div>
    </div>
  );
}
