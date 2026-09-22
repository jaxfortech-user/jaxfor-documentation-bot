# Jaxfor Invoice Dashboard

Next.js (App Router) dashboard for the jaxfor-documentation-bot pipeline.
Shows processed invoices, a review queue, and summary stats. Auth via Clerk.
Deployed to Vercel; reads data from the Railway backend's read-only API.

## What it shows
- **Processed** (`/dashboard`) — every processed invoice, most recent first, plus summary cards (total, needs review, clean, vendor count)
- **Review Queue** (`/dashboard/review`) — only invoices flagged `needs_review`
- Each row links to the original file in Google Drive

## Setup

### 1. Clerk
1. Create a free app at https://clerk.com
2. Copy the Publishable Key and Secret Key from the Clerk dashboard
3. In Clerk's dashboard, under **User & Authentication**, enable whichever sign-in methods you want (email, Google, etc.)

### 2. Backend (Railway) — one new env var
Add this to your existing Railway project's env vars:

```
DASHBOARD_API_KEY=<generate any long random string>
DASHBOARD_ORIGIN=https://<your-vercel-domain>.vercel.app
```

`DASHBOARD_ORIGIN` can be a comma-separated list if you need to allow a preview URL too.
Redeploy Railway after adding these (or it'll pick them up on the next deploy anyway).

### 3. Local dev
```
cp .env.local.example .env.local
# fill in Clerk keys + JAXFOR_API_BASE_URL (your Railway URL) + JAXFOR_API_KEY (= DASHBOARD_API_KEY above)
npm install
npm run dev
```

### 4. Deploy to Vercel
1. Push this `dashboard/` folder to its own GitHub repo (or a subfolder of the existing repo — see note below)
2. Import it in Vercel
3. Set the same env vars as `.env.local` in Vercel's Project Settings → Environment Variables:
   - `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
   - `CLERK_SECRET_KEY`
   - `NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in`
   - `NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up`
   - `NEXT_PUBLIC_CLERK_AFTER_SIGN_IN_URL=/dashboard`
   - `NEXT_PUBLIC_CLERK_AFTER_SIGN_UP_URL=/dashboard`
   - `JAXFOR_API_BASE_URL` — your Railway backend URL
   - `JAXFOR_API_KEY` — same value as `DASHBOARD_API_KEY` on Railway
4. Deploy. Once you have the Vercel URL, go back to Railway and set `DASHBOARD_ORIGIN` to it, then redeploy Railway.

> Note on repo layout: if this folder lives inside the same repo as the Python backend,
> set Vercel's "Root Directory" (Project Settings → General) to `dashboard/` so it only
> builds this subfolder.

## How auth + data flow works
- Clerk's middleware protects every `/dashboard/*` page and every `/api/*` route — signed-out users get redirected to `/sign-in`.
- The dashboard pages are server components that call the Railway backend directly using a server-only `JAXFOR_API_KEY` — that key is never sent to the browser.
- The backend's `/api/*` endpoints (in `app/dashboard_api.py`) are read-only: they read straight from the output Google Sheet and never trigger the pipeline or write anything.
