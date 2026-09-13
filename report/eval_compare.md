# Syllable Segmentation Evaluation Report

## Summary Metrics

| Segmenter | Boundary P | Boundary R | Boundary F1 | Syllable Acc | Word Acc | Total Words |
|-----------|------------|------------|-------------|--------------|----------|-------------|
| Rule-based | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1000 |
| CRF | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1000 |
| SentencePiece | 0.3291 | 1.0000 | 0.4953 | 0.0314 | 0.0020 | 1000 |

## Per-Syllable-Count Accuracy

| Segmenter | Syllable Count | Accuracy | Total | Correct |
|-----------|----------------|----------|-------|---------|
| Rule-based | 1 | 1.0000 | 69 | 69 |
| Rule-based | 2 | 1.0000 | 354 | 354 |
| Rule-based | 3 | 1.0000 | 290 | 290 |
| Rule-based | 4 | 1.0000 | 113 | 113 |
| Rule-based | 5 | 1.0000 | 121 | 121 |
| Rule-based | 6 | 1.0000 | 42 | 42 |
| Rule-based | 7 | 1.0000 | 8 | 8 |
| Rule-based | 8 | 1.0000 | 2 | 2 |
| Rule-based | 9 | 1.0000 | 1 | 1 |
| CRF | 1 | 1.0000 | 69 | 69 |
| CRF | 2 | 1.0000 | 354 | 354 |
| CRF | 3 | 1.0000 | 290 | 290 |
| CRF | 4 | 1.0000 | 113 | 113 |
| CRF | 5 | 1.0000 | 121 | 121 |
| CRF | 6 | 1.0000 | 42 | 42 |
| CRF | 7 | 1.0000 | 8 | 8 |
| CRF | 8 | 1.0000 | 2 | 2 |
| CRF | 9 | 1.0000 | 1 | 1 |
| SentencePiece | 1 | 0.0000 | 69 | 0 |
| SentencePiece | 2 | 0.0000 | 354 | 0 |
| SentencePiece | 3 | 0.0000 | 290 | 0 |
| SentencePiece | 4 | 0.0088 | 113 | 1 |
| SentencePiece | 5 | 0.0083 | 121 | 1 |
| SentencePiece | 6 | 0.0000 | 42 | 0 |
| SentencePiece | 7 | 0.0000 | 8 | 0 |
| SentencePiece | 8 | 0.0000 | 2 | 0 |
| SentencePiece | 9 | 0.0000 | 1 | 0 |

## Error Analysis

### Rule-based
- **Total words**: 1000
- **Correct syllable count**: 1000
- **Total errors**: 0
- **Over-segmentation**: 0
- **Under-segmentation**: 0
- **Boundary shift**: 0

#### By Word Frequency
| Frequency Bucket | Total | Correct | Errors | Accuracy |
|------------------|-------|---------|--------|----------|
| unseen (0) | 1000 | 1000 | 0 | 1.0000 |

#### Common Errors (Top 10)
| Word | Gold | Predicted | Error Type | Frequency |
|------|------|-----------|------------|-----------|

### CRF
- **Total words**: 1000
- **Correct syllable count**: 1000
- **Total errors**: 0
- **Over-segmentation**: 0
- **Under-segmentation**: 0
- **Boundary shift**: 0

#### By Word Frequency
| Frequency Bucket | Total | Correct | Errors | Accuracy |
|------------------|-------|---------|--------|----------|
| unseen (0) | 1000 | 1000 | 0 | 1.0000 |

#### Common Errors (Top 10)
| Word | Gold | Predicted | Error Type | Frequency |
|------|------|-----------|------------|-----------|

### SentencePiece
- **Total words**: 1000
- **Correct syllable count**: 2
- **Total errors**: 998
- **Over-segmentation**: 998
- **Under-segmentation**: 0
- **Boundary shift**: 0

#### By Word Frequency
| Frequency Bucket | Total | Correct | Errors | Accuracy |
|------------------|-------|---------|--------|----------|
| unseen (0) | 1000 | 2 | 998 | 0.0020 |

#### Common Errors (Top 10)
| Word | Gold | Predicted | Error Type | Frequency |
|------|------|-----------|------------|-----------|
| deihsakna | ['deih', 'sak', 'na'] | ['d', 'e', 'i', 'h', 's', 'a', 'k', 'n', 'a'] | over-segmentation | 0 |
| kilemin | ['kil', 'em', 'in'] | ['k', 'i', 'l', 'e', 'm', 'i', 'n'] | over-segmentation | 0 |
| zelzel | ['zel', 'zel'] | ['z', 'e', 'l', 'z', 'e', 'l'] | over-segmentation | 0 |
| arms | ['ar', 'm', 's'] | ['a', 'r', 'm', 's'] | over-segmentation | 0 |
| goliath | ['gol', 'iat', 'h'] | ['g', 'o', 'l', 'i', 'a', 't', 'h'] | over-segmentation | 0 |
| lampang | ['lam', 'pang'] | ['l', 'a', 'm', 'p', 'a', 'n', 'g'] | over-segmentation | 0 |
| anim | ['an', 'im'] | ['a', 'n', 'i', 'm'] | over-segmentation | 0 |
| buddihst | ['bu', 'd', 'dih', 's', 't'] | ['b', 'u', 'd', 'd', 'i', 'h', 's', 't'] | over-segmentation | 0 |
| zahtaakhuai | ['zah', 'ta', 'ak', 'hua', 'i'] | ['z', 'a', 'h', 't', 'a', 'a', 'k', 'h', 'u', 'a', 'i'] | over-segmentation | 0 |
| sawlin | ['sawl', 'in'] | ['s', 'a', 'w', 'l', 'i', 'n'] | over-segmentation | 0 |

## Statistical Significance Tests

### Rule-based vs CRF
- **Test**: McNemar's test
- **Statistic**: 0.0000
- **p-value**: 1.000000
- **Significant**: False
- **Interpretation**: No disagreements between segmenters

### Rule-based vs SentencePiece
- **Test**: McNemar's test (with continuity correction)
- **Statistic**: 996.0010
- **p-value**: 0.000000
- **Significant**: True
- **Interpretation**: Rule-based significantly outperforms the other (p=0.0000)

### CRF vs SentencePiece
- **Test**: McNemar's test (with continuity correction)
- **Statistic**: 996.0010
- **p-value**: 0.000000
- **Significant**: True
- **Interpretation**: CRF significantly outperforms the other (p=0.0000)
