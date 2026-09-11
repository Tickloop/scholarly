import { MarkerType, type XYPosition } from '@xyflow/react'

import type { Paper, PaperRelationship } from '@/types/research'

export const PAPER_NODE_WIDTH = 320
export const DEFAULT_NODE_POSITION: XYPosition = { x: 0, y: 0 }
export const RELATIONSHIP_EDGE_MARKER = MarkerType.Arrow
export const DEFAULT_RELATIONSHIP_LABEL = 'Related'

export const PAPER_DATE_FORMATTER = new Intl.DateTimeFormat('en', {
  month: 'short',
  timeZone: 'UTC',
})

export const PAPERS: Paper[] = [
  {
    id: 'attention-is-all-you-need',
    title: 'Attention Is All You Need',
    authors: ['Ashish Vaswani', 'Noam Shazeer', 'Niki Parmar', 'Jakob Uszkoreit'],
    year: 2017,
    month: 12,
    summary:
      'Introduces the Transformer, replacing recurrence with self-attention for sequence modeling.',
    link: 'https://arxiv.org/abs/1706.03762',
    x: 0,
    y: 0,
  },
  {
    id: 'bert',
    title: 'BERT: Pre-training of Deep Bidirectional Transformers',
    authors: ['Jacob Devlin', 'Ming-Wei Chang', 'Kenton Lee', 'Kristina Toutanova'],
    year: 2019,
    month: 6,
    summary:
      'Pre-trains bidirectional Transformer representations using masked language modeling.',
    link: 'https://arxiv.org/abs/1810.04805',
    x: 560,
    y: 0,
  },
]

export const RELATIONSHIPS: PaperRelationship[] = [
  {
    id: 'transformer-to-bert',
    source: 'attention-is-all-you-need',
    target: 'bert',
    label: 'provides the encoder architecture for',
  },
]
