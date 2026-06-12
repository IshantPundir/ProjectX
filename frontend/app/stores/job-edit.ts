import { create } from 'zustand'

import type {
  SignalItem,
  SignalSnapshot,
  SignalType,
  SignalPriority,
  SignalStage,
} from '@/lib/api/jobs'

type DraftSignals = {
  signals: SignalItem[]
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
  addChip: (value: string, type: SignalType, stage: SignalStage, priority: SignalPriority) => void
  removeChip: (index: number) => void
  updateSignal: (index: number, updates: Partial<SignalItem>) => void
  markClean: () => void
}

export type { DraftSignals }

export const useJobEditStore = create<JobEditState>()((set) => ({
  isEditing: false,
  draft: null,
  isDirty: false,

  startEditing: (snapshot: SignalSnapshot) =>
    set({
      isEditing: true,
      isDirty: false,
      draft: {
        signals: snapshot.signals.map((s) => ({ ...s })),
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

  addChip: (value: string, type: SignalType, stage: SignalStage, priority: SignalPriority) =>
    set((state) => {
      if (!state.draft) return state
      const newItem: SignalItem = {
        value,
        type,
        priority,
        weight: 1,
        knockout: false,
        purpose: 'skill',
        stage,
        evaluation_method: 'verbal_response',
        evaluation_hint: null,
        source: 'recruiter',
        inference_basis: null,
      }
      return {
        draft: {
          ...state.draft,
          signals: [...state.draft.signals, newItem],
        },
        isDirty: true,
      }
    }),

  removeChip: (index: number) =>
    set((state) => {
      if (!state.draft) return state
      return {
        draft: {
          ...state.draft,
          signals: state.draft.signals.filter((_, i) => i !== index),
        },
        isDirty: true,
      }
    }),

  updateSignal: (index: number, updates: Partial<SignalItem>) =>
    set((state) => {
      if (!state.draft) return state
      const signals = state.draft.signals.map((s, i) =>
        i === index ? { ...s, ...updates } : s,
      )
      return {
        draft: { ...state.draft, signals },
        isDirty: true,
      }
    }),

  markClean: () => set({ isDirty: false }),
}))
