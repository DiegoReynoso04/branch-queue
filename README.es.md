# Banco Meridional: gestor de colas de sucursal

*Read in English: [README.md](README.md)*

Gestor de colas en terminal para las sucursales de Banco Meridional. Los
clientes toman un ticket numerado para un servicio (`deposito`, `retiro` o
`gestion_cuenta`). Cada agente atiende un solo servicio y llama al cliente que
más tiempo lleva esperando para ese servicio.

- Una cola FIFO por servicio, así que llamar al siguiente cliente es O(1).
- Los números de ticket son globales y únicos en toda la sucursal.
- `call_next` es seguro con hilos: dos agentes del mismo servicio nunca llaman
  al mismo cliente.

## Requisitos

- Python 3.10 o superior.
- Sin dependencias: solo la biblioteca estándar. No hace falta `pip install`.

## Ejecutar la CLI

Desde la raíz del proyecto:

```bash
python branch-queue.py
```

(Usa `python3` o `py` si así se inicia Python en tu sistema.)

Menú:

```
1. Emitir ticket          nombre + servicio (por número o por nombre)
2. Llamar al siguiente    llama al siguiente cliente de un servicio
3. Ver siguiente          muestra quién va después, sin llamarlo
4. Lista de espera        clientes en espera agrupados por servicio
5. Estadísticas           clientes en espera por servicio y en total
0. Salir                  sale (Ctrl+C / Ctrl+D también salen limpiamente)
```

## Ejecutar los tests

Desde la raíz del proyecto:

```bash
python -m unittest discover -s tests -v
```

Los tests solo usan `unittest`. Como el nombre del archivo lleva guion, cargan
`branch-queue.py` con `importlib`.

## Estructura del proyecto

```
branch-queue.py            Ticket, BranchQueue, BranchQueueCLI y main()
tests/test_branch_queue.py tests con unittest
DESIGN.md / DESIGN.es.md   notas de diseño
README.md / README.es.md   este archivo
```

## Diseño

La estructura de datos, el contador global, cómo gestiona `call_next` a dos
agentes a la vez y los casos borde se explican en [DESIGN.es.md](DESIGN.es.md)
([in English](DESIGN.md)).
