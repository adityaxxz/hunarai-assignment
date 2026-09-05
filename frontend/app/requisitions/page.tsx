import { PageContainer } from "@/components/page-container";
import { EmptyState } from "@/components/states";

export default function Page() {
  return (
    <PageContainer title="Requisitions" description="Role definitions, screening criteria and the generated voice agent config.">
      <EmptyState
        title="Not built yet"
        description="This screen will list requisitions and let you create one: title, location, languages, shift and pay band, plus knockout questions and a weighted rubric. From those it generates a Hunar agent config for review before it is pushed."
      />
    </PageContainer>
  );
}
