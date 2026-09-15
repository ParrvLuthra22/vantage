import Link from "next/link";

export default function NotFound() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-2xl font-semibold">Eval run not found</h1>
        <Link
          href="/evals"
          className="text-primary hover:underline mt-2 inline-block"
        >
          Back to eval runs
        </Link>
      </div>
    </div>
  );
}
