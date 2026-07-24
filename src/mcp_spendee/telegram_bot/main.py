from __future__ import annotations

import logging
import signal
import time

from .config import Config
from .processor import Processor
from .state import StateStore
from .telegram import TelegramClient
from .transcriber import WhisperTranscriber


def run(config: Config) -> None:
    store = StateStore(config.state_file)
    client = TelegramClient(config)
    bot_name = client.check()
    transcriber = WhisperTranscriber(config, client)
    processor = Processor(config, store, client, transcriber)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logging.info(
        "bot started; name=%r allowlisted_users=%d private_only=%s offset=%d",
        bot_name,
        len(config.allowed_user_ids),
        config.private_only,
        store.marker,
    )
    if not config.allowed_user_ids:
        logging.warning("allowlist is empty; only /whoami, /help and /start will be handled")

    while not stopping:
        event = store.claim()
        if event is not None:
            try:
                status = processor.process(event)
                store.complete(event.input.message_id, status)
            except Exception as error:
                will_retry = store.retry_or_fail(event.input.message_id, config.max_attempts, error)
                logging.exception(
                    "message %s failed on attempt %d; retry=%s",
                    event.input.message_id,
                    event.attempts,
                    will_retry,
                )
                if not will_retry:
                    try:
                        client.send_text(
                            event.input,
                            "Не смог записать транзакцию после нескольких попыток. "
                            "Проверьте сервис и пришлите сообщение ещё раз.",
                        )
                    except Exception:
                        logging.exception("could not send terminal error")
            continue
        try:
            inputs, marker = client.poll(store.marker)
            store.add(inputs, marker)
            if inputs:
                logging.info("received %d message(s); offset=%d", len(inputs), marker)
        except Exception:
            logging.exception("polling failed")
            time.sleep(5)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        run(Config.load())
    except Exception:
        logging.exception("bot stopped")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
