# Sonar BTC model — 2026-11-23T12:29:25Z
BTC 61271.1 · regime DOWN · last closed day 2026-11-22

| horizon | target | best | AUC 2023+ | best baseline AUC | acc 2023+ | best baseline acc | beats baselines |
|---|---|---|---|---|---|---|---|
| d1 | up | gbm | 0.529 | 0.495 | 0.519 | 0.504 | ✅ |
| d1 | big | gbm | 0.651 | 0.614 | 0.872 | 0.873 | ❌ |
| d3 | up | logit | 0.529 | 0.489 | 0.518 | 0.529 | ❌ |
| d3 | big | logit | 0.594 | 0.63 | 0.866 | 0.866 | ❌ |
| d7 | up | logit | 0.539 | 0.491 | 0.512 | 0.526 | ❌ |
| d7 | big | blend | 0.663 | 0.703 | 0.852 | 0.849 | ❌ |

Today (probability, experimental): up_d1 0.406, big_d1 0.079, up_d3 0.5, big_d3 0.083, up_d7 0.477, big_d7 0.072
