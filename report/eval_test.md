# Syllable Segmentation Evaluation Report

## Summary Metrics

| Segmenter | Boundary P | Boundary R | Boundary F1 | Syllable Acc | Word Acc | Total Words |
|-----------|------------|------------|-------------|--------------|----------|-------------|
| Rule-based | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1000 |

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

## Statistical Significance Tests
