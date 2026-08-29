export const PAPER_REFERENCE_PATTERN = String.raw`^\s*(?:[Hh][Tt][Tt][Pp][Ss]://.+|(?:[Dd][Oo][Ii]:\s*)?10\.\d{4,9}/[^\s?#]+|(?:[Aa][Rr][Xx][Ii][Vv]:\s*)?(?:[A-Za-z][A-Za-z0-9.-]*/\d{7}|\d{4}\.\d{4,5})(?:[Vv]\d+)?)\s*$`

export function paperReferenceValidationError(value: string) {
  const cleaned = value.trim()
  if (/^http:\/\//i.test(cleaned)) {
    return 'Remote paper links must use HTTPS.'
  }
  if (/^https:\/\//i.test(cleaned)) {
    try {
      const parsed = new URL(cleaned)
      if (parsed.protocol === 'https:' && parsed.hostname && !parsed.username && !parsed.password) {
        return undefined
      }
    } catch {
      // The common contract error below covers malformed HTTPS references.
    }
    return 'Paper HTTPS URLs must include a public hostname and no credentials.'
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(cleaned) && !/^(?:doi|arxiv):/i.test(cleaned)) {
    return 'Paper references must be an HTTPS URL, DOI, or arXiv ID.'
  }

  const doi = cleaned.replace(/^doi:/i, '').trim()
  if (/^10\.\d{4,9}\/[^\s?#]+$/i.test(doi)) return undefined

  const arxiv = cleaned.replace(/^arxiv:/i, '').trim()
  if (/^(?:[a-z][a-z0-9.-]*\/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?$/i.test(arxiv)) {
    return undefined
  }
  return 'Paper references must be an HTTPS URL, DOI, or arXiv ID.'
}

export function isPaperLink(value: string) {
  return paperReferenceValidationError(value) === undefined
}
