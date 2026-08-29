import type {
  CreateRelationshipInput,
  PaperRelationship,
} from '@/types'

export function createRelationship(
  input: CreateRelationshipInput,
): PaperRelationship {
  return {
    id: crypto.randomUUID(),
    ...input,
  }
}
