from django.apps import AppConfig


class DatasetsConfig(AppConfig):
    name = 'apps.datasets'

    def ready(self) -> None:
        from . import signals  # noqa: F401
