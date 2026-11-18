# Notas de diseño: gestor de colas de Banco Meridional

*Read in English: [DESIGN.md](DESIGN.md)*

Los clientes toman un ticket numerado para un servicio (`deposito`, `retiro`,
`gestion_cuenta`). Cada agente atiende un solo servicio y llama al cliente que
más tiempo lleva esperando **para ese servicio**. Todo el código está en
[`branch-queue.py`](branch-queue.py) y los tests en
[`tests/test_branch_queue.py`](tests/test_branch_queue.py).

## 1. Estructura de datos: `dict[str, deque[Ticket]]`

`BranchQueue` guarda una cola FIFO por servicio:

```python
self._queues: dict[str, deque[Ticket]] = {s: deque() for s in VALID_SERVICES}
```

La alternativa obvia es una única lista con todos los tickets en orden de
llegada. La diferencia aparece en `call_next(servicio)`:

| Operación | Una `deque` por servicio | Una lista única compartida |
|---|---|---|
| `issue_ticket` | O(1): `append` | O(1): `append` |
| `call_next(s)` | **O(1)**: `popleft` | **O(n)** para buscar el primer ticket de `s` **+ O(n)** de `list.pop(i)`, que desplaza todos los elementos posteriores |
| `peek_next(s)` | O(1): `queue[0]` | O(n): recorrido |
| `stats` | O(k) = O(1): `len` de cada deque | O(n): contar todo |
| `list_waiting` | O(n): copia todos los tickets | O(n) |

*n = clientes en espera entre todos los servicios; k = número de servicios (3, fijo).*

**El coste de un servicio no depende de los demás servicios.** Con una lista
compartida, si 500 personas esperan para `deposito` y una persona para `retiro`
está al final, el agente de `retiro` recorre 500 tickets en cada llamada. Con
una deque por servicio, llamar al siguiente cliente de `retiro` es un solo
`popleft()`, sea cual sea la longitud de las otras colas. La búsqueda en el dict
`self._queues[service]` es O(1).

**Por qué `deque` y no `list`.** Un `dict[str, list]` seguiría siendo mala
elección para una cola: `list.pop(0)` es O(m), porque desplaza una posición a la
izquierda los m − 1 elementos restantes. `collections.deque` está pensada para
operar por los dos extremos: `append`, `popleft` y leer `queue[0]` son O(1). La
memoria es O(n) en ambos casos.

Lo que se pierde es un único orden global de llegada, pero el número global de
ticket permite reconstruirlo (ver la sección 2).

## 2. Un contador global, no uno por cola

`self._next_number` lo comparten todos los servicios. Los tickets de distintos
servicios se intercalan: `deposito` #1, `retiro` #2, `deposito` #3,
`gestion_cuenta` #4.

- **Un número por cliente en la sucursal.** La pantalla puede anunciar "#7" sin
  ambigüedad. Con un contador por cola habría tres "#1" a la vez, y harían falta
  prefijos como `D-1` / `R-1` para distinguirlos.
- **El número registra el orden global de llegada.** Ordenar por número entre
  colas devuelve el orden exacto de llegada. Sirve para informes o para un futuro
  puesto que atienda cualquier servicio, sin guardar una lista aparte.
- **FIFO equivale a número ascendente.** El número se asigna y el ticket se
  añade bajo el mismo lock, así que cada deque está siempre ordenada por número.
- **Sincronización sencilla.** El contador lo protege el mismo lock que protege
  las deques, así que no hace falta otro lock.

Coste: los números dentro de una cola tienen huecos (`deposito`: 1, 3, 4…), así
que un cliente no puede saber su posición solo por su número. Esa información
la dan "Ver siguiente" (`peek_next`) y la lista de espera (`list_waiting`).

## 3. `call_next` concurrente con dos agentes del mismo servicio

Dos agentes de `retiro` pueden pulsar "llamar al siguiente" en el mismo momento.
Llamar a un cliente es una secuencia *check-then-act*: mirar la cola y después
sacar algo de ella. Si son pasos separados y sin lock, otro hilo puede ejecutarse
entre ambos. Hay dos variantes de esta carrera.

