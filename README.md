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
  optionally with existing modern labels.

Creating a transaction is deliberately two-step. Call `create_transaction` once
with `confirm=false` to inspect the normalized payload, then repeat it with
`confirm=true` and a unique `request_id`. Reusing a request ID in the same
server process returns the cached result instead of creating a duplicate.
The forked `spendee` library owns both the legacy transaction call and modern
Firestore label write. If label attachment fails after transaction creation, a
retry with the same `request_id` retries only the labels and does not create the
transaction twice.

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

## Personal Telegram bot

The repository also contains a polling Telegram bot modelled after
`telegram-yazio-bot`. It accepts transaction requests as text, Russian voice
messages, or PNG/JPEG screenshots from a banking app, runs the installed
`spendee-add-transaction` Codex skill, and replies with the result. Voice
messages are converted with `ffmpeg` and recognized locally by `whisper.cpp`;
audio stays in a private temporary directory and is deleted immediately after
transcription.

For a screenshot, the bot selects the largest Telegram photo variant or accepts
an image sent as a PNG/JPEG document. The file is stored with private
permissions, attached to the ephemeral Codex invocation, and deleted immediately
after processing. Unlike voice transcription, screenshot recognition is not
local: the image is sent to OpenAI as Codex image input. Avoid sending unrelated
account details; crop the screenshot to the transaction when practical.

The bot keeps a durable queue and stores the Codex result before sending the
Telegram reply. This prevents delivery retries from creating a transaction
twice. Only users from `TELEGRAM_BOT_ALLOWED_USER_IDS` are allowed to invoke
Codex, and private chats are required by default.

Install the user service:

```bash
./scripts/install-systemd.sh
```

The installer creates the local configuration with permissions `0600`:

```text
~/.codex/secrets/telegram-spendee-bot.env
```

Create a separate bot with [@BotFather](https://t.me/BotFather), put its token
into `TELEGRAM_BOT_TOKEN`, and set your numeric Telegram ID in
`TELEGRAM_BOT_ALLOWED_USER_IDS`. The installed `spendee-add-transaction` skill,
an authenticated Codex CLI, the Spendee MCP configuration, `uv`, `ffmpeg`,
`cmake`, and `git` are required.

After filling the configuration, start the service:

```bash
./scripts/install-systemd.sh --start
```

Commands `/help`, `/whoami`, and `/status` are supported.

The installer also enables `telegram-spendee-bot-update.timer`. Every minute it
checks `origin/main`, accepts only fast-forward updates on a clean `main`
checkout, runs `make test` and `make lint`, synchronizes the Python environment,
and restarts the service. Deployment results are sent through the local
`send_codex_telegram` helper.

## Security

- Credentials are read only from environment variables.
- Status output never includes credentials.
- Transaction amounts must be positive; `transaction_type` determines the sign.
- Writes require both `confirm=true` and a non-empty `request_id`.
- The in-memory request cache prevents retries from duplicating a transaction
  only while the same server process remains running.

## License

MIT
