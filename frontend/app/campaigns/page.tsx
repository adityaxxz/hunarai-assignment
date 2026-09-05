import { PageContainer } from "@/components/page-container";
import { EmptyState } from "@/components/states";

export default function Page() {
  return (
    <PageContainer title="Campaigns" description="Call batches, live funnel and per-candidate results.">
      <EmptyState
        title="Not built yet"
        description="This screen will show campaign dispatch and the live funnel from queued through dialled, connected, engaged, screened and qualified, including retry state and the next scheduled retry per call."
      />
    </PageContainer>
  );
}
