# mcp-spendee

An unofficial Model Context Protocol (MCP) server for
[Spendee](https://www.spendee.com/). It lets MCP clients inspect wallets,
categories, and transactions, and create income or expense transactions.

> [!WARNING]
> Spendee does not publish a supported public API. This server uses a
> [fork](https://github.com/gibsn/spendee) of the archived third-party
> `spendee` package and may stop working when Spendee changes its private API.
> Test it with a non-critical wallet first.

## Tools

- `spendee_status` — check local configuration without logging in or exposing secrets.
- `list_wallets` — list Firestore wallet IDs, names, states, and currencies.
- `list_labels` — list modern Spendee labels stored in Firestore.
- `list_categories` — list and optionally filter categories.
- `list_transactions` — list and optionally filter transactions.
- `create_transaction` — preview or create an expense/income transaction,
  optionally with existing modern labels and an amount in a currency different
  from the selected wallet.

Creating a transaction is deliberately two-step. Call `create_transaction` once
with `confirm=false` to inspect the normalized payload, then repeat it with
`confirm=true` and a unique `request_id`. Reusing a request ID in the same
server process returns the cached result instead of creating a duplicate.
Wallet routing is a client responsibility; the server does not encode personal
rules for wallet names, transaction purposes, currencies, or dates.
Wallet and category IDs are legacy integers when Spendee still provides them;
newer resources use Firestore UUID strings. Both forms are accepted by
`list_categories`, `list_transactions`, and `create_transaction`.

Before a confirmed write, the server compares the complete normalized
transaction with current Firestore data: wallet, category, amount, type, note,
date and foreign-currency fields. An exact match returns `status=existing`
instead of creating another transaction, even when a retry uses a different
`request_id` or the server has restarted. If the retry adds labels, the server
applies them to the existing transaction.
When two source operations genuinely have identical fields, the caller can set
`allow_duplicate=true` only after the user explicitly confirms that both are
separate charges.

The forked `spendee` library owns both the modern Firestore transaction and
label writes. If label attachment fails after transaction creation, a
retry with the same `request_id` retries only the labels and does not create the
transaction twice.

For a foreign-currency transaction, pass the original positive `amount` and its
ISO `currency`. The preview resolves Spendee's current exchange rate and shows
both the wallet amount and the original amount. Repeat the confirmed call with
the preview's `foreign_rate` in `exchange_rate`; this pins the exact conversion
that was reviewed instead of silently fetching a newer rate.

## Transaction history and dates

`list_transactions` filters on Firestore's `madeAt` timestamp before downloading
rows, using a [structured query](https://firebase.google.com/docs/firestore/reference/rest/v1/StructuredQuery).
Results are newest first within each wallet. `limit` (default 100, maximum 1000)
and `offset` apply per wallet; omit `wallet_id` to query each wallet separately.

- `date_from` / `date_to` as `YYYY-MM-DD` include the entire UTC calendar days.
- Timestamps must include `Z` or a UTC offset; the start is inclusive and the
  end exclusive. Naive timestamps are rejected, not interpreted in Moscow time.
- `limit=null` requires both date bounds and returns the complete period.
  Internal pages use a cursor and a fixed read snapshot. A failed page fails the
  whole call, never returning an apparently complete partial result.

For a duplicate precheck of a September 29 operation with an unknown source
zone, use its calendar date with a one-day margin on both sides:

```json
{
  "wallet_id": "<wallet ID from list_wallets>",
  "date_from": "2026-09-28",
  "date_to": "2026-09-30",
  "limit": null,
  "include_labels": false
}
```

The confirmed write also checks **all** transactions in a date window around
its normalized instant, without a row limit. The exact-match signature remains
unchanged; expanding a search window never changes the transaction timestamp.
`occurred_at` accepts explicit UTC offsets. Naive creation timestamps retain the
existing `SPENDEE_TIMEZONE` fallback, so callers should supply an offset when the
source timezone is known.

## Diagnostics

The server emits JSON events to stderr with a shared `call_id` and the MCP
protocol request ID. Events cover tool duration, worker queue and lock wait,
authentication and auth retries, HTTP endpoint category/status/duration/timeouts,
query bounds, page/document counts, and transaction result status. Wallets are
hashed. Credentials, tokens, HTTP bodies, URL query strings, transaction amounts,
notes and labels are not included in these diagnostic events.

Firebase authentication has a 10-second connection / 30-second read timeout;
Firestore retains its 30-second timeout. These are network timeouts, not a total
MCP call deadline. On client cancellation the server logs `tool_cancelled` and
eventually `worker_finished` with `after_cancel=true` if the worker was running.
The worker stops before starting further network requests; an in-flight write
can still complete, so retry an uncertain write only with the same `request_id`.
A proxy timeout that does not send an MCP cancellation cannot be detected as a
cancellation by this server; correlate its `tool_start` / `tool_end` events with
the client's timeout timestamp and proxy logs.

For the shared systemd pool on aitools, read the system journal (the unprivileged
user journal may show only older entries):

```bash
sudo journalctl _SYSTEMD_USER_UNIT=codex-mcp-pool.service --since '10 minutes ago' -o cat
```

## Installation

Install [uv](https://docs.astral.sh/uv/), clone the repository, then run:

```bash
uv sync
```

Set credentials in the MCP client environment. Do not commit a `.env` file:

```text
SPENDEE_EMAIL=you@example.com
SPENDEE_PASSWORD=your-password
SPENDEE_TIMEZONE=Europe/Moscow
SPENDEE_GLOBAL_CURRENCY=EUR
```

`SPENDEE_TIMEZONE` and `SPENDEE_GLOBAL_CURRENCY` are optional. They default to
`Europe/Moscow` and `EUR`.

## MCP client configuration

Use the absolute path to the cloned checkout:

```json
{
  "mcpServers": {
    "spendee": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/mcp-spendee",
        "run",
        "mcp-spendee"
      ],
      "env": {
        "SPENDEE_EMAIL": "you@example.com",
        "SPENDEE_PASSWORD": "your-password",
        "SPENDEE_TIMEZONE": "Europe/Moscow",
        "SPENDEE_GLOBAL_CURRENCY": "EUR"
      }
    }
  }
}
```

The server uses MCP over stdio. Logs and errors never write to the protocol's
stdout stream.

## Development

```bash
make install
make lint
make test
```

## Security

- Credentials are read only from environment variables.
- Status output never includes credentials.
- Transaction amounts must be positive; `transaction_type` determines the sign.
- Writes require both `confirm=true` and a non-empty `request_id`.
- The request cache handles immediate retries, and the Firestore content check
  prevents exact duplicates across request IDs and server restarts.

## License

MIT
