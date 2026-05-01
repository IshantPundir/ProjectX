import { WizardShell } from './WizardShell'

export default async function InterviewPage({
  params,
}: {
  params: Promise<{ token: string }>
}) {
  const { token } = await params
  return <WizardShell token={token} />
}
