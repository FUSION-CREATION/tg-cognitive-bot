from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple
import re


@dataclass(frozen=True)
class Distortion:
    slug: str
    title: str
    signals: tuple[str, ...]
    reframe_question: str
    micro_action: str


DISTORTIONS: tuple[Distortion, ...] = (
    Distortion(
        slug="catastrophizing",
        title="Катастрофизация",
        signals=("все пропало", "конец", "ужас", "катастроф", "никогда не", "кошмар"),
        reframe_question="Что наиболее вероятно, а не что наиболее страшно?",
        micro_action="Напиши 3 исхода: худший, реалистичный, лучший. Действуй по реалистичному.",
    ),
    Distortion(
        slug="mind_reading",
        title="Чтение мыслей",
        signals=("они думают", "он думает", "она думает", "меня считают", "точно решил про меня"),
        reframe_question="Какие факты подтверждают, что ты точно знаешь мысли другого?",
        micro_action="Раздели лист на 2 колонки: факты и догадки. Ответ дай только по фактам.",
    ),
    Distortion(
        slug="fortune_telling",
        title="Предсказание будущего",
        signals=("точно не получится", "бесполезно", "ничего не выйдет", "все равно провал"),
        reframe_question="На каких данных основан прогноз, и что может пойти иначе?",
        micro_action="Оцени вероятность прогноза в % и пропиши план B на 2 шага.",
    ),
    Distortion(
        slug="black_white",
        title="Черно-белое мышление",
        signals=("или идеально", "либо успех либо", "всегда", "никогда", "полный провал"),
        reframe_question="Как выглядит 'достаточно хорошо' в этой ситуации?",
        micro_action="Сформулируй минимально приемлемый результат на сегодня.",
    ),
    Distortion(
        slug="disqualifying_positive",
        title="Обесценивание позитивного",
        signals=("случайно получилось", "мне просто повезло", "это не считается", "ничего особенного"),
        reframe_question="Какой твой реальный вклад в результат?",
        micro_action="Запиши 1 конкретное действие, которое ты сделал хорошо.",
    ),
    Distortion(
        slug="overgeneralization",
        title="Сверхобобщение",
        signals=("всегда так", "никогда не", "со мной постоянно", "вечно одно и то же"),
        reframe_question="Были ли исключения из этого правила?",
        micro_action="Назови 2 недавних контрпримера, где было иначе.",
    ),
    Distortion(
        slug="personalization",
        title="Персонализация",
        signals=("это из-за меня", "я во всем виноват", "все испортил я"),
        reframe_question="Какие факторы кроме тебя могли повлиять на результат?",
        micro_action="Составь карту факторов: я / другие / контекст.",
    ),
    Distortion(
        slug="emotional_reasoning",
        title="Эмоциональное доказательство",
        signals=("мне страшно значит", "чувствую значит это правда", "раз тревожно значит опасно"),
        reframe_question="Эмоция и факт здесь точно одно и то же?",
        micro_action="Назови отдельно эмоцию и 3 наблюдаемых факта.",
    ),
    Distortion(
        slug="should_statements",
        title="Долженствование",
        signals=("я должен", "они должны", "обязан", "обязаны", "нельзя ошибаться"),
        reframe_question="Что реалистично в этой ситуации вместо жесткого 'должен'?",
        micro_action="Перефразируй 1 'должен' в 'предпочитаю/выбираю'.",
    ),
    Distortion(
        slug="labeling",
        title="Ярлык",
        signals=("я неудачник", "я ничтожество", "он токсик", "я плохой"),
        reframe_question="Это ярлык личности или описание конкретного поведения?",
        micro_action="Опиши конкретное действие без ярлыков и оскорблений.",
    ),
    Distortion(
        slug="mental_filter",
        title="Ментальный фильтр",
        signals=("вижу только плохое", "ничего хорошего", "сплошной негатив"),
        reframe_question="Что важного ты сейчас не учитываешь?",
        micro_action="Сделай баланс: 3 минуса и 3 плюса ситуации.",
    ),
    Distortion(
        slug="sampling_bias",
        title="Ошибка выборки / выжившего",
        signals=("у всех получилось", "только у меня не выходит", "всем легко кроме меня", "ошибка выжившего"),
        reframe_question="Полную ли выборку ты видишь, или только видимую часть?",
        micro_action="Добавь минимум 3 примера людей, у кого был иной исход.",
    ),
)


