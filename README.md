# mcp-spendee

An unofficial Model Context Protocol (MCP) server for
[Spendee](https://www.spendee.com/). It lets MCP clients inspect wallets,
categories, and transactions, and create income or expense transactions.

> [!WARNING]
> Spendee does not publish a supported public API. This server uses the archived
> third-party [`spendee`](https://pypi.org/project/spendee/) package and may stop
> working when Spendee changes its private API. Test it with a non-critical
> wallet first.

## Tools

- `spendee_status` — check local configuration without logging in or exposing secrets.
- `list_wallets` — list wallet IDs, names, balances, and currencies.
- `list_labels` — list modern Spendee labels stored in Firestore.
- `list_categories` — list and optionally filter categories.
- `list_transactions` — list and optionally filter transactions.
- `create_transaction` — preview or create an expense/income transaction,
  optionally with existing modern labels.

Creating a transaction is deliberately two-step. Call `create_transaction` once
with `confirm=false` to inspect the normalized payload, then repeat it with
`confirm=true` and a unique `request_id`. Reusing a request ID in the same
server process returns the cached result instead of creating a duplicate.
When labels are requested, the server first creates the transaction through the
legacy API and then atomically sets its Firestore labels through the forked
`spendee-firestore-client` library. A retry with the same `request_id` retries
only failed label attachment and never creates the transaction twice.

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
