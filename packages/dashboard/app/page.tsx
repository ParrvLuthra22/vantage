export default function Home() {
  return (
    <div className="min-h-screen">
      <nav className="border-b border-border">
        <div className="mx-auto max-w-7xl px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-primary" />
            <span className="font-semibold tracking-tight">Vantage</span>
          </div>
          <div className="text-xs text-muted-foreground">v0.2.0</div>
        </div>
      </nav>
      <main className="mx-auto max-w-7xl px-6 py-12">
        <h1 className="text-2xl font-semibold tracking-tight">Traces</h1>
        <p className="text-sm text-muted-foreground mt-1">Coming in P15.</p>
      </main>
    </div>
  );
}
