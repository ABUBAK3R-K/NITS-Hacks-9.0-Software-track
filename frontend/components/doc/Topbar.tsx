import { FileText, MonitorPlay } from 'lucide-react';
import { GitHubMark } from './GitHubMark';
import { Logo } from './Logo';
import { ThemeToggle } from './ThemeToggle';

const ICON = { size: 16, strokeWidth: 1.75, 'aria-hidden': true } as const;
export const REPO_URL = 'https://github.com/ABUBAK3R-K/NITS-Hacks-9.0-Software-track';

export function Topbar() {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <a className="brand" href="#top" aria-label="J.A.R.V.I.S., case study, back to top">
          <Logo size={22} />
          <span className="brand-name">J.A.R.V.I.S.</span>
          <span className="brand-sep" aria-hidden="true">
            /
          </span>
          <span className="brand-crumb">Case study</span>
        </a>
        <nav className="topnav" aria-label="Main">
          <a className="nav-link" href="/dashboard" title="Open the live incident dashboard">
            <MonitorPlay {...ICON} />
            <span className="nav-label">Live demo</span>
          </a>
          <a className="nav-link" href="#report" title="A grounded incident report, with its citations">
            <FileText {...ICON} />
            <span className="nav-label">Report example</span>
          </a>
          <span className="nav-divider" aria-hidden="true" />
          <a className="icon-btn" href={REPO_URL} aria-label="Repository on GitHub" title="Repository on GitHub">
            <GitHubMark />
          </a>
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}
