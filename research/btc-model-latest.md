# Sonar BTC model — 2026-11-22T04:15:03Z
BTC 60215.8 · regime DOWN · last closed day 2026-11-21

| horizon | target | best | AUC 2023+ | best baseline AUC | acc 2023+ | best baseline acc | beats baselines |
|---|---|---|---|---|---|---|---|
| d1 | up | gbm | 0.534 | 0.496 | 0.527 | 0.504 | ✅ |
| d1 | big | gbm | 0.647 | 0.613 | 0.873 | 0.873 | ✅ |
| d3 | up | gbm | 0.515 | 0.488 | 0.518 | 0.53 | ❌ |
| d3 | big | logit | 0.597 | 0.641 | 0.866 | 0.866 | ❌ |
| d7 | up | logit | 0.507 | 0.491 | 0.491 | 0.526 | ❌ |
| d7 | big | logit | 0.64 | 0.706 | 0.849 | 0.849 | ❌ |

Today (probability, experimental): up_d1 0.425, big_d1 0.088, up_d3 0.48, big_d3 0.143, up_d7 0.533, big_d7 0.116
