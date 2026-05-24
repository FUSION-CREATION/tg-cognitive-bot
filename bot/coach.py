from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI


_JSON_RE = re.compile(r"\{.*\}", re.S)


class ProductCoach:
    def __init__(
        self,
        api_key: str | None,
        model: str = "gpt-4.1-mini",
        input_cost_per_1m: float = 0.4,
        output_cost_per_1m: float = 1.6,
    ) -> None:
        self.model = model
        self.input_cost_per_1m = float(input_cost_per_1m)
        self.output_cost_per_1m = float(output_cost_per_1m)
        self.client = AsyncOpenAI(api_key=api_key) if api_key else None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def case_analysis(self, user_text: str, user_context: str = "") -> dict[str, Any] | None:
        schema = {
            "enough_context": True,
            "missing_context": [""],
            "clarifying_questions": [""],
            "problem_core": "",
            "distortions": [{"name": "", "why": "", "evidence": ""}],
            "reality_score_0_100": 0,
            "cost_of_illusion_30d": "",
            "main_excuse": "",
            "counter_fact": "",
            "hard_truth": "",
            "reframe": "",
            "actions_15m": [""],
            "actions_24h": [""],
            "non_negotiable_step_24h": "",
            "anti_relapse": "",
            "clarifying_question": "",
        }
        return await self._run_json_task(
            task_name="case_analysis",
            schema=schema,
            user_text=user_text,
            user_context=user_context,
            instruction=(
                "Сделай объективный психологический разбор ситуации из сообщения на русском языке. "
                "Не утешай без оснований. Покажи реальную проблему и рабочие шаги. "
                "Определи 1-3 вероятных когнитивных искажения. "
                "Пиши предельно кратко: только суть, без украшений. "
                "Добавь оценку Reality Score 0-100: насколько мышление сейчас опирается на факты, а не эмоции. "
                "Добавь cost_of_illusion_30d: конкретная цена бездействия за 30 дней. "
                "Выдели одну главную отмазку (main_excuse) и один контрфакт (counter_fact), который ее ломает. "
                "Запрещено давать дыхательные/медитативные техники по умолчанию: "
                "давай их только при явных признаках острой тревоги/паники в тексте."
            ),
        )

    async def action_plan(self, user_text: str, user_context: str = "") -> dict[str, Any] | None:
        schema = {
            "enough_context": True,
            "missing_context": [""],
            "clarifying_questions": [""],
            "goal_interpretation": "",
            "main_blocker": "",
            "hard_truth": "",
            "plan_24h": [""],
            "plan_7d": [""],
            "plan_30d": [""],
            "no_compromise_rules": [""],
            "failure_trigger": "",
            "recovery_if_failed": "",
            "first_step": "",
            "checkpoint_metric": "",
        }
        return await self._run_json_task(
            task_name="action_plan",
            schema=schema,
            user_text=user_text,
            user_context=user_context,
            instruction=(
                "Построй практичный план выхода из ситуации. "
                "Фокус на действиях, измеримости и реалистичности. Без мотивационной воды. "
                "Ответ короткий и прямой. "
                "План должен быть жестким: с дедлайнами, правилами без компромиссов и понятной ценой срыва. "
                "Укажи failure_trigger и recovery_if_failed, чтобы не было слива после одной ошибки."
            ),
        )

    async def reply_variants(self, user_text: str, user_context: str = "") -> dict[str, Any] | None:
        schema = {
            "enough_context": True,
            "missing_context": [""],
            "clarifying_questions": [""],
            "conflict_pattern": "",
            "hard_truth": "",
            "reply_short": "",
            "reply_assertive": "",
            "reply_boundary": "",
            "risk_if_send_emotional": "",
        }
        return await self._run_json_task(
            task_name="reply_variants",
            schema=schema,
            user_text=user_text,
            user_context=user_context,
            instruction=(
                "Сформируй три версии ответа на конфликтное сообщение: кратко, уверенно, жесткая граница. "
                "Ответы должны снижать хаос и вести к цели пользователя. "
                "Каждый ответ короткий, без лишних слов."
            ),
        )

    async def decision_audit(self, user_text: str, user_context: str = "") -> dict[str, Any] | None:
        schema = {
            "enough_context": True,
            "missing_context": [""],
            "clarifying_questions": [""],
            "decision_summary": "",
            "hard_truth": "",
            "likely_distortions": [""],
            "risks": [""],
            "irreversible_risks": [""],
            "go_criteria": [""],
            "no_go_criteria": [""],
            "verdict": "GO",
            "confidence_0_100": 0,
            "what_must_be_true_before_action": [""],
            "best_next_step": "",
        }
        return await self._run_json_task(
            task_name="decision_audit",
            schema=schema,
            user_text=user_text,
            user_context=user_context,
            instruction=(
                "Проведи аудит решения перед действием. "
                "Нужен трезвый вывод и критерии GO/NO-GO. "
                "Пиши коротко и жестко по делу. "
                "Дай прямой вердикт (GO, NO-GO или HOLD) и confidence_0_100. "
                "Отдельно выдели необратимые риски (irreversible_risks). "
                "Отдельно перечисли what_must_be_true_before_action: факты-предусловия без которых действовать нельзя. "
                "Не используй абсолюты типа 'гарантированно', 'всегда', 'никогда' без железных фактов."
            ),
        )

    async def crisis_support(self, user_text: str, user_context: str = "") -> dict[str, Any] | None:
        schema = {
            "risk_level": "high",
            "what_i_heard": "",
            "next_10_min": [""],
            "one_person_to_contact": "",
            "one_message_template": "",
            "one_small_step_now": "",
            "followup_question": "",
        }
        return await self._run_json_task(
            task_name="crisis_support",
            schema=schema,
            user_text=user_text,
            user_context=user_context,
            instruction=(
                "Человек в эмоциональном кризисе с риском самоповреждения. "
                "Дай короткий план безопасности на 10 минут, без воды, без осуждения. "
                "Фокус: что делать прямо сейчас, к кому обратиться, как пережить ближайший пик."
            ),
        )

    async def reality_check(
        self,
        user_text: str,
        user_context: str = "",
        profile_context: str = "",
    ) -> dict[str, Any] | None:
        schema = {
            "enough_context": True,
            "missing_data": [""],
            "profile_quality": 0,
            "profile": {
                "age": "",
                "location": "",
                "income_monthly": "",
                "expenses_monthly": "",
                "debt": "",
                "work_status": "",
                "work_hours_week": "",
                "goal_12m": "",
                "main_problems": [""],
                "what_tried": "",
                "constraints": "",
                "time_for_change_hours_week": "",
            },
            "where_you_are_now": "",
            "hard_truth": "",
            "self_deception": [""],
            "improve_first": [""],
            "plan_7d": [""],
            "weekly_metrics": [""],
            "first_step_24h": "",
        }
        return await self._run_json_task(
            task_name="reality_check",
            schema=schema,
            user_text=user_text,
            user_context=f"{user_context}\nPrevious profile: {profile_context or 'нет данных'}",
            instruction=(
                "Сделай Reality Check по жизненному контексту. "
                "Сначала структурируй профиль из входного текста. "
                "Потом дай жесткий, но полезный разбор: где реальность, где самообман, что исправлять первым. "
                "Тон прямой и трезвый, без унижения, без мотивационной воды. "
                "Если не хватает критичных данных, enough_context=false и перечисли missing_data. "
                "Списки максимум по 3 пункта, каждый пункт конкретный и проверяемый."
            ),
        )

    async def _run_json_task(
        self,
        task_name: str,
        schema: dict[str, Any],
        user_text: str,
        instruction: str,
        user_context: str,
    ) -> dict[str, Any] | None:
        if not self.client:
            return None

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты продуктовый психологический ассистент в Telegram. "
                            "Отвечай конкретно, честно и полезно. "
                            "Никаких общих фраз, только разбор и прикладные шаги. "
                            "Тон: без преукрас, без подлизывания. "
                            "Формулировки короткие: 1-2 фразы на поле, списки максимум 3 пункта. "
                            "Текст должен быть сильным и предметным: что делать и зачем это сработает. "
                            "Если текст грубый, с матом или про секс, не цензурируй и не морализируй: "
                            "разбирай по сути, где ошибка мышления и какой рабочий шаг дальше. "
                            "Избегай недоказуемых абсолютов: не пиши 'гарантированно', 'всегда', 'никогда', "
                            "если это не прямой факт из входа. "
                            "Запрещено использовать слово 'пользователь' и безличные формулировки типа "
                            "'человек испытывает'. Пиши адресно на 'ты'. "
                            "Если в сообщении не хватает фактов, не выдумывай: ставь enough_context=false, "
                            "заполняй missing_context и clarifying_questions, а остальные поля коротко или пусто. "
                            "Перед выбором искажений опирайся на факты из текста. "
                            "Для каждого искажения в поле evidence приводи короткий фрагмент исходного текста "
                            "или точный факт, на который опираешься. "
                            "Поле actions_15m должно быть привязано к контексту: "
                            "каждый шаг содержит конкретный объект из текста (человек/проект/срок/сообщение). "
                            "Избегай шаблонов вроде 'подыши', 'запиши чувства', если нет явного запроса на это. "
                            "Используй названия из CBT-карты: катастрофизация, чтение мыслей, предсказание будущего, "
                            "черно-белое мышление, сверхобобщение, персонализация, эмоциональное доказательство, "
                            "долженствование, ментальный фильтр, обесценивание позитивного, ярлык, "
                            "ошибка выборки/выжившего. "
                            "Учитывай историю и паттерны пользователя, если они переданы. "
                            "Верни только JSON по заданной схеме."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Task: {task_name}\n"
                            f"Instruction: {instruction}\n"
                            f"User context: {user_context or 'нет данных'}\n"
                            f"JSON schema example: {json.dumps(schema, ensure_ascii=False)}\n"
                            f"User input: {user_text}"
                        ),
                    },
                ],
            )

            content = response.choices[0].message.content or ""
            payload = self._parse_json(content)
            if payload is None:
                return None

            usage = response.usage
            in_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            out_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            cost = self._estimate_cost(in_tokens=in_tokens, out_tokens=out_tokens)

            payload["__meta"] = {
                "source": "llm",
                "model": self.model,
                "input_tokens": in_tokens,
                "output_tokens": out_tokens,
                "audio_seconds": 0,
                "cost_usd": round(cost, 8),
            }
            return payload
        except Exception:
            return None

    def _estimate_cost(self, in_tokens: int, out_tokens: int) -> float:
        return (
            (float(in_tokens) / 1_000_000.0) * self.input_cost_per_1m
            + (float(out_tokens) / 1_000_000.0) * self.output_cost_per_1m
        )

    def _parse_json(self, raw: str) -> dict[str, Any] | None:
        raw = raw.strip()
        if not raw:
            return None

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        match = _JSON_RE.search(raw)
        if not match:
            return None

        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
