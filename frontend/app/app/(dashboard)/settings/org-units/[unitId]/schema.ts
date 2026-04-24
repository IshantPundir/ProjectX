/**
 * Shared Zod schemas for the org-unit detail page's subcomponent forms.
 * Each subcomponent (CompanyProfileDetail, DivisionDetail, RegionDetail,
 * TeamDetail, MembersSection) uses the schema below or extends it.
 */
import { z } from 'zod'

export const unitNameSchema = z.object({
  name: z
    .string()
    .min(1, 'Name is required')
    .max(100, 'Keep names under 100 characters'),
})
export type UnitNameFormValues = z.infer<typeof unitNameSchema>
