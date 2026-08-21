export function EmptyState({ project }: { project: string }) {
  return (
    <div className="rounded-md border border-dashed border-border p-12 text-center">
      <p className="text-sm text-muted-foreground">
        No traces found for project <span className="font-mono">{project}</span>.
      </p>
      <p className="text-xs text-muted-foreground mt-2">
        Run an instrumented agent to see traces appear here.
      </p>
    </div>
  );
}
