"""Preserve RequiredType wrappers before django-stubs calls get_proper_type.

django-stubs 5.2.9 handles every Annotated type, including LangChain TypedDicts.
Mypy 2.4 represents Required/NotRequired as RequiredType, not ProperType. Keep
that wrapper intact and delegate every other case to the unchanged Django plugin.
"""

from collections.abc import Callable

from mypy.plugin import AnalyzeTypeContext, Plugin
from mypy.types import RequiredType, Type
from mypy_django_plugin.main import NewSemanalDjangoPlugin


class CompatibleDjangoPlugin(NewSemanalDjangoPlugin):
    def get_type_analyze_hook(self, fullname: str) -> Callable[[AnalyzeTypeContext], Type] | None:
        original = super().get_type_analyze_hook(fullname)
        if original is None or fullname not in ("typing.Annotated", "typing_extensions.Annotated"):
            return original

        def analyze(ctx: AnalyzeTypeContext) -> Type:
            if ctx.type.args:
                inner = ctx.api.analyze_type(ctx.type.args[0])
                if isinstance(inner, RequiredType):
                    return inner
            return original(ctx)

        return analyze


def plugin(version: str) -> type[Plugin]:
    return CompatibleDjangoPlugin
