# ProjectX — Admin

Internal administration panel for the ProjectX platform.

## Tech Stack

- **Next.js 16** (App Router)
- **React 19** / **TypeScript** (strict mode)
- **Tailwind CSS v4**

## Quick Start

### With Docker

```bash
# Production build
docker build -t projectx-admin .
docker run -p 3001:3001 projectx-admin

# Development (hot reload)
docker compose --profile dev up

# Production
docker compose --profile prod up --build
```

### Without Docker

```bash
npm install
npm run dev -- -p 3001
```

Open **http://localhost:3001**.

## Commands

```bash
npm run dev          # Dev server
npm run build        # Production build
npm run start        # Production server
npm run lint         # ESLint
```

## Deployment

| Target     | Platform                     |
|------------|------------------------------|
| MVP        | Railway (auto-deploy)        |
| Enterprise | AWS ECS Fargate + CloudFront |
