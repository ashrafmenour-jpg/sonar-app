# Sonar BTC model — 2026-11-22T23:18:53Z
BTC 60215.8 · regime DOWN · last closed day 2026-11-21

| horizon | target | best | AUC 2023+ | best baseline AUC | acc 2023+ | best baseline acc | beats baselines |
|---|---|---|---|---|---|---|---|
| d1 | up | gbm | 0.53 | 0.496 | 0.519 | 0.504 | ✅ |
| d1 | big | gbm | 0.651 | 0.613 | 0.872 | 0.873 | ❌ |
| d3 | up | blend | 0.529 | 0.488 | 0.528 | 0.53 | ❌ |
| d3 | big | logit | 0.593 | 0.63 | 0.866 | 0.866 | ❌ |
| d7 | up | logit | 0.538 | 0.491 | 0.511 | 0.526 | ❌ |
| d7 | big | blend | 0.663 | 0.703 | 0.851 | 0.849 | ❌ |

Today (probability, experimental): up_d1 0.46, big_d1 0.068, up_d3 0.526, big_d3 0.103, up_d7 0.534, big_d7 0.084
