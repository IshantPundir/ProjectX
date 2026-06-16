import { Skeleton } from '@/components/px'

export default function Loading() {
  return (
    <div className="mx-auto max-w-2xl p-10">
      <Skeleton className="h-64 w-full" />
    </div>
  )
}
