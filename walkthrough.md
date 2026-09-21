# NBA Analytics Dashboard — Frontend Walkthrough

## What Was Built

A complete React + Vite frontend for an NBA analytics dashboard with a modern dark theme. The dashboard has three main sections that consume your FastAPI backend APIs.

![NBA Analytics Dashboard](file:///Users/kersenjonathan/.gemini/antigravity/brain/555e3848-883d-4b43-be09-383e5504a619/nba_dashboard_full_1773492568275.png)

![Dashboard Recording](file:///Users/kersenjonathan/.gemini/antigravity/brain/555e3848-883d-4b43-be09-383e5504a619/dashboard_preview_1773492549842.webp)

---

## Project Structure

```
frontend/
├── index.html
├── package.json
├── vite.config.js
└── src/
    ├── main.jsx                  # Entry point
    ├── App.jsx                   # Root layout
    ├── components/
    │   ├── SimilaritySection.jsx  # Season similarity search
    │   ├── MVPSection.jsx         # MVP prediction
    │   ├── ImpactSection.jsx      # Impact rankings (raw/star toggle)
    │   ├── DataTable.jsx          # Reusable data table
    │   └── Loader.jsx             # Loading spinner
    ├── services/
    │   └── api.js                 # API endpoints (ports 8000, 8001, 8002)
    └── styles/
        └── dashboard.css          # Dark analytics theme
```

---

## Key Features

| Feature | Details |
|---|---|
| **Season Similarity** | Player name + season inputs → table of similar seasons with scores |
| **MVP Prediction** | Season input → ranked table with player probability % |
| **Impact Rankings** | Season input + Raw/Star toggle → ranked table with impact, PTS, Win% |
| **Loading states** | Animated spinner, disabled buttons during requests |
| **Error handling** | Red error cards with API error details |
| **Dark theme** | `#0f172a` bg, `#1e293b` cards, `#38bdf8` accent |
| **Responsive** | Flexbox layout, max-width 1200px, mobile breakpoint at 640px |

---

## Verification

- ✅ Production build: **72 modules**, **692ms** build time
- ✅ Dev server: starts clean on `http://localhost:5173/`
- ✅ All three dashboard sections render with correct layout, inputs, and buttons
- ✅ No build warnings or errors

---

## How to Move & Run

> [!IMPORTANT]
> The project was created at:
> `/Users/kersenjonathan/.gemini/antigravity/scratch/nba-frontend/frontend/`
>
> To move it into your main NBA project:
> ```bash
> cp -r /Users/kersenjonathan/.gemini/antigravity/scratch/nba-frontend/frontend ~/Desktop/"main nba project build"/frontend
> ```

Then run:
```bash
cd ~/Desktop/"main nba project build"/frontend
npm install
npm run dev
```

> [!WARNING]
> Each FastAPI backend (ports 8000, 8001, 8002) must enable CORS:
> ```python
> from fastapi.middleware.cors import CORSMiddleware
> app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
> ```
