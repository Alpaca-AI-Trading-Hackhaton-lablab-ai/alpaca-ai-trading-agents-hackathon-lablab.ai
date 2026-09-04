# Pendiente Alpacore — TradeLix backend

> **Before deploying**, read this file and verify each item is still accurate.
> Update it when APIs change or items are completed.
> _Last research: 2026-09-04 via Alpaca docs + community (bracket ≠ notional)._

## Agent workflow (mandatory)

1. **Research this file first** with a lighter / cheaper model + web search (Alpaca docs,
   paper API, bracket/trailing/fills). Rewrite the P1 notes here if anything changed.
2. **Then implement** with a capable model. Do not skip step 1.
3. **Modify and push** the contract files below to **`main`** in both repos. A local-only
   edit is not enough — deploy reads GitHub `main`.
4. **Only after those pushes**, deploy **backend + frontend on one EC2 `t3.small`**
   (single box; both Docker Compose stacks; shared `tradelix` network). Not two
   instances, not Fargate/ECS for this PoC. See the instance table below for why
   the type is `t3.small` and not the originally planned `t3.medium`.

### Files that must be edited and pushed

| Repo | Path | Why |
|------|------|-----|
| backend | `pendiente-alpacorp.md` | This backlog + deploy target. Update status after each cut. |
| backend | `AGENTS.md` | Deploy/push rules for agents. |
| backend | `CLAUDE.md` | Same rules (takes precedence over AGENTS). |
| backend | `README.md` | One-box `t3.small` pointer. |
| frontend | `AGENTS.md` | Points here; same single-EC2 + push rule. |
| frontend | `README.md` | Same one-box pointer. |

Backend remote: `Alpaca-AI-Trading-Hackhaton-lablab-ai/alpaca-ai-trading-agents-hackathon-lablab.ai`  
Frontend remote: `Alpaca-AI-Trading-Hackhaton-lablab-ai/alpacore-tradelix-web`

### Single EC2 (paper PoC) — deployed 2026-09-03

- One instance, both repos cloned (or pulled) from `main`.
- Start backend compose first (`postgres` + `redis` + `tradelix-backend:8000`, network `tradelix`).
- Then frontend compose (nginx `/api` → `tradelix-backend:8000`).
- Paper only. Secrets live in the backend `.env` on the box (mode `600`), never in git.

**Instance type is `t3.small`, not `t3.medium`.** AWS account `010539085752` is on the
Free Plan (US$100 credits, expires 2027-03-03), which only permits free-tier-eligible
instance types; `RunInstances` rejects `t3.medium` outright with
`InvalidParameterCombination`. `t3.small` gives 2 vCPU / 2 GiB instead of 2 vCPU / 4 GiB,
so the bootstrap provisions **4 GB of swap** — the Vite build is what spikes memory.
Raising the account to a PAID plan would unlock `t3.medium`; `m7i-flex.large`
(2 vCPU / 8 GiB) and `c7i-flex.large` (2 vCPU / 4 GiB) are free-tier-eligible
alternatives if the box turns out to be tight.

| | |
|---|---|
| Instance | `i-096fb4520e41cf74a` — `t3.small`, `us-east-2a` (Ohio) |
| Address | `3.21.62.12` (EIP `eipalloc-06035fe8e43bbb14e`) → `alpacorp.ribartra.org` |
| Security group | `sg-05e4f56ed4fffc069` — 22 from the operator IP only, 80/443 public |
| Key pair | `alpacore-poc` (ed25519) |
| Base | Ubuntu 24.04, Docker CE + Compose plugin, 30 GB gp3 |

Same frontend `nginx.conf` and `docker-compose.yml` locally and on the box (gzip, asset
cache, `/api` → backend). nginx always listens on container `:80`. Host port is
`WEB_PORT` (default **3200** for local). On the box set `WEB_PORT=80` in the web
project `.env` so the domain needs no port — do not fork nginx. An existing
`docker-compose.override.yml` on the instance is equivalent and must stay untracked.
Both stacks run `restart: unless-stopped`.

**The frontend repo is private**, so the box cannot `git clone` it without a token. It is
uploaded as a `git archive` tarball from the operator machine instead. Fix this by adding a
deploy key on the instance if the deploy has to be self-service.

Do not free the second EIP `16.58.127.199` — it backs the apex `ribartra.org`.

---

## P1 — done (2026-09-04)

Tick → `seed_plan` → `dispatch(plan=)` with **integer `qty`**. Risk zeros same-direction
adds. `fill_listener` fires parked emulated rows on parent `fill` / `partial_fill`.
Price motor (`/spy`, scheduler) stays as fallback.

**API Alpaca (rewritten 2026-09-04 — previous note was wrong):**
- Native bracket: `order_class="bracket"`, `take_profit.limit_price`, `stop_loss.stop_price`.
  **Does not combine with `notional` / fractional** (docs + forum 2026-08). We send
  `qty = floor(notional / entry)`. `qty < 1` → `NO_TRADE`, no broker call.
- Trailing as a bracket leg: still unsupported. Extra TP / BE / trail stay in
  `conditional.emulated_rows`.
- Child legs stay `HELD` until the parent fills (`QueryOrderStatus.ALL` to list them).
  Legs reject custom `client_order_id` PATCH. Map `parent_id → leg_ids` from `order.legs`
  at submit; store it on the parked plan JSON (no Alembic column).
- `trade_updates` paper stream (`TradingStream`). Parent `client_order_id` is on every
  event; leg ids are only those saved at submit.

---

## Fuera de este corte (P2+)

- Un solo endpoint HTTP (fusionar `/execute` + `/bracket/execute`).
- MCP: `mcp_client.py` aún muerto (qty, no notional).
- Critic / feedback loop entre fills y decision scoring.
- Backtesting offline con el mismo gate/risk pipeline.
