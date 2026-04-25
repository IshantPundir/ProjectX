// Signals use ordinal weight 1 | 2 | 3. Map to a visual 0–1 scale.
export function weightToConfidence(weight: 1 | 2 | 3): number {
  return ({ 1: 0.52, 2: 0.74, 3: 0.92 } as const)[weight]
}
