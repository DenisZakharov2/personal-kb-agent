"""Универсальный клиент для работы с LLM-провайдерами (Этап 2).

Работает с любым OpenAI-совместимым API: OpenAI, Kimi (Moonshot), OpenRouter,
Anthropic (через совместимый прокси), Ollama и др.

Минимальный контракт:
    complete(messages, *, model, temperature, max_tokens) -> LLMResponse

Исходный текст изолируется от системных инструкций (защита от промпт-инъекций).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import httpx

from kb_agent.config import AppConfig
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.processing.llm_client")

# Статусы, при которых имеет смысл повторить запрос
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    error: Optional[str] = None


class LLMClient:
    """Клиент к любому OpenAI-совместимому API."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.api_key = config.openai_api_key
        self.model = config.openai_model
        self.temperature = config.openai_temperature
        self.max_tokens = config.openai_max_tokens
        self.base_url = config.openai_base_url.rstrip("/")
        self.provider = config.llm_provider
        self.timeout = float(getattr(config, "llm_timeout", 60.0))
        self.max_retries = max(0, int(getattr(config, "llm_max_retries", 2)))

        # Локальные провайдеры (Ollama и т.п.) не требуют API-ключа
        if not self.api_key and self.provider not in ("ollama", "local", "custom"):
            logger.warning("API ключ LLM не задан (KB_OPENAI_API_KEY)")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter рекомендует передавать источник запроса
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/DenisZakharov2/personal-kb-agent"
            headers["X-Title"] = "personal-kb-agent"
        return headers

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """Отправляет запрос к LLM API и возвращает ответ.

        messages — список {role, content}. Первое сообщение должно быть system.
        Исходный текст материала передаётся как user-сообщение, изолированное
        от системных инструкций. При 429/5xx и сетевых ошибках запрос
        повторяется (KB_LLM_MAX_RETRIES, экспоненциальная пауза).
        """
        model = model or self.model
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens or self.max_tokens

        if not self.api_key and self.provider not in ("ollama", "local", "custom"):
            return LLMResponse(
                text="",
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0.0,
                error="API ключ LLM не задан",
            )

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        url = f"{self.base_url}/chat/completions"
        headers = self._headers()
        attempts = self.max_retries + 1
        start = time.time()
        last_error = ""

        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                    resp = client.post(url, headers=headers, json=payload)
                latency_ms = (time.time() - start) * 1000

                if resp.status_code in _RETRYABLE_STATUS and attempt + 1 < attempts:
                    last_error = f"HTTP {resp.status_code}"
                    logger.warning(
                        "LLM (%s): %s, повтор %s/%s",
                        self.provider, last_error, attempt + 1, attempts - 1,
                    )
                    time.sleep(0.5 * (2 ** attempt))
                    continue

                if resp.status_code != 200:
                    logger.error("LLM ошибка %s: %s", resp.status_code, resp.text[:500])
                    return LLMResponse(
                        text="",
                        model=model,
                        prompt_tokens=0,
                        completion_tokens=0,
                        latency_ms=latency_ms,
                        error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    )

                data = resp.json()
                choice = data["choices"][0]
                text = choice.get("message", {}).get("content", "")
                usage = data.get("usage", {})
                return LLMResponse(
                    text=text,
                    model=model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    latency_ms=latency_ms,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if attempt + 1 < attempts:
                    logger.warning(
                        "LLM (%s): %s, повтор %s/%s",
                        self.provider, last_error, attempt + 1, attempts - 1,
                    )
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                logger.exception("Ошибка при вызове LLM API")
                return LLMResponse(
                    text="",
                    model=model,
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=(time.time() - start) * 1000,
                    error=last_error,
                )

        # Недостижимо (цикл всегда возвращает результат), но нужно для типизации
        return LLMResponse(
            text="",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=(time.time() - start) * 1000,
            error=last_error or "неизвестная ошибка",
        )

    def summarize(
        self,
        raw_text: str,
        source_type: str,
        source_name: str,
        topics: Optional[list[str]] = None,
    ) -> LLMResponse:
        """Суммаризирует сырой текст в структурированную заметку."""
        topics_hint = ""
        if topics:
            topics_hint = (
                "\n\nТемы, интересующие пользователя (поле «Тема» должно быть одной из них "
                "или «Общее»): " + ", ".join(topics)
            )
        system_prompt = (
            "Вы — опытный редактор базы знаний. Ваша задача — прочитать сырой текст "
            "из источника (Telegram или YouTube) и превратить его в структурированную "
            "заметку для Obsidian. Отвечайте ТОЛЬКО на русском языке.\n\n"
            "Формат ответа:\n"
            "1. Заголовок (краткий, по сути)\n"
            "2. Суть (2-3 предложения)\n"
            "3. Ключевые идеи (список с маркерами)\n"
            "4. Инструменты и ресурсы (упомянутые сервисы, библиотеки, книги)\n"
            "5. Практические выводы (что можно применить)\n"
            "6. Теги (через запятую)\n"
            "7. Тема (одна тема из списка тем пользователя или «Общее»)\n\n"
            "Не придумывайте фактов, которых нет в тексте. Если информации мало — "
            "честно укажите это. Не выходите за рамки предоставленного текста."
            f"{topics_hint}"
        )
        user_prompt = (
            f"Источник: {source_type} ({source_name})\n\n"
            f"Текст:\n{raw_text}\n\n"
            "Напишите структурированную заметку согласно формату выше."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.complete(messages)
