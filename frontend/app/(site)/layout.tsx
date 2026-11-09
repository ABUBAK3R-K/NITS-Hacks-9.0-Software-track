import type { Metadata } from 'next';
import { InlineScript } from '@/components/doc/InlineScript';
import './doc.css';

export const metadata: Metadata = {
  title: 'J.A.R.V.I.S.: many alerts, one cause, proof attached',
  description:
    'Engineering case study: a deterministic incident root-cause engine. Our AI is not allowed to decide anything. It can only explain what the math proved.',
};

// The theme lives on this wrapper, not on <html>: the dashboard's global dark styling can never be restyled by it.
// The script runs while the HTML is parsed (the wrapper's opening tag is already in the DOM), before first paint.
const NO_FLASH = `try{var t=localStorage.getItem('jarvis-doc-theme');if(t==='light'||t==='dark')document.getElementById('cs-root').dataset.theme=t}catch(e){}`;

export default function CaseStudyLayout({ children }: { children: React.ReactNode }) {
  return (
    <div id="cs-root" className="cs" suppressHydrationWarning>
      <InlineScript html={NO_FLASH} />
      {children}
    </div>
  );
}
