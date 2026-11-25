from app.domain.handover import *  # noqa: F401,F403


def __getattr__(name):
    from app.domain.handover import __getattr__ as fallback
    return fallback(name)
