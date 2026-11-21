# Sonar BTC model — 2026-11-21T21:58:38Z
BTC 58951.5 · regime DOWN · last closed day 2026-11-20

| horizon | target | best | AUC 2023+ | best baseline AUC | acc 2023+ | best baseline acc | beats baselines |
|---|---|---|---|---|---|---|---|
| d1 | up | gbm | 0.535 | 0.496 | 0.528 | 0.503 | ✅ |
| d1 | big | gbm | 0.647 | 0.613 | 0.873 | 0.873 | ✅ |
| d3 | up | gbm | 0.516 | 0.487 | 0.519 | 0.53 | ❌ |
| d3 | big | logit | 0.597 | 0.64 | 0.866 | 0.866 | ❌ |
| d7 | up | logit | 0.507 | 0.49 | 0.49 | 0.527 | ❌ |
| d7 | big | logit | 0.64 | 0.706 | 0.849 | 0.849 | ❌ |

Today (probability, experimental): up_d1 0.494, big_d1 0.133, up_d3 0.625, big_d3 0.183, up_d7 0.56, big_d7 0.157
