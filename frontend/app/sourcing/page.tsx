import { PageContainer } from "@/components/page-container";
import { EmptyState } from "@/components/states";

export default function Page() {
  return (
    <PageContainer title="Sourcing" description="Outbound search, contact resolution and reachout.">
      <EmptyState
        title="Not built yet"
        description="This screen will turn a pasted job description into an editable people-search query, run it, resolve contacts to dialable numbers with the resolver named on each record, and gate dispatch behind an explicit consent step."
      />
    </PageContainer>
  );
}