_WORD_RE = re.compile(r"[а-яёa-z0-9-]+", re.IGNORECASE)
_SPLIT_RE = re.compile(r"[.!?]\s+")
_INTERPRETATION_MARKERS = (
    "кажется",
    "наверное",
    "думаю",
    "чувствую",
    "точно",
    "всегда",
    "никогда",
    "все",
    "никто",
)


def _normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


def detect_distortions(text: str, top_n: int = 2) -> List[Tuple[Distortion, int]]:
    normalized = _normalize(text)
    results: list[tuple[Distortion, int]] = []

    for distortion in DISTORTIONS:
        score = 0
        for signal in distortion.signals:
            if signal in normalized:
                score += 3
        if distortion.slug == "black_white":
            score += normalized.count("всегда") + normalized.count("никогда")
        if distortion.slug == "should_statements":
            score += normalized.count("должен") + normalized.count("обязан")
        if score > 0:
            results.append((distortion, score))

    if not results:
        default = next(d for d in DISTORTIONS if d.slug == "mental_filter")
        return [(default, 1)]

    results.sort(key=lambda item: item[1], reverse=True)
    return results[:top_n]


def split_facts_and_interpretations(situation: str, thought: str) -> tuple[list[str], list[str]]:
    raw_chunks = []
    for source in (situation, thought):
        for chunk in _SPLIT_RE.split(source):
            chunk = chunk.strip(" \n\t-–—")
            if chunk:
                raw_chunks.append(chunk)

    facts: list[str] = []
    interpretations: list[str] = []

    for chunk in raw_chunks:
        norm = _normalize(chunk)
        if any(marker in norm for marker in _INTERPRETATION_MARKERS):
            interpretations.append(chunk)
        else:
            facts.append(chunk)

    if not facts and situation.strip():
        facts = [situation.strip()]
    if not interpretations and thought.strip():
        interpretations = [thought.strip()]

    return facts[:3], interpretations[:3]


def build_reframe(distortions: List[Distortion]) -> str:
    titles = ", ".join(d.title.lower() for d in distortions)
    return (
        "Рабочая формулировка: 'Я отделяю наблюдаемые факты от своих выводов. "
        f"Сейчас у меня вероятны искажения: {titles}. "
        "Значит, финальный вывод о себе или о ситуации делать рано.'"
    )


def build_action(distortion: Distortion) -> str:
    return distortion.micro_action


def hard_truth(distortion: Distortion) -> str:
    messages = {
        "catastrophizing": "Неприятная правда: ты принимаешь худший сценарий за основной, это завышает тревогу и ломает решения.",
        "mind_reading": "Неприятная правда: ты делаешь вывод о чужих мыслях без прямых данных.",
        "fortune_telling": "Неприятная правда: прогноз звучит уверенно, но не подкреплен достаточными фактами.",
        "black_white": "Неприятная правда: жесткое 'или-или' обрезает рабочие промежуточные варианты.",
        "disqualifying_positive": "Неприятная правда: ты игнорируешь собственный вклад, и это искажает самооценку.",
        "overgeneralization": "Неприятная правда: единичный опыт раздувается до правила 'всегда/никогда'.",
        "personalization": "Неприятная правда: ты берешь на себя больше ответственности, чем реально контролируешь.",
        "emotional_reasoning": "Неприятная правда: сильная эмоция не равна объективному факту.",
        "should_statements": "Неприятная правда: формулировки 'должен/обязан' создают давление, но не дают плана действия.",
        "labeling": "Неприятная правда: ярлык про личность мешает корректно исправить конкретное поведение.",
        "mental_filter": "Неприятная правда: ты смотришь на часть данных и пропускаешь контекст.",
        "sampling_bias": "Неприятная правда: вывод сделан по неполной выборке.",
    }
    return messages.get(distortion.slug, "Неприятная правда: текущий вывод опережает факты.")


def analyze_case(situation: str, thought: str) -> dict:
    matches = detect_distortions(f"{situation} {thought}")
    distortions = [item[0] for item in matches]
    facts, interpretations = split_facts_and_interpretations(situation=situation, thought=thought)

    return {
        "distortions": distortions,
        "facts": facts,
        "interpretations": interpretations,
        "hard_truth": hard_truth(distortions[0]),
        "reframe": build_reframe(distortions),
        "action": build_action(distortions[0]),
        "questions": [d.reframe_question for d in distortions],
    }
