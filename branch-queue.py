"""Gestor de colas para las sucursales de Banco Meridional."""

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime

VALID_SERVICES: tuple[str, ...] = ("deposito", "retiro", "gestion_cuenta")


class InvalidServiceError(ValueError):
    """El tipo de servicio solicitado no existe en la sucursal."""

    def __init__(self, service: str) -> None:
        self.service = service
        self.valid_services = VALID_SERVICES
        super().__init__(
            f"Servicio inválido: {service!r}. "
            f"Servicios válidos: {', '.join(VALID_SERVICES)}."
        )


class EmptyQueueError(LookupError):
    """No hay clientes esperando para el servicio indicado."""

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(f"No hay clientes esperando para el servicio {service!r}.")


@dataclass(frozen=True)
class Ticket:
    """Ticket numerado que recibe un cliente al llegar a la sucursal."""

    number: int
    client_name: str
    service_type: str
    issued_at: datetime

    def __str__(self) -> str:
        return (
            f"#{self.number} · {self.client_name} · "
            f"{self.service_type} · {self.issued_at:%H:%M:%S}"
        )


class BranchQueue:
    """Colas FIFO de una sucursal: una deque por servicio y numeración global."""

    def __init__(self) -> None:
        self._queues: dict[str, deque[Ticket]] = {
            service: deque() for service in VALID_SERVICES
        }
        self._next_number: int = 1
        self._lock = threading.Lock()

    def _validate_service(self, service_type: str) -> str:
        """Normaliza el servicio (strip + lower) y lo valida. O(1)."""
        if not isinstance(service_type, str):
            raise InvalidServiceError(repr(service_type))
        normalized = service_type.strip().lower()
        if normalized not in self._queues:
            raise InvalidServiceError(service_type)
        return normalized

    def issue_ticket(self, client_name: str, service_type: str) -> Ticket:
        """Emite un ticket y lo encola al final de su servicio. O(1)."""
        service = self._validate_service(service_type)
        name = client_name.strip() if isinstance(client_name, str) else ""
        if not name:
            raise ValueError("El nombre del cliente no puede estar vacío.")
        with self._lock:
            ticket = Ticket(
                number=self._next_number,
                client_name=name,
                service_type=service,
                issued_at=datetime.now(),
            )
            self._next_number += 1
            self._queues[service].append(ticket)
        return ticket

    def call_next(self, service_type: str) -> Ticket:
        """Retira y devuelve el ticket más antiguo del servicio. O(1).

        Lanza EmptyQueueError si no hay nadie esperando para ese servicio.
        """
        service = self._validate_service(service_type)
        with self._lock:
            queue = self._queues[service]
            if not queue:
                raise EmptyQueueError(service)
            return queue.popleft()

    def peek_next(self, service_type: str) -> Ticket | None:
        """Devuelve el siguiente ticket del servicio sin retirarlo, o None. O(1)."""
        service = self._validate_service(service_type)
        with self._lock:
            queue = self._queues[service]
            return queue[0] if queue else None

    def list_waiting(self) -> dict[str, list[Ticket]]:
        """Copia en orden FIFO de todos los tickets en espera, por servicio. O(n)."""
        with self._lock:
            return {service: list(queue) for service, queue in self._queues.items()}

    def stats(self) -> dict[str, int]:
        """Número de clientes en espera por servicio más la clave "total".

        O(1): len() de una deque es O(1) y el número de servicios es fijo.
        """
        with self._lock:
            counts = {service: len(queue) for service, queue in self._queues.items()}
        counts["total"] = sum(counts.values())
        return counts
