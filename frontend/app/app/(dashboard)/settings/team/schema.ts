import { z } from 'zod'

export const inviteTeamMemberSchema = z.object({
  email: z
    .string()
    .min(1, 'Email is required')
    .email('Enter a valid email address'),
})

export type InviteTeamMemberFormValues = z.infer<typeof inviteTeamMemberSchema>
