import { PageContainer } from "@/components/page-container";
import { EmptyState } from "@/components/states";

export default function Page() {
  return (
    <PageContainer title="Attendance" description="Design note for tracking attendance without smartphones.">
      <EmptyState
        title="Not built yet"
        description="This will render docs/ATTENDANCE_DESIGN.md, the written answer to tracking daily attendance for 1000 people across 100 locations where workers have no smartphones."
      />
    </PageContainer>
  );
}
