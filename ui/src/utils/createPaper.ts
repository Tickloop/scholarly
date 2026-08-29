import type { CreatePaperInput, Paper } from '@/types'

export function createPaper(input: CreatePaperInput): Paper {
  return {
    id: crypto.randomUUID(),
    ...input,
  }
}
