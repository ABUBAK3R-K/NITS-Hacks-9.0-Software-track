import { LiveDataProvider } from "@/lib/live";

// Live data (API + SSE, or fixture replay) only for the dashboard and incident pages,
// so the case-study landing page at / never opens a stream to the backend.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <LiveDataProvider>{children}</LiveDataProvider>;
}
