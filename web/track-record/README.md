# Track record page

The public page for selling the sniper feed: every alert, what the price did next, and what the
trading bot's exits would have made. Alerts show up here 24 hours after they're sent (subscribers
pay for the real-time ones); nothing is removed, losers included.

- `public/`: the page (plain HTML, CSS and JS; every value from the data is inserted as text).
- `api/stats.py`: `GET /api/stats` fetches `STATS_URL` (the `stats.json` the VM publishes hourly)
  and lets Vercel's CDN cache it for 5 minutes.

## Deploy on Vercel

1. Vercel → **Add New → Project** → import `kgoodeee93/Quasi`.
2. **Root Directory**: `web/track-record`. Framework preset: **Other**. No build command.
3. Environment variables:
   - `STATS_URL` = the URL `deploy-vm.sh` prints, e.g.
     `https://storage.googleapis.com/YOUR_PROJECT-sniper-feed-public/stats.json`
   - `JOIN_URL` (optional) = where the "Get alerts in real time" button goes (an `https://` link to
     your Telegram channel or checkout page). No `JOIN_URL`, no button.
4. Deploy. Every push to `main` redeploys.

**Plan:** Vercel's Hobby plan is for personal, non-commercial use only. Once this page sells or
links to the paid feed, it needs Vercel Pro (or a host whose free plan allows commercial use).

## Try it locally

```bash
cd web/track-record
python -m pytest -q tests
```
