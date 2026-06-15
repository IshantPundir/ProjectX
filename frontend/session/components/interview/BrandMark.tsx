// components/interview/BrandMark.tsx
'use client'

import Image from 'next/image'

import { brand } from '@/lib/brand'
import { cn } from '@/lib/utils'

interface Props {
  variant?: 'mark' | 'wordmark'
  className?: string
  priority?: boolean
}

/**
 * BinQle.ai platform logo. `mark` is the square glyph (header chip); `wordmark`
 * is the full lockup. Alt text is always the product name for screen readers.
 */
export function BrandMark({ variant = 'mark', className, priority = true }: Props) {
  const asset = brand.logo[variant]
  return (
    <Image
      src={asset.src}
      alt={brand.name}
      width={asset.width}
      height={asset.height}
      priority={priority}
      className={cn(
        variant === 'mark' ? 'h-7 w-7 rounded-[7px]' : 'h-7 w-auto',
        'select-none',
        className,
      )}
    />
  )
}
