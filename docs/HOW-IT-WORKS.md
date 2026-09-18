# Myelinated Memory — How It Works

*A retrieval-strength memory system for Hermes Agent, built on the biology-inspired idea that memories you use a lot should be easy to recall, and memories you don't should cost less.*

## 1. The Big Idea
Normally an AI agent has a fixed memory allowance. Everything gets the same treatment regardless of how often it matters. Myelinated Memory assigns each memory a **score (0 → 1)** that rises when used and falls when ignored. High-scoring memories get full detail in context; low-scoring ones are demoted to a summary or stub — so the agent naturally spends its limited memory on what you actually rely on.

## 2. Mechanics
- **Boost on access (Hebbian):** When recalled and used, the score rises with diminishing returns.
- **Decay on dormancy (Ebbinghaus × aging):** Dormant memories lose strength based on age and category (User prefs decay slowly; one-off tasks decay fast).

## 3. Tiers
| Tier | Score Range | What the agent sees |
|------|-------------|----------------------|
| **Active** | > 0.5 | Full text, every time |
| **Latent** | 0.1–0.5 | One-line summary only |
| **Archived** | ≤ 0.1 | ID stub only |