### a) Comprobar `if queue` y luego hacer `popleft()` por separado → `IndexError`

```python
# SIN LOCK: incorrecto
if not queue:
    raise EmptyQueueError(service)
return queue.popleft()
```

Queda un solo ticket (#7):

| Paso | Agente A | Agente B |
|---|---|---|
| 1 | `if not queue` → 1 ticket, sigue | |
| 2 | | `if not queue` → 1 ticket, sigue |
| 3 | `popleft()` → #7 | |
| 4 | | `popleft()` → **`IndexError: pop from an empty deque`** |

El agente B recibe una excepción inesperada en vez de `EmptyQueueError`. Es la
carrera que demuestra `test_concurrent_call_next_check_and_pop_are_atomic`. Ese
test ralentiza `popleft()` a propósito para caer siempre en el hueco. Sin el lock
falla 5 de 5 veces; con el lock pasa siempre.

### b) Leer `queue[0]` para anunciar al cliente y retirarlo después → doble llamado

```python
# SIN LOCK: incorrecto
ticket = queue[0]
announce(ticket)          # "Ticket #7, Ana, pase a la ventanilla 2"
queue.popleft()
```

En la cola están #7 Ana y #9 Luis:

| Paso | Agente A (ventanilla 1) | Agente B (ventanilla 2) |
|---|---|---|
| 1 | `ticket = queue[0]` → #7 Ana | |
| 2 | | `ticket = queue[0]` → #7 Ana |
| 3 | anuncia "#7 Ana → ventanilla 1" | |
| 4 | | anuncia "#7 Ana → ventanilla 2" ← **doble llamado** |
| 5 | `popleft()` retira #7 | |
| 6 | | `popleft()` retira **a #9 Luis, a quien nunca se llamó** |

A Ana la llaman a dos ventanillas y Luis desaparece de la cola sin que nadie lo
atienda.

### La regla

1. **Comprobar y retirar en una sola sección crítica.** El primer cambio tiene
   que ser sacar el ticket de la cola. "¿Hay alguien esperando?" y `popleft()`
   ocurren juntos bajo `self._lock`:

   ```python
   with self._lock:
       queue = self._queues[service]
       if not queue:
           raise EmptyQueueError(service)
       return queue.popleft()
   ```

2. **Anunciar solo un ticket que ya se ha retirado.** `call_next` devuelve el
   ticket que retiró, y solo entonces la CLI muestra "Llamando a: …". Cuando un
   ticket ha salido de la cola, ningún otro agente puede obtenerlo. `peek_next`
   es solo informativo. Su resultado puede quedar desfasado en cuanto se libera
   el lock, así que nunca debe usarse para llamar a un cliente.
   `test_peek_next_does_not_modify_state` comprueba que de verdad solo lee.

`test_concurrent_call_next_has_no_duplicates` comprueba la garantía de punta a
punta: dos hilos vacían una cola de N = 5000 tickets, y los números llamados son
exactamente N y sin duplicados. Los dos tests de concurrencia también recogen
cualquier excepción inesperada dentro de un hilo, así que un hilo que falla no
deja pasar el test en silencio.

**¿Por qué un `Lock` explícito si `deque.popleft()` ya es atómico?** Un solo
`popleft()` no se puede interrumpir a medias en CPython (por el GIL, o por los
locks por objeto en la versión free-threaded). Eso es un detalle de
implementación, y la corrección no se apoya en él. Además, solo cubre **una**
operación. No protege el par *comprobar + retirar*, ni *leer el contador +
incrementarlo + añadir el ticket* en `issue_ticket`. Sin el lock, dos hilos
podrían leer `_next_number == 5` a la vez, y los dos clientes recibirían el
ticket #5.

Otras decisiones sobre el lock:

- **Las lecturas también usan el lock.** Copiar una deque con `list(deque)`
  mientras otro hilo la modifica lanza `RuntimeError: deque mutated during
  iteration`. `peek_next` tiene el mismo problema check-then-act que
  `call_next`. `stats` necesita que todos sus contadores sean del mismo
  instante.
- **Secciones críticas cortas.** La validación ocurre antes del lock, y dentro no
  hay E/S: la CLI imprime después de liberarlo. Un terminal lento nunca bloquea a
  otros agentes.
- **Un único lock para todo.** Todas las secciones críticas son O(1), salvo la
  copia de `list_waiting`, así que la contención es despreciable. Un lock por
  servicio seguiría necesitando proteger el contador global, lo que añade
  complejidad sin ganar nada.

### Varios procesos o quioscos (solo una nota, no implementado)

`threading.Lock` solo funciona dentro de un mismo proceso. Si varios quioscos o
servidores compartieran la cola, el paso atómico pasaría al almacén compartido:

- **Base de datos:** en una sola transacción,
  `SELECT … WHERE service = ? AND status = 'waiting' ORDER BY number LIMIT 1 FOR UPDATE SKIP LOCKED`,
  luego `UPDATE … SET status = 'called'` y luego `COMMIT`. El número global
  saldría de una secuencia o de una columna autoincremental de la base de datos.
- **Cola externa:** una lista de Redis por servicio (`LPOP` / `BLPOP` son
  atómicos) o un broker como RabbitMQ con confirmaciones.

La regla no cambia: reclamar el ticket es atómico en el almacén compartido, y al
cliente solo se le anuncia cuando esa reclamación ha tenido éxito.

## 4. Casos borde

| Caso | Cómo se gestiona |
|---|---|
| Servicio con otras mayúsculas o con espacios (`" Retiro "`) | `normalize_service` aplica `strip()` + `lower()` → `"retiro"`. |
| Servicio desconocido (`"hipoteca"`, `""`, `"4"`) o que no es texto | `InvalidServiceError` (un `ValueError`), con un mensaje que lista los servicios válidos. La CLI lo muestra y vuelve a preguntar. |
| Servicio escrito como número del menú en la CLI | `1`–`3` se traducen con `VALID_SERVICES`. Cualquier otro número pasa a la validación y se rechaza. |
| Nombre vacío o solo con espacios | `issue_ticket` lanza `ValueError`. La CLI comprueba el nombre antes y vuelve a preguntar. El nombre se guarda sin espacios al principio ni al final. |
| `call_next` con la cola vacía | `EmptyQueueError` (un `LookupError`) con `.service`. La CLI lo muestra como aviso y vuelve al menú. |
| `peek_next` con la cola vacía | Devuelve `None`. No lanza excepción, porque una cola vacía es una situación normal. |
| `list_waiting` con servicios vacíos | Todos los servicios aparecen, con `[]`. La CLI muestra "(vacía)". |
| Modificar el resultado de `list_waiting` | Se devuelven listas nuevas, así que la cola interna no se ve afectada. `Ticket` es `frozen`, así que los objetos compartidos no se pueden modificar. |
| Opción de menú inválida (texto, fuera de rango, vacía) | Mensaje "Opción no válida", y el bucle continúa. |
| Ctrl+C / Ctrl+D (o fin de la entrada) | `KeyboardInterrupt` / `EOFError` se capturan alrededor de todo el bucle. El programa muestra un mensaje y termina con código 0. |
| Dos tickets con el mismo `issued_at` | El orden lo da la posición en la deque, que sigue al número, no a la hora. `issued_at` solo se usa para mostrar. |
| Contador muy grande | Los `int` de Python no tienen límite superior. |

Los errores inesperados no se ocultan: la CLI solo captura `InvalidServiceError`,
`EmptyQueueError` y `ValueError`, nunca `Exception`.

**Fuera de alcance:** el estado vive en memoria y se pierde al cerrar el
programa. El contador no se reinicia cada día. No hay cancelación, gestión de
clientes que no se presentan ni prioridades.
