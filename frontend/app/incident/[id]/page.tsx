import { Suspense } from "react";
import { ReportView } from "@/components/ReportView";

export default function IncidentReportPage({ params }: PageProps<"/incident/[id]">) {
  return (
    <Suspense fallback={<div className="p-10 text-slate-400">Loading report...</div>}>
      {params.then(({ id }) => (
        <ReportView id={id} />
      ))}
    </Suspense>
  );
}
