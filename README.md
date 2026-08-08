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
- `list_wallets` — list wallet IDs, names, balances, and currencies.
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
Every call must also state `wallet_selection_reason`: `explicit_in_request`,
`travel_rule`, or `ordinary_default`. The server accepts `ordinary_default`
only for the `Операционка` wallet and `travel_rule` only for `Общий`, so an
agent cannot silently route an ordinary unspecified expense to `Общий`.
The forked `spendee` library owns both the legacy transaction call and modern
Firestore label write. If label attachment fails after transaction creation, a
retry with the same `request_id` retries only the labels and does not create the
transaction twice.

For a foreign-currency transaction, pass the original positive `amount` and its
ISO `currency`. The preview resolves Spendee's current exchange rate and shows
both the wallet amount and the original amount. Repeat the confirmed call with
the preview's `foreign_rate` in `exchange_rate`; this pins the exact conversion
that was reviewed instead of silently fetching a newer rate.

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
- The in-memory request cache prevents retries from duplicating a transaction
  only while the same server process remains running.

## License

MIT
