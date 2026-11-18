# Banco Meridional — branch queue manager

*Leer en español: [README.es.md](README.es.md)*

A terminal queue manager for Banco Meridional branches. Customers take a
numbered ticket for one service (`deposito`, `retiro` or `gestion_cuenta`).
Each agent serves a single service and calls the customer who has waited
longest for that service.

- One FIFO queue per service, so calling the next customer is O(1).
- Ticket numbers are global and unique across the whole branch.
- `call_next` is thread-safe: two agents for the same service never call the
  same customer.

## Requirements

- Python 3.10 or newer.
- No dependencies: only the standard library. No `pip install` needed.

## Run the CLI

From the project root:

```bash
python branch-queue.py
```

(Use `python3` or `py` if that is how Python is started on your system.)

Menu:

```
1. Emitir ticket          issue a ticket (name + service, by number or name)
2. Llamar al siguiente    call the next customer for a service
3. Ver siguiente          see who is next, without calling them
4. Lista de espera        waiting list grouped by service
5. Estadísticas           number waiting per service and in total
0. Salir                  exit (Ctrl+C / Ctrl+D also exit cleanly)
```

## Run the tests

From the project root:

```bash
python -m unittest discover -s tests -v
```

The tests use only `unittest`. Because the module file name has a hyphen, they
load `branch-queue.py` with `importlib`.

## Project layout

```
branch-queue.py            Ticket, BranchQueue, BranchQueueCLI and main()
tests/test_branch_queue.py unittest suite
DESIGN.md / DESIGN.es.md   design notes
README.md / README.es.md   this file
```

## Design

The data structure, the global counter, how `call_next` handles two agents at
once, and the edge cases are explained in [DESIGN.md](DESIGN.md)
([en español](DESIGN.es.md)).
