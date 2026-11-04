"""Универсальный клиент для работы с LLM-провайдерами (Этап 2).

Работает с любым OpenAI-совместимым API: OpenAI, Kimi (Moonshot), OpenRouter,
Anthropic (через совместимый прокси), Ollama и др.

Минимальный контракт:
    complete(messages, *, model, temperature, max_tokens) -> LLMResponse

Исходный текст изолируется от системных инструкций (защита от промпт-инъекций).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from pydantic import BaseModel, Field, ValidationError

from kb_agent.config import AppConfig
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.processing.llm_client")


class SummaryData(BaseModel):
    """Структурированный результат суммаризации (P2-10).

    Вместо парсинга свободного Markdown по строкам «Теги:»/«Тема:» модель
    возвращает JSON по жёсткой схеме. Тема валидируется против тем профиля.
    """

    title: str = Field(default="Без названия", description="Краткий заголовок заметки")
    content: str = Field(default="", description="Полный текст заметки в формате Markdown")
    tags: list[str] = Field(default_factory=list, description="Теги (до 10)")
    topic: str = Field(default="Общее", description="Одна из тем профиля пользователя")


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_block(text: str) -> str:
    """Извлекает JSON из ответа модели (вырезает markdown-блоки, если есть)."""
    match = _JSON_BLOCK_RE.search(text or "")
    if match:
        return match.group(1)
    # Иначе пробуем взять всё от первой { до последней }
    start = text.find("{") if text else -1
    end = text.rfind("}") if text else -1
    if 0 <= start < end:
        return text[start : end + 1]
    return text or ""

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
        # Ответ последнего вызова complete(): нужна для записи generation_log
        # (токены, задержка, модель) без изменения сигнатуры методов.
        self.last_response: Optional[LLMResponse] = None

        # Локальные провайдеры (Ollama и т.п.) не требуют API-ключа
        if not self.api_key and self.provider not in ("ollama", "local", "custom"):
            logger.warning("API ключ LLM не задан (KB_OPENAI_API_KEY)")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # Локальные провайдеры (Ollama, local, custom) не требуют API-ключа:
        # Authorization не посылаем, чтобы не отправлять «Bearer None».
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
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
            self.last_response = LLMResponse(
                text="",
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0.0,
                error="API ключ LLM не задан",
            )
            return self.last_response

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
                    self.last_response = LLMResponse(
                        text="",
                        model=model,
                        prompt_tokens=0,
                        completion_tokens=0,
                        latency_ms=latency_ms,
                        error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    )
                    return self.last_response

                data = resp.json()
                choice = data["choices"][0]
                text = choice.get("message", {}).get("content", "")
                usage = data.get("usage", {})
                self.last_response = LLMResponse(
                    text=text,
                    model=model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    latency_ms=latency_ms,
                )
                return self.last_response
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
                self.last_response = LLMResponse(
                    text="",
                    model=model,
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=(time.time() - start) * 1000,
                    error=last_error,
                )
                return self.last_response

        # Недостижимо (цикл всегда возвращает результат), но нужно для типизации
        self.last_response = LLMResponse(
            text="",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=(time.time() - start) * 1000,
            error=last_error or "неизвестная ошибка",
        )
        return self.last_response

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

    def summarize_structured(
        self,
        raw_text: str,
        source_type: str,
        source_name: str,
        topics: Optional[list[str]] = None,
    ) -> SummaryData:
        """Суммаризирует текст в структурированный результат по JSON-схеме.

        Модель просится вернуть JSON по схеме ``SummaryData`` (заголовок,
        содержимое, теги, тема). Ответ парсится и валидируется через Pydantic;
        при невалидной схеме запрос повторяется до ``max_retries``.
        Тема валидируется против тем профиля: если модель вернула тему,
        отсутствующую в профиле, подбирается ближайшая или ``Общее``.

        Возвращает ``SummaryData`` (при полном отказе — заготовку с темой
        ``Общее`` и пустым содержимым), чтобы конвейер мог продолжить работу.
        """
        topics = topics or []
        topics_hint = f"\nДопустимые темы (выберите ровно одну): {', '.join(topics)}" if topics else ""
        system_prompt = (
            "Вы — опытный редактор базы знаний. Прочитайте сырой текст источника "
            "и верните структурированную заметку в формате JSON — только JSON, "
            "без любого другого текста.\n"
            "Обязательные поля: title (краткий заголовок), content (полный текст "
            "заметки в формате Markdown), tags (список тегов, массив строк), "
            f"topic (одна из допустимых тем{topics_hint}).\n"
            "Не придумывайте фактов, которых нет в тексте. "
            "Если информации мало — честно укажите это в contenте."
        )
        user_prompt = (
            f"Источник: {source_type} ({source_name})\n\n"
            f"Текст:\n{raw_text}\n\n"
            "Верните только JSON по схеме выше."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        last_error = ""
        for attempt in range(self.max_retries + 1):
            resp = self.complete(messages)
            if resp.error:
                last_error = resp.error or "пустой ответ от LLM"
                if attempt + 1 <= self.max_retries:
                    time.sleep(0.5 * (2 ** attempt))
                continue
            try:
                data = json.loads(_extract_json_block(resp.text))
                summary = SummaryData(**data)
                summary.topic = self._validate_topic(summary.topic, topics)
                return summary
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                logger.warning(
                    "LLM структурированный ответ невалиден (попытка %s/%s): %s",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
                if attempt + 1 <= self.max_retries:
                    time.sleep(0.5 * (2 ** attempt))

        logger.error("Не удалось получить валидную структуру от LLM: %s", last_error)
        return SummaryData(topic=self._validate_topic("Общее", topics))

    @staticmethod
    def _validate_topic(topic: str, topics: list[str]) -> str:
        """Валидирует тему против списка тем профиля.

        Если тема отсутствует в профиле, ищется ближайшее совпадение (одна
        строка содержит другую), иначе возвращается «Общее».
        """
        if not topics:
            return topic or "Общее"
        if topic in topics:
            return topic
        lower = (topic or "").lower()
        for t in topics:
            if t.lower() in lower or lower in t.lower():
                return t
        return "Общее"
