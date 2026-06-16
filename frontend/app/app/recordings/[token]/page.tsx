'use client'

import { useParams } from 'next/navigation'

import { PublicRecordingsView } from '@/components/dashboard/reports/PublicRecordingsView'

export default function PublicRecordingsPage() {
  const params = useParams<{ token: string }>()
  return <PublicRecordingsView token={params.token} />
}
