"""Gestor de colas para las sucursales de Banco Meridional."""

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
