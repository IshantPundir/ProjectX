import { create } from 'zustand'

import type { SignalItem, SignalSnapshot } from '@/lib/api/jobs'

/** The 4 list sections that support chip CRUD */
type ChipSection = 'required_skills' | 'preferred_skills' | 'must_haves' | 'good_to_haves'

type DraftSignals = {
  required_skills: SignalItem[]
  preferred_skills: SignalItem[]
  must_haves: SignalItem[]
  good_to_haves: SignalItem[]
  min_experience_years: number
  seniority_level: 'junior' | 'mid' | 'senior' | 'lead' | 'principal'
  role_summary: string
}

type JobEditState = {
  isEditing: boolean
  draft: DraftSignals | null
  isDirty: boolean
  startEditing: (snapshot: SignalSnapshot) => void
  stopEditing: () => void
  updateDraft: (updates: Partial<DraftSignals>) => void
  addChip: (section: ChipSection, value: string) => void
  removeChip: (section: ChipSection, index: number) => void
  markClean: () => void
}

export type { ChipSection, DraftSignals }

export const useJobEditStore = create<JobEditState>()((set) => ({
  isEditing: false,
  draft: null,
  isDirty: false,

  startEditing: (snapshot: SignalSnapshot) =>
    set({
      isEditing: true,
      isDirty: false,
      draft: {
        required_skills: snapshot.required_skills.map((s) => ({ ...s })),
        preferred_skills: snapshot.preferred_skills.map((s) => ({ ...s })),
        must_haves: snapshot.must_haves.map((s) => ({ ...s })),
        good_to_haves: snapshot.good_to_haves.map((s) => ({ ...s })),
        min_experience_years: snapshot.min_experience_years,
        seniority_level: snapshot.seniority_level,
        role_summary: snapshot.role_summary,
      },
    }),

  stopEditing: () =>
    set({
      isEditing: false,
      draft: null,
      isDirty: false,
    }),

  updateDraft: (updates: Partial<DraftSignals>) =>
    set((state) => {
      if (!state.draft) return state
      return {
        draft: { ...state.draft, ...updates },
        isDirty: true,
      }
    }),

  addChip: (section: ChipSection, value: string) =>
    set((state) => {
      if (!state.draft) return state
      const newItem: SignalItem = {
        value,
        source: 'recruiter',
        inference_basis: null,
      }
      return {
        draft: {
          ...state.draft,
          [section]: [...state.draft[section], newItem],
        },
        isDirty: true,
      }
    }),

  removeChip: (section: ChipSection, index: number) =>
    set((state) => {
      if (!state.draft) return state
      return {
        draft: {
          ...state.draft,
          [section]: state.draft[section].filter((_, i) => i !== index),
        },
        isDirty: true,
      }
    }),

  markClean: () => set({ isDirty: false }),
}))
