import type { Paper, PaperRelationship } from '@/types'
import { attentionReview, bertReview } from '@/_mock/paperReviews'

export const papers: Paper[] = [
  {
    id: '01a04a27-0744-74ca-a574-8d0bc4f6f1da',
    title: 'Attention Is All You Need',
    authors: [
      'Ashish Vaswani',
      'Noam Shazeer',
      'Niki Parmar',
      'Jakob Uszkoreit',
    ],
    year: 2017,
    month: 12,
    summary:
      'Introduces the Transformer, replacing recurrence with self-attention for sequence modeling.',
    link: 'https://arxiv.org/abs/1706.03762',
    review: attentionReview,
  },
  {
    id: '01a04a27-0745-7c24-a120-182c6a6507be',
    title: 'BERT: Pre-training of Deep Bidirectional Transformers',
    authors: ['Jacob Devlin', 'Ming-Wei Chang', 'Kenton Lee', 'Kristina Toutanova'],
    year: 2019,
    month: 6,
    summary:
      'Pre-trains bidirectional Transformer representations using masked language modeling.',
    link: 'https://arxiv.org/abs/1810.04805',
    review: bertReview,
  },
]

export const relationships: PaperRelationship[] = [
  {
    id: '01a04a27-0746-7ef0-9f8d-b94fd4f032cc',
    source: '01a04a27-0744-74ca-a574-8d0bc4f6f1da',
    target: '01a04a27-0745-7c24-a120-182c6a6507be',
    type: 'extends',
    label: 'provides the encoder architecture for',
    explanation:
      'BERT adapts the Transformer encoder introduced by Vaswani et al. and trains it bidirectionally on unlabeled text.',
  },
]
