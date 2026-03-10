# Local UI Service Contract

## HTTP Endpoints

- `GET /api/accounts`
  - returns account list (password masked as `has_password`)
- `POST /api/accounts`
  - creates or updates a single account
- `DELETE /api/accounts/{name}`
  - removes account by name
- `POST /api/healthcheck`
  - request: `account_names[]` optional, `symbol` optional
  - response: per-account health status
- `POST /api/trade/submit-plan`
  - request: `plan_rows[]`, `timeout_seconds`, `poll_seconds`, `request_id` optional
  - response: execution results and idempotent `request_id`
- `POST /api/trade/quick-multi`
  - minimal input order endpoint for selected accounts:
  - request: `accounts[]`, `symbol`, `side`, `volume`, optional `trigger_price/sl_price/tp_price/comment`
  - response: `request_id`, submitted row count, results
- `GET /api/orders/active`
  - returns active positions + pending orders + aggregate floating profit
- `POST /api/orders/close`
  - close by `account + symbol + side(+optional volume)`
- `GET /api/healthcheck/{account_name}`
  - healthcheck one account
- `GET /api/license/status`
  - returns license/trial status
- `POST /api/license/activate`
  - request: local license file path
- `GET /api/system/ping`
  - liveness probe

## WebSocket Stream

- `GET /ws/realtime`
  - push interval: default 1 second
  - payload:
    - `type`: `snapshot | status | error`
    - `timestamp_utc`
    - `data`:
      - `accounts`: account-level summary
      - `positions`: all accounts active positions
      - `pending_orders`: all accounts active pending orders
      - `total_profit`: aggregate floating profit

## Idempotency

- `POST /api/trade/submit-plan` accepts optional `request_id`.
- repeated request with same `request_id` returns cached result.

## State Model

- lifecycle: `submitted -> placed -> filled -> closed` or `failed/timeout`
- account monitor status: `updating | healthy | degraded`
- license status: `trial_active | trial_expired | license_valid | license_invalid`
