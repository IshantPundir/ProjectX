export default async function InterviewPage({
  params,
}: {
  params: Promise<{ token: string }>
}) {
  // Token is read here so the server segment is correctly hydrated; the live
  // wizard (Task 3C.2.3) will replace this placeholder.
  await params
  return (
    <div>
      <h1 className="text-2xl font-semibold">Interview pre-check</h1>
      <p className="mt-4 text-zinc-600">Loading session… (wired in next task)</p>
    </div>
  )
}
