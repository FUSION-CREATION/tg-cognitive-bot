from __future__ import annotations

from dataclasses import dataclass

from bot.cognitive import analyze_case, detect_distortions


@dataclass(frozen=True)
class PlanResult:
    distortions: list[str]
    hard_truth: str
    plan_today: str
    plan_week: str
    anti_relapse: str


@dataclass(frozen=True)
class ReplyResult:
    distortions: list[str]
    hard_truth: str
    short_reply: str
    assertive_reply: str
    hard_boundary_reply: str


@dataclass(frozen=True)
class AuditResult:
    distortions: list[str]
    hard_truth: str
    risks: list[str]
    go_criteria: list[str]
    no_go_criteria: list[str]
    next_step: str


def _context_plan_lines(text: str) -> tuple[str, str]:
    lowered = text.lower()
    if any(k in lowered for k in ("срок", "дедлайн", "задач", "проект")):
        return (
            "1) Выбери один блок на 25 минут и закрой его до конца. "
            "2) Зафиксируй критерий готовности и отправь статус вовне. "
            "3) Запланируй следующий блок в календаре.",
            "Дни 1-3: ежедневно закрывай по одному блоку. "
            "Дни 4-7: подними объем на 20% и пересобери план по фактам выполнения."
        )
    if any(k in lowered for k in ("конфликт", "сообщение", "начальник", "коллег", "клиент", "переписк")):
        return (
            "1) Сформулируй цель диалога в 1 фразу. "
            "2) Напиши черновик ответа без обвинений и с одним конкретным запросом. "
            "3) Отправь сообщение и зафиксируй договоренность.",
            "Дни 1-3: держи коммуникацию только в фактах и сроках. "
            "Дни 4-7: закрепи новый формат общения и убери триггерные формулировки."
        )
    if any(k in lowered for k in ("деньг", "долг", "оплат", "бюджет")):
        return (
            "1) Пропиши сумму, срок и приоритет платежа. "
            "2) Свяжись с участником и согласуй конкретный график. "
            "3) Зафиксируй договоренность письменно.",
            "Дни 1-3: закрой срочные обязательства по графику. "
            "Дни 4-7: пересобери бюджет и убери повторяющиеся утечки."
        )
    return (
        "1) Отдели факты от интерпретаций в 3 пунктах. "
        "2) Выбери один проверяемый шаг на 30 минут. "
        "3) Зафиксируй результат и следующий шаг.",
        "Дни 1-3: ежедневно выполняй один проверяемый шаг. "
        "Дни 4-7: масштабируй рабочую схему и убери то, что не дало результата."
    )


def build_plan(goal: str, situation: str, thought: str) -> PlanResult:
    analysis = analyze_case(situation=situation, thought=thought)
    top = analysis["distortions"][0]
    second = analysis["distortions"][1] if len(analysis["distortions"]) > 1 else None
    distortions = [top.title] + ([second.title] if second else [])

    plan_today, plan_week = _context_plan_lines(f"{goal} {situation} {thought}")
    anti_relapse = (
        f"Если снова накроет мысль в стиле '{top.title.lower()}', "
        "вернись к схеме: факт -> проверка допущения -> один короткий шаг."
    )

    return PlanResult(
        distortions=distortions,
        hard_truth=analysis["hard_truth"],
        plan_today=plan_today,
        plan_week=plan_week,
        anti_relapse=anti_relapse,
    )


def build_reply(incoming_message: str, desired_outcome: str) -> ReplyResult:
    matches = detect_distortions(incoming_message, top_n=2)
    distortions = [m[0].title for m in matches]
    top = matches[0][0]

    hard_truth = (
        "Неприятная правда: если отвечать из триггера, ты усилишь конфликт и ухудшишь исход. "
        "Тебе нужен ответ по цели, а не по эмоции."
    )

    short_reply = (
        "Принял сообщение. Давай вернемся к фактам и конкретному решению по вопросу."
    )
    assertive_reply = (
        f"Я вижу твою позицию. Моя цель: {desired_outcome}. "
        "Готов обсуждать по фактам и шагам, без личных оценок."
    )
    hard_boundary_reply = (
        "В таком тоне я продолжать не буду. "
        "Если готов к предметному диалогу без давления, продолжим."
    )

    # adapt tone if obvious pressure/manipulation detected
    lowered = incoming_message.lower()
    if any(token in lowered for token in ("всегда", "никогда", "ты обязан", "это твоя вина")):
        hard_boundary_reply = (
            "Я не принимаю формулировки с давлением и обвинениями. "
            "Готов продолжить только в уважительном и предметном формате."
        )

    return ReplyResult(
        distortions=distortions,
        hard_truth=f"{hard_truth} Вероятное искажение: {top.title.lower()}.",
        short_reply=short_reply,
        assertive_reply=assertive_reply,
        hard_boundary_reply=hard_boundary_reply,
    )


def build_decision_audit(situation: str, selected_action: str) -> AuditResult:
    analysis = analyze_case(situation=situation, thought=selected_action)
    top = analysis["distortions"][0]
    second = analysis["distortions"][1] if len(analysis["distortions"]) > 1 else None
    distortions = [top.title] + ([second.title] if second else [])

    risks = [
        "Решение может быть реактивным (из эмоции), а не рациональным.",
        "Ты можешь недооценивать последствия через 3-7 дней.",
        "Есть риск принять догадки за факты."
    ]

    go_criteria = [
        "Есть 2+ подтвержденных факта в пользу решения.",
        "Риск обратим или ограничен по ущербу.",
        "Есть конкретный первый шаг и дедлайн выполнения."
    ]

    no_go_criteria = [
        "Решение основано только на злости/страхе.",
        "Нет фактов, только интерпретации.",
        "Возможный ущерб высокий и необратимый."
    ]

    next_step = (
        "Сделай паузу 20 минут, затем перепроверь решение по критериям GO/NO-GO. "
        "Если хотя бы один пункт NO-GO совпал, не действуй импульсивно."
    )

    return AuditResult(
        distortions=distortions,
        hard_truth=analysis["hard_truth"],
        risks=risks,
        go_criteria=go_criteria,
        no_go_criteria=no_go_criteria,
        next_step=next_step,
    )
