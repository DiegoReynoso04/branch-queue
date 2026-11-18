"""Gestor de colas para las sucursales de Banco Meridional."""

# Diseño: una deque por servicio (dict[str, deque[Ticket]]) para que call_next sea
# O(1) sin depender de cuánta gente espere en otros servicios, y numeración global.
# Un threading.Lock hace que "comprobar y retirar" sea atómico en call_next: solo se
# anuncia un ticket ya retirado de la cola. Detalles en DESIGN.md / DESIGN.es.md.

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

VALID_SERVICES: tuple[str, ...] = ("deposito", "retiro", "gestion_cuenta")


class InvalidServiceError(ValueError):
    """El tipo de servicio solicitado no existe en la sucursal."""

    def __init__(self, service: object) -> None:
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


def normalize_service(service_type: str) -> str:
    """Normaliza el servicio (strip + lower) y lo valida. O(1).

    Lanza InvalidServiceError si no es uno de VALID_SERVICES.
    """
    if not isinstance(service_type, str):
        raise InvalidServiceError(service_type)
    normalized = service_type.strip().lower()
    if normalized not in VALID_SERVICES:
        raise InvalidServiceError(service_type)
    return normalized


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
        return normalize_service(service_type)

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


class BranchQueueCLI:
    """Menú interactivo de terminal. Toda la E/S pasa por input_fn y output_fn."""

    MENU = (
        "\n=== Banco Meridional · Gestor de colas ===\n"
        "1. Emitir ticket\n"
        "2. Llamar al siguiente\n"
        "3. Ver siguiente\n"
        "4. Lista de espera\n"
        "5. Estadísticas\n"
        "0. Salir"
    )

    SERVICE_BY_NUMBER: dict[str, str] = {
        str(number): service for number, service in enumerate(VALID_SERVICES, start=1)
    }

    def __init__(
        self,
        queue: BranchQueue | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.queue = queue if queue is not None else BranchQueue()
        self._input = input_fn
        self._output = output_fn
        self._actions: dict[str, Callable[[], None]] = {
            "1": self._issue_ticket,
            "2": self._call_next,
            "3": self._peek_next,
            "4": self._list_waiting,
            "5": self._stats,
        }

    def run(self) -> None:
        """Bucle principal. Termina con la opción 0, Ctrl+C o Ctrl+D."""
        try:
            while True:
                self._output(self.MENU)
                option = self._input("Elige una opción: ").strip()
                if option == "0":
                    self._output("¡Hasta luego!")
                    return
                action = self._actions.get(option)
                if action is None:
                    self._output(
                        f"Opción no válida: {option!r}. Elige un número del 0 al 5."
                    )
                    continue
                try:
                    action()
                except InvalidServiceError as error:
                    self._output(f"Error: {error}")
                except EmptyQueueError as error:
                    self._output(f"Aviso: {error}")
                except ValueError as error:
                    self._output(f"Error: {error}")
        except (KeyboardInterrupt, EOFError):
            self._output("\nSesión terminada. ¡Hasta luego!")

    def _ask_name(self) -> str:
        """Pide el nombre del cliente hasta que no esté vacío."""
        while True:
            name = self._input("Nombre del cliente: ").strip()
            if name:
                return name
            self._output("El nombre no puede estar vacío. Inténtalo de nuevo.")

    def _ask_service(self) -> str:
        """Muestra los servicios numerados y pide uno hasta que sea válido.

        Acepta el número del menú (1-3) o el nombre, con la misma normalización
        que BranchQueue (normalize_service).
        """
        for index, service in enumerate(VALID_SERVICES, start=1):
            self._output(f"  {index}. {service}")
        while True:
            answer = self._input("Servicio (número o nombre): ").strip()
            if answer in self.SERVICE_BY_NUMBER:
                return self.SERVICE_BY_NUMBER[answer]
            try:
                return normalize_service(answer)
            except InvalidServiceError as error:
                self._output(f"Error: {error} Inténtalo de nuevo.")

    def _issue_ticket(self) -> None:
        name = self._ask_name()
        service = self._ask_service()
        ticket = self.queue.issue_ticket(name, service)
        self._output(f"Ticket emitido: {ticket}")

    def _call_next(self) -> None:
        ticket = self.queue.call_next(self._ask_service())
        self._output(f"Llamando a: {ticket}")

    def _peek_next(self) -> None:
        ticket = self.queue.peek_next(self._ask_service())
        if ticket is None:
            self._output("No hay clientes en espera")
        else:
            self._output(f"Siguiente: {ticket}")

    def _list_waiting(self) -> None:
        for service, tickets in self.queue.list_waiting().items():
            self._output(f"{service}:")
            if not tickets:
                self._output("  (vacía)")
            for ticket in tickets:
                self._output(f"  {ticket}")

    def _stats(self) -> None:
        stats = self.queue.stats()
        total = stats.pop("total")
        for service, count in stats.items():
            self._output(f"  {service}: {count}")
        self._output(f"  total: {total}")


def main() -> None:
    BranchQueueCLI().run()


if __name__ == "__main__":
    main()
