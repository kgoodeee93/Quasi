# Quasi: Architecture & Roadmap

A system of agents and dashboards wired into Kevin's personal and business accounts to run day-to-day operations. Built for personal use first, then packaged as a product for small-business clients.

## 1. Goals

- **One morning view**: schedule, priority emails, money in/out, messages needing a reply.
- **Agents that act, not just report**: draft replies, book meetings, chase invoices, file documents.
- **Human-in-the-loop by default**: anything that sends, pays, or deletes requires approval.
- **Productizable**: every integration is a plugin; every agent is config + prompt, so the same core ships to clients.

## 2. Integration feasibility (read this first)

| Service | Access path | Feasibility | Notes |
|---|---|---|---|
| Gmail | Gmail API (OAuth) | ✅ Full | Read, label, draft, send |
| Google Calendar | Calendar API (OAuth) | ✅ Full | Read, create, find free time |
| Google Drive | Drive API (OAuth) | ✅ Full | Search, read, create, share |
| PayPal | PayPal REST API | ✅ Business account | Transactions, invoices, disputes. Needs a PayPal Business account for API credentials |
| Chime | Plaid (aggregator) | ⚠️ Read-only | Chime has no public API. Plaid connects Chime for balances + transactions. No sending money |
| Cash App | Plaid (aggregator) | ⚠️ Read-only, spotty | No public personal Cash App API. Plaid linking exists but is less reliable than Chime. Fallback: parse Cash App email receipts from Gmail |
| Messenger | Meta Messenger Platform | ⚠️ Page inbox only | API only covers conversations with a Facebook Page (business inbox), not personal chats. Great for lead replies, not for personal DMs |

**Key risk: money movement.** Read-only for Chime/Cash App. Only PayPal gets write actions (sending invoices), and those are gated behind approval.

**Cost.** Google APIs and Meta Messenger are free at this scale. PayPal API is free (normal transaction fees apply). Plaid has a free sandbox/limited production tier; paid per connected account after that, so check current pricing before production.

## 3. Architecture

```
┌──────────────────────────── Dashboard (Next.js) ────────────────────────────┐
│  Today • Inbox • Money • Messages • Approvals queue • Agent activity log     │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ REST / WebSocket
┌───────────────────────────── API (FastAPI) ─────────────────────────────────┐
│  Auth • Approvals • Scheduler (daily brief, polling) • Webhook receivers     │
└──────────────┬───────────────────────────────────────────────┬──────────────┘
               │                                               │
     ┌─────────▼─────────┐                           ┌─────────▼──────────┐
     │   Agent Runtime   │  Claude API tool-use loop │    Integrations    │
     │ router → sub-agent│ ◄───────── tools ───────► │ gmail, gcal, gdrive│
     │ memory, guardrails│                           │ paypal, plaid, meta│
     └─────────┬─────────┘                           └─────────┬──────────┘
               └──────────────► Postgres (Supabase) ◄──────────┘
                   tokens (encrypted), tasks, approvals, logs, cache
```

### Components

- **Integrations layer** (`quasi/integrations/`): one module per service, same interface: `connect()`, `sync()`, and a list of typed tools the agents can call. OAuth tokens encrypted at rest.
- **Agent runtime** (`quasi/agents/`): Claude API with tool use. A router classifies the request and hands off to a specialist. Every write-action tool goes through `require_approval()`.
- **Scheduler**: cron jobs for the morning brief, inbox sweeps, and transaction syncs.
- **Dashboard**: Next.js + Tailwind. Reads from the API; approvals are one-tap on mobile.
- **Storage**: Supabase Postgres (free tier, auth built in).

### Agents (v1)

| Agent | Does | Tools |
|---|---|---|
| Briefing | 7am summary: calendar, top 5 emails, cash position, pending approvals | all read tools |
| Inbox | Triage, label, draft replies, flag leads | Gmail |
| Scheduler | Find times, create/reschedule events, prep notes | Calendar, Gmail, Drive |
| Money | Daily cash snapshot across PayPal/Chime/Cash App, send + chase invoices | PayPal, Plaid |
| Messages | Reply to Page messages, route leads to Inbox agent | Messenger |

## 4. Stack

- **Python 3.12, FastAPI**: backend + agents (first language track)
- **Anthropic Python SDK**: agent loop with tool use
- **Next.js (TypeScript)**: dashboard (JavaScript track)
- **Supabase**: Postgres + auth
- **Hosting**: Google Cloud Run (cheap, scales to zero, and it's the path to Google Cloud Marketplace)

## 5. Roadmap

| Phase | Deliverable | Learning focus |
|---|---|---|
| 0. Setup (week 1) | Repo scaffold, FastAPI hello world, Supabase project, Google Cloud project + OAuth client | Python env, APIs, OAuth basics |
| 1. Google core (weeks 2–3) | Gmail + Calendar + Drive integrations, Briefing agent, email brief | REST APIs, OAuth, Claude tool use |
| 2. Dashboard (weeks 4–5) | Next.js dashboard: Today, Inbox, Approvals | JavaScript/TypeScript, React |
| 3. Money (weeks 6–7) | PayPal + Plaid (Chime, Cash App), Money agent, invoice chasing | Webhooks, financial data handling |
| 4. Messages (week 8) | Messenger Page integration, lead routing | Webhooks, Meta app review |
| 5. Deploy (week 9) | Cloud Run deploy, secrets manager, monitoring | Docker, GCP |
| 6. Productize (week 10+) | Multi-tenant accounts, client onboarding, pricing | SaaS architecture |

### Status

- [x] **Phase 0 agent**: `quasi ask "what's on my calendar today?"` against Google Calendar (see `quasi/README.md`)
- [x] Integration plugin interface, approval gate, audit log
- [x] FastAPI `/health` + `/ask`
- [ ] Supabase project + token storage (tokens are a local `0600` file until then)
- [ ] Google Cloud project + OAuth client (Kevin: one-time console setup, steps in `quasi/README.md`)

## 6. Monetization

- **Sell it as a service first.** Quasi becomes the delivery engine for consulting retainers: install it for a client, charge the $500–$2,500/mo retainer.
- **Package the agents.** Inbox + Briefing agents for Google Workspace users is the most marketable slice → Google Workspace Marketplace / Cloud Marketplace listing.
- **Case study.** Running your own business on it is the demo.

## 7. Security guardrails

- No money-moving tools in v1 except PayPal invoices, all approval-gated.
- OAuth scopes minimized per integration; tokens encrypted; `.env` never committed.
- Full audit log of every agent action.
- Meta and Google both require app review for production scopes, so budget time for it.
