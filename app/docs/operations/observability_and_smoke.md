# WP-04/P0-D · Observabilidad y smoke

## Contrato de eventos

`apps.core.observability.emit` genera JSON en `sooi.operations`. Sólo acepta una
lista cerrada de claves y valores técnicos. Eventos, componentes, operaciones,
estados de proveedor y códigos de motivo usan catálogos cerrados. Rechaza campos desconocidos para impedir que
nombres, emails, teléfonos, direcciones, mensajes, texto libre, URLs o
credenciales lleguen al log. La correlación acepta únicamente identificadores
alfanuméricos acotados o genera un UUID aleatorio.

Estados válidos: `success`, `failure`, `partial` y `retry`.

La sincronización devuelve JSON estructurado por cuenta y un estado agregado:
todo correcto es `success`; una mezcla con éxito es `partial`; sólo fallos
transitorios es `retry`; el resto es `failure`. La vista conserva esa semántica.

Señales cubiertas:

- IMAP/inbox: sincronización, alias cruzado, proveedor no disponible, descarte y conversión.
- búsquedas/runs/Celery: resultado, parcial, fallo y reintento de tarea.
- conversión de captación: creación o replay idempotente.
- web/base de datos: health y readiness.
- SMTP: aceptación, rechazo inmediato y error del proveedor para bienvenida y
  aviso de demo. No equivale a entrega final ni persiste reconciliación; esos
  estados pertenecen a WP-06 y no se simulan aquí.

No se persisten eventos DH-11: la revisión de privacidad previa sigue siendo un
gate. El log técnico debe retenerse como máximo 30 días según DH-11; el backend de
operación debe imponer esa retención antes de cualquier piloto.

## Endpoints

- `GET /health/`: vida del proceso, no consulta proveedores.
- `GET /ready/`: ejecuta `SELECT 1`; responde 200/`ready` o 503/`unavailable`.

No devuelve configuración, versión, host, excepción ni credenciales.

## Smoke determinista local

Ejecutar `ops/tests/run_wp03_wp04_tests.sh`. Usa SQLite en memoria, backend de
email local y usuarios sintéticos. Comprueba health, readiness, proveedor
indisponible y dashboard autenticado. No usa red, producción ni staging.

## Operación y parada

Parar la candidata ante acceso cruzado, PII en logs, readiness 503 sostenido,
conversión duplicada, reintentos sin límite operativo, fallo crítico de smoke o
credenciales observadas. Los logs son señales operativas, no prueba de entrega ni
analítica de producto.
