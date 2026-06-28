/**
 * Docs — a clean, github.io-style reading column. Content lives in src/content/docs.ts and is
 * trivial to extend (add an entry); this file is just the typographic rendering.
 */
import React from 'react';
import { SiteNav, SiteFooter } from '../components/site/SiteChrome';
import { docEntries, type Block } from '../content/docs';

/** Minimal inline formatter: **bold** and `code`. No nesting — that's intentional, keeps it simple. */
function renderInline(text: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={i} className="font-semibold text-ink">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code
          key={i}
          className="rounded bg-ink/[0.05] px-1.5 py-0.5 font-mono text-[0.85em] text-ink"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

function BlockView({ block }: { block: Block }) {
  if (block.kind === 'h') {
    return (
      <h3 className="mt-12 mb-4 font-instrument text-[1.65rem] leading-snug text-ink">
        {block.text}
      </h3>
    );
  }
  if (block.kind === 'p') {
    return <p className="mt-4 text-[1.02rem] leading-[1.85] text-ink-soft">{renderInline(block.text)}</p>;
  }
  return (
    <ul className="mt-4 space-y-3">
      {block.items.map((item, i) => (
        <li key={i} className="relative pl-6 text-[1.02rem] leading-[1.8] text-ink-soft">
          <span className="absolute left-0 top-[0.78em] h-px w-3 bg-ink-faint" aria-hidden="true" />
          {renderInline(item)}
        </li>
      ))}
    </ul>
  );
}

export default function Docs() {
  return (
    <div className="site-page font-hanken">
      <div className="mx-auto max-w-2xl px-6">
        <header className="pt-9">
          <SiteNav />
        </header>

        <main className="site-rise pt-20" style={{ animationDelay: '60ms' }}>
          <p className="text-[0.72rem] uppercase tracking-[0.22em] text-ink-faint">Documentation</p>
          <h1 className="mt-3 font-instrument text-[3rem] leading-none tracking-[-0.01em] text-ink">
            Release notes
          </h1>
          <p className="mt-5 text-[1.05rem] leading-relaxed text-ink-soft">
            How the running system has drifted from the README, version by version. Newest first.
          </p>

          <div className="mt-14 h-px w-full bg-hairline" />

          {docEntries.map((entry) => (
            <article key={entry.id} className="pt-14">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-mono text-[0.8rem] tracking-wide text-ink-faint">{entry.version}</span>
                {entry.status && (
                  <span className="rounded-full bg-ink px-2.5 py-0.5 text-[0.68rem] uppercase tracking-[0.14em] text-paper">
                    {entry.status}
                  </span>
                )}
                <span className="ml-auto text-[0.8rem] text-ink-faint">{entry.date}</span>
              </div>

              <h2 className="mt-4 font-instrument text-[2.4rem] leading-tight tracking-[-0.01em] text-ink">
                {entry.title}
              </h2>
              <p className="mt-3 text-[1.1rem] italic leading-relaxed text-ink-soft font-instrument">
                {entry.lede}
              </p>

              <div className="mt-4">
                {entry.blocks.map((block, i) => (
                  <BlockView key={i} block={block} />
                ))}
              </div>
            </article>
          ))}
        </main>

        <SiteFooter />
      </div>
    </div>
  );
}
