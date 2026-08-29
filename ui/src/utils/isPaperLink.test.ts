import { describe, expect, it } from 'vitest'

import { isPaperLink, paperReferenceValidationError } from '@/utils/isPaperLink'

describe('paper reference contract', () => {
  it.each([
    'https://publisher.example/paper.pdf',
    '10.1000/bare-doi',
    'doi:10.1000/prefixed-doi',
    'arXiv:2005.11401v2',
    '2005.11401v3',
    'hep-th/9901001',
    'math.GT/0309136v1',
  ])('accepts %s', (reference) => {
    expect(isPaperLink(reference)).toBe(true)
    expect(paperReferenceValidationError(reference)).toBeUndefined()
  })

  it.each([
    'http://publisher.example/paper.pdf',
    'ftp://publisher.example/paper.pdf',
    'not a paper',
    'https://user:secret@publisher.example/paper.pdf',
    '10.1000/doi?query=not-identifier',
    '10.1000/doi#fragment',
  ])('rejects %s', (reference) => {
    expect(isPaperLink(reference)).toBe(false)
    expect(paperReferenceValidationError(reference)).toBeTruthy()
  })
})
