import os
import socket
from typing import Any

from django.core.management.base import BaseCommand

from worker.loop import run_forever


class Command(BaseCommand):
    help = "Procesa la cola de trabajos hasta recibir una señal de parada."

    def handle(self, *args: Any, **options: Any) -> None:
        run_forever(f"{socket.gethostname()}:{os.getpid()}")
