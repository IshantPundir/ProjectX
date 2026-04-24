import { z } from 'zod'

export const createOrgUnitSchema = z.object({
  name: z
    .string()
    .min(1, 'Unit name is required')
    .max(100, 'Keep names under 100 characters'),
  unit_type: z.enum(['division', 'client_account', 'region', 'team']),
  parent_unit_id: z.string(),
})

export type CreateOrgUnitFormValues = z.infer<typeof createOrgUnitSchema>
