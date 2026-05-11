from aiogram.fsm.state import State, StatesGroup


class RazborStates(StatesGroup):
    situation = State()
    thought = State()
    emotion_before = State()
    emotion_after = State()


class SosStates(StatesGroup):
    trigger = State()
    emotion_before = State()
    thought = State()
    emotion_after = State()


class CheckinStates(StatesGroup):
    mood = State()
    stress = State()
    energy = State()
    note = State()
