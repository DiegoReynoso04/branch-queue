"""Tests de branch-queue.py (solo biblioteca estándar).

Ejecutar desde la raíz del proyecto:
    python -m unittest discover -s tests -v
"""

import importlib.util
import sys
import threading
import time
import unittest
from collections import deque
from collections.abc import Callable
from pathlib import Path

# branch-queue.py lleva guion, así que no se puede hacer "import branch-queue".
_MODULE_PATH = Path(__file__).resolve().parent.parent / "branch-queue.py"
_spec = importlib.util.spec_from_file_location("branch_queue", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
bq = importlib.util.module_from_spec(_spec)
sys.modules["branch_queue"] = bq  # dataclasses resuelve el módulo por su nombre
_spec.loader.exec_module(bq)


class ScriptedIO:
    """Convierte una lista de respuestas en input_fn y captura output_fn.

    Cuando se agotan las respuestas lanza EOFError, igual que Ctrl+D.
    """

    def __init__(self, answers: list[str]) -> None:
        self._answers = iter(answers)
        self.prompts: list[str] = []
        self.output: list[str] = []

    def input_fn(self, prompt: str) -> str:
        self.prompts.append(prompt)
        try:
            return next(self._answers)
        except StopIteration:
            raise EOFError from None

    def output_fn(self, text: str) -> None:
        self.output.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.output)


def raising(exc: type[BaseException]) -> Callable[[str], str]:
    """input_fn falso que lanza la excepción indicada."""

    def fn(prompt: str) -> str:
        raise exc

    return fn


class BranchQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = bq.BranchQueue()

    def test_numbers_are_global_sequential_and_unique(self) -> None:
        services = ["deposito", "retiro", "deposito", "gestion_cuenta"]
        numbers = [self.queue.issue_ticket(f"c{i}", s).number for i, s in enumerate(services)]
        self.assertEqual(numbers, [1, 2, 3, 4])

    def test_fifo_within_each_service(self) -> None:
        for name in ("Ana", "Luis", "Marta"):
            self.queue.issue_ticket(name, "deposito")
        self.queue.issue_ticket("Otro", "retiro")
        called = [self.queue.call_next("deposito").client_name for _ in range(3)]
        self.assertEqual(called, ["Ana", "Luis", "Marta"])

    def test_call_next_does_not_affect_other_queues(self) -> None:
        self.queue.issue_ticket("Ana", "deposito")
        self.queue.issue_ticket("Luis", "retiro")
        self.queue.issue_ticket("Marta", "gestion_cuenta")
        self.queue.call_next("deposito")
        self.assertEqual(
            self.queue.stats(),
            {"deposito": 0, "retiro": 1, "gestion_cuenta": 1, "total": 2},
        )
        self.assertEqual(self.queue.peek_next("retiro").client_name, "Luis")
        self.assertEqual(self.queue.peek_next("gestion_cuenta").client_name, "Marta")

    def test_empty_queue_call_raises_and_peek_returns_none(self) -> None:
        with self.assertRaises(bq.EmptyQueueError) as ctx:
            self.queue.call_next("retiro")
        self.assertEqual(ctx.exception.service, "retiro")
        self.assertIsNone(self.queue.peek_next("retiro"))

    def test_peek_next_does_not_modify_state(self) -> None:
        self.queue.issue_ticket("Ana", "deposito")
        self.queue.issue_ticket("Luis", "deposito")
        before = self.queue.stats()
        first = self.queue.peek_next("deposito")
        second = self.queue.peek_next("deposito")
        self.assertEqual(self.queue.stats(), before)
        self.assertIs(first, second)
        self.assertIs(self.queue.call_next("deposito"), first)

    def test_invalid_service_rejected_everywhere(self) -> None:
        operations = {
            "issue_ticket": lambda: self.queue.issue_ticket("Ana", "hipoteca"),
            "call_next": lambda: self.queue.call_next("hipoteca"),
            "peek_next": lambda: self.queue.peek_next("hipoteca"),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name):
                with self.assertRaises(bq.InvalidServiceError) as ctx:
                    operation()
                message = str(ctx.exception)
                for service in bq.VALID_SERVICES:
                    self.assertIn(service, message)
        self.assertEqual(self.queue.stats()["total"], 0)

    def test_list_waiting_returns_copies(self) -> None:
        self.queue.issue_ticket("Ana", "deposito")
        self.queue.issue_ticket("Luis", "deposito")
        waiting = self.queue.list_waiting()
        self.assertEqual(set(waiting), set(bq.VALID_SERVICES))
        self.assertEqual([t.client_name for t in waiting["deposito"]], ["Ana", "Luis"])
        waiting["deposito"].clear()
        waiting["retiro"].append("basura")
        self.assertEqual(self.queue.stats()["deposito"], 2)
        self.assertEqual(self.queue.list_waiting()["retiro"], [])

    def test_stats_adds_up(self) -> None:
        for service, count in (("deposito", 3), ("retiro", 1), ("gestion_cuenta", 2)):
            for i in range(count):
                self.queue.issue_ticket(f"c{i}", service)
        stats = self.queue.stats()
        self.assertEqual(stats, {"deposito": 3, "retiro": 1, "gestion_cuenta": 2, "total": 6})
        self.assertEqual(stats["total"], sum(stats[s] for s in bq.VALID_SERVICES))

    def _drain_with_two_agents(self) -> tuple[list[int], list[BaseException]]:
        """Dos hilos llaman a call_next("retiro") hasta vaciar la cola.

        Devuelve los números llamados y cualquier excepción inesperada de los
        hilos (si no se recogieran, un hilo moriría en silencio y el test pasaría).
        """
        barrier = threading.Barrier(2)
        results: list[list[int]] = [[], []]
        errors: list[BaseException] = []

        def agent(slot: int) -> None:
            barrier.wait()  # los dos hilos arrancan a la vez
            while True:
                try:
                    results[slot].append(self.queue.call_next("retiro").number)
                except bq.EmptyQueueError:
                    return
                except Exception as error:  # se comprueba en el test
                    errors.append(error)
                    return

        old_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)  # fuerza muchos cambios de hilo
        try:
            threads = [threading.Thread(target=agent, args=(i,)) for i in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            sys.setswitchinterval(old_interval)
        return results[0] + results[1], errors

    def test_concurrent_call_next_has_no_duplicates(self) -> None:
        n = 5000
        for i in range(n):
            self.queue.issue_ticket(f"c{i}", "retiro")
        called, errors = self._drain_with_two_agents()
        self.assertEqual(errors, [])
        self.assertEqual(len(called), n)
        self.assertEqual(set(called), set(range(1, n + 1)))
        self.assertEqual(self.queue.stats()["total"], 0)

    def test_concurrent_call_next_check_and_pop_are_atomic(self) -> None:
        """Agranda a propósito el hueco entre "¿hay tickets?" y popleft().

        popleft() ya es atómico en CPython, así que la carrera real no produce
        duplicados: sin el lock, los dos hilos ven el único ticket y el segundo
        falla con IndexError. Con el lock, el segundo recibe EmptyQueueError.
        """

        class SlowDeque(deque):
            def popleft(self):  # type: ignore[override]
                time.sleep(0.01)  # el otro hilo comprueba la cola mientras tanto
                return super().popleft()

        self.queue._queues["retiro"] = SlowDeque()
        for round_number in range(5):
            with self.subTest(round=round_number):
                self.queue.issue_ticket(f"c{round_number}", "retiro")
                called, errors = self._drain_with_two_agents()
                self.assertEqual(errors, [])
                self.assertEqual(len(called), 1)


class NormalizeServiceTests(unittest.TestCase):
    def test_accepts_spaces_and_case(self) -> None:
        self.assertEqual(bq.normalize_service(" Retiro "), "retiro")

    def test_rejects_invalid(self) -> None:
        for value in ("", "4", "hipoteca"):
            with self.subTest(value=value):
                with self.assertRaises(bq.InvalidServiceError):
                    bq.normalize_service(value)


class BranchQueueCLITests(unittest.TestCase):
    def run_cli(self, answers: list[str]) -> tuple[ScriptedIO, "bq.BranchQueue"]:
        io = ScriptedIO(answers)
        queue = bq.BranchQueue()
        bq.BranchQueueCLI(queue, io.input_fn, io.output_fn).run()
        return io, queue

    def test_session_with_garbage_input_ends_cleanly(self) -> None:
        io, queue = self.run_cli([
            "abc", "", "99", "-1",          # opciones basura
            "1", "", "Ana", "hipoteca", "1",  # nombre vacío y servicio inválido
            "4",                             # lista de espera
            "5",                             # estadísticas
            "2", "3",                        # llamar en gestion_cuenta (vacía)
            "3", "2",                        # peek en retiro (vacía)
            "0",
        ])
        self.assertEqual(io.text.count("Opción no válida"), 4)
        self.assertIn("El nombre no puede estar vacío", io.text)
        self.assertIn("Servicio inválido: 'hipoteca'", io.text)
        self.assertIn("Ticket emitido: #1 · Ana · deposito", io.text)
        self.assertIn("(vacía)", io.text)
        self.assertIn("Aviso: No hay clientes esperando para el servicio 'gestion_cuenta'", io.text)
        self.assertIn("No hay clientes en espera", io.text)
        self.assertIn("  total: 1", io.text)
        self.assertEqual(io.output[-1], "¡Hasta luego!")
        self.assertEqual(queue.stats()["total"], 1)

    def test_invalid_service_reprompts_and_issues_one_ticket(self) -> None:
        io, queue = self.run_cli(["1", "Ana López", "hipoteca", "retiro", "0"])
        self.assertIn("Servicio inválido: 'hipoteca'", io.text)
        self.assertEqual(io.prompts.count("Servicio (número o nombre): "), 2)
        self.assertEqual(queue.stats()["total"], 1)
        self.assertEqual(queue.peek_next("retiro").client_name, "Ana López")

    def test_exhausted_answers_end_like_ctrl_d(self) -> None:
        io, _ = self.run_cli(["5"])
        self.assertIn("Sesión terminada", io.output[-1])

    def test_keyboard_interrupt_exits_cleanly(self) -> None:
        output: list[str] = []
        bq.BranchQueueCLI(input_fn=raising(KeyboardInterrupt), output_fn=output.append).run()
        self.assertIn("Sesión terminada", output[-1])

    def test_eof_exits_cleanly(self) -> None:
        output: list[str] = []
        bq.BranchQueueCLI(input_fn=raising(EOFError), output_fn=output.append).run()
        self.assertIn("Sesión terminada", output[-1])


if __name__ == "__main__":
    unittest.main()
