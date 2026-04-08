'use client'

import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

// These enums MUST match backend/nexus/tests/fixtures/company_profile_enums.json
// A backend unit test enforces parity (test_company_profile_schema.py::
// test_enum_parity_with_frontend_fixture). Drift here means backend rejects
// values the frontend allows. If you add/remove/rename a value here, also
// update the fixture AND the Python Literal in
// backend/nexus/app/modules/org_units/company_profile.py.
export const INDUSTRY_OPTIONS = [
  { value: 'fintech_financial_services', label: 'Fintech / Financial Services' },
  { value: 'healthcare_medtech', label: 'Healthcare / Medtech' },
  { value: 'ecommerce_retail', label: 'E-commerce / Retail' },
  { value: 'ai_ml_products', label: 'AI / ML Products' },
  { value: 'saas_enterprise_software', label: 'SaaS / Enterprise Software' },
  { value: 'developer_tools_infrastructure', label: 'Developer Tools / Infrastructure' },
  { value: 'agency_consulting_staffing', label: 'Agency / Consulting / Staffing' },
  { value: 'media_content', label: 'Media / Content' },
  { value: 'logistics_supply_chain', label: 'Logistics / Supply Chain' },
  { value: 'other', label: 'Other' },
] as const

export const COMPANY_STAGE_OPTIONS = [
  { value: 'pre_seed_seed', label: 'Pre-seed / Seed (≤20 people)' },
  { value: 'series_a_b', label: 'Series A–B (20–200 people)' },
  { value: 'series_c_plus', label: 'Series C+ (200–1000 people)' },
  { value: 'large_enterprise', label: 'Large Enterprise (1000+ people)' },
] as const

const INDUSTRY_VALUES = INDUSTRY_OPTIONS.map((o) => o.value) as [string, ...string[]]
const COMPANY_STAGE_VALUES = COMPANY_STAGE_OPTIONS.map((o) => o.value) as [string, ...string[]]

export const companyProfileSchema = z.object({
  about: z
    .string()
    .min(30, 'Describe what you build in at least a sentence (30+ characters)')
    .max(500, 'Keep it concise — 500 characters max'),
  industry: z.enum(INDUSTRY_VALUES),
  company_stage: z.enum(COMPANY_STAGE_VALUES),
  hiring_bar: z
    .string()
    .min(20, 'Describe what a strong hire looks like (20+ characters)')
    .max(280, 'Twitter-length — 280 characters max'),
})

export type CompanyProfile = z.infer<typeof companyProfileSchema>

type Props = {
  initialValue?: Partial<CompanyProfile>
  onSubmit: (value: CompanyProfile) => Promise<void>
  submitLabel?: string
}

export function CompanyProfileForm({
  initialValue,
  onSubmit,
  submitLabel = 'Save Company Profile',
}: Props) {
  const form = useForm<CompanyProfile>({
    resolver: zodResolver(companyProfileSchema),
    defaultValues: {
      about: initialValue?.about ?? '',
      industry: (initialValue?.industry as CompanyProfile['industry']) ?? undefined,
      company_stage:
        (initialValue?.company_stage as CompanyProfile['company_stage']) ?? undefined,
      hiring_bar: initialValue?.hiring_bar ?? '',
    },
    mode: 'onChange',
  })

  const aboutValue = form.watch('about') || ''
  const hiringBarValue = form.watch('hiring_bar') || ''

  return (
    <form
      onSubmit={form.handleSubmit(onSubmit)}
      className="space-y-6 max-w-2xl"
    >
      <div>
        <div className="flex items-baseline justify-between">
          <Label htmlFor="about" className="text-sm font-semibold">
            What does your company actually build or do?
          </Label>
          <span className="text-xs text-zinc-400">{aboutValue.length} / 500</span>
        </div>
        <p className="text-xs text-zinc-500 mt-1 mb-2">
          Be specific — what problems, at what scale, for whom?{' '}
          <em>Not your mission statement.</em>
        </p>
        <Textarea id="about" {...form.register('about')} rows={4} />
        {form.formState.errors.about && (
          <p className="text-xs text-red-500 mt-1">
            {form.formState.errors.about.message}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <Label htmlFor="industry" className="text-sm font-semibold">
            Industry
          </Label>
          {/* Base UI Select.Root uses onValueChange((value, eventDetails) => void) */}
          <Select
            onValueChange={(v) =>
              form.setValue('industry', v as CompanyProfile['industry'], {
                shouldValidate: true,
              })
            }
            defaultValue={form.getValues('industry')}
          >
            <SelectTrigger id="industry" className="mt-2 w-full">
              <SelectValue placeholder="Select industry" />
            </SelectTrigger>
            <SelectContent>
              {INDUSTRY_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {form.formState.errors.industry && (
            <p className="text-xs text-red-500 mt-1">
              {form.formState.errors.industry.message}
            </p>
          )}
        </div>

        <div>
          <Label htmlFor="company_stage" className="text-sm font-semibold">
            Company stage
          </Label>
          <Select
            onValueChange={(v) =>
              form.setValue('company_stage', v as CompanyProfile['company_stage'], {
                shouldValidate: true,
              })
            }
            defaultValue={form.getValues('company_stage')}
          >
            <SelectTrigger id="company_stage" className="mt-2 w-full">
              <SelectValue placeholder="Select stage" />
            </SelectTrigger>
            <SelectContent>
              {COMPANY_STAGE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {form.formState.errors.company_stage && (
            <p className="text-xs text-red-500 mt-1">
              {form.formState.errors.company_stage.message}
            </p>
          )}
        </div>
      </div>

      <div>
        <div className="flex items-baseline justify-between">
          <Label htmlFor="hiring_bar" className="text-sm font-semibold">
            What does a strong hire look like here?
          </Label>
          <span className="text-xs text-zinc-400">
            {hiringBarValue.length} / 280
          </span>
        </div>
        <p className="text-xs text-zinc-500 mt-1 mb-2">
          What do you value that a generic JD wouldn&apos;t capture?
        </p>
        <Textarea id="hiring_bar" {...form.register('hiring_bar')} rows={3} />
        {form.formState.errors.hiring_bar && (
          <p className="text-xs text-red-500 mt-1">
            {form.formState.errors.hiring_bar.message}
          </p>
        )}
      </div>

      <Button
        type="submit"
        disabled={!form.formState.isValid || form.formState.isSubmitting}
      >
        {form.formState.isSubmitting ? 'Saving...' : submitLabel}
      </Button>
    </form>
  )
}
