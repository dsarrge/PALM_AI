# PALM Food AI
Personal Analytics &amp; Life Modeling AI 
- An offline-first food and nutrition logging system.

## Modules
- `food_ai/` — nutrition, food logging, barcode pipelines
- `shared/` — shared persistence (SQLite, models, artifacts)
- `scripts/` — utilities & entry points
- `artifacts/` — generated outputs

### Barcode Scan Module
This module focuses on:
- Barcode-based food intake logging
- Offline-first product resolution
- Nutrition normalization and validation
- Daily nutrition aggregation

---

## System Architecture

```mermaid
flowchart LR
    %% ================= ROOT =================
    ROOT[PALM/]

    %% ================= MODULES =================
    subgraph FoodAI["food_ai/  (Food & Nutrition Logic)"]
        E[log_consumption.py]
        B[barcode_lookup.py]
        F[nutrition_engine.py]
        G[daily_totals.py]
    end

    subgraph Utils["utils/  (IO & External Systems)"]
        A[barcode_read.py]
        C[openfood_lookup.py]
        D[openfood_store.py]
        H[daily_nutrition.py]
    end

    %% ================= SHARED DATA =================
    subgraph Shared["shared/  (Global Persistence)"]
        DB[(palm.db)]
    end

    ROOT --> FoodAI
    ROOT --> Utils
    ROOT --> Shared

    A --> B

    B -->|local hit| DB
    B -->|miss| C
    C --> D
    D --> DB
    DB --> B

    B --> E
    E --> F
    F --> E
    E --> DB

    DB --> G
    DB --> H

