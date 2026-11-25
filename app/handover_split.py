from app.handover.core import *  # noqa: F401,F403


def __getattr__(name):
    from app.handover import __getattr__ as package_getattr
    return package_getattr(name)
