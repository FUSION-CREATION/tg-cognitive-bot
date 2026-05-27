from aiogram.fsm.state import State, StatesGroup


class RazborStates(StatesGroup):
    intake = State()


class PlanStates(StatesGroup):
    intake = State()


class AuditStates(StatesGroup):
    intake = State()


class RealityStates(StatesGroup):
    intake = State()


class ProgressReportStates(StatesGroup):
    intake = State()


class CrisisStates(StatesGroup):
    followup = State()


class AdminBroadcastStates(StatesGroup):
    waiting_segment = State()
    waiting_text = State()
    waiting_confirm = State()


class AdminAccessStates(StatesGroup):
    waiting_grant_tg_id = State()
    waiting_revoke_tg_id = State()


class AdminPanelStates(StatesGroup):
    waiting_action = State()
