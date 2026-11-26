# Sonar BTC model — 2026-11-26T22:24:35Z
BTC 60458.0 · regime DOWN · last closed day 2026-11-25

| horizon | target | best | AUC 2023+ | best baseline AUC | acc 2023+ | best baseline acc | beats baselines |
|---|---|---|---|---|---|---|---|
| d1 | up | gbm | 0.53 | 0.496 | 0.52 | 0.504 | ✅ |
| d1 | big | gbm | 0.652 | 0.614 | 0.872 | 0.874 | ❌ |
| d3 | up | logit | 0.529 | 0.49 | 0.518 | 0.529 | ❌ |
| d3 | big | logit | 0.595 | 0.631 | 0.866 | 0.866 | ❌ |
| d7 | up | logit | 0.54 | 0.492 | 0.513 | 0.525 | ❌ |
| d7 | big | blend | 0.664 | 0.704 | 0.852 | 0.85 | ❌ |

Today (probability, experimental): up_d1 0.439, big_d1 0.07, up_d3 0.47, big_d3 0.081, up_d7 0.525, big_d7 0.067
