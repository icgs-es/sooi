# WP-10 · Matriz operativa integral

| Dominio | Contrato principal | Evidencia | Smoke / rollback |
|---|---|---|---|
| Seguridad y configuración | WP-01/WP-02 | `tests_wp01_wp02` y configuración segura | check fail-closed; rollback de artefacto |
| Roles y ownership | WP-03 | aislamiento por propietario | acceso cruzado denegado |
| Trial y entitlements | WP-05/WP-06 | enforcement servidor | operación permitida o denegada |
| Búsquedas | WP-03/WP-08/WP-09 | ownership y primer hito | navegación al siguiente paso |
| Runs | WP-03/WP-04 | idempotencia y reintento acotado | estado parcial explícito |
| Captación | WP-03/WP-08/WP-09 | captura propiedad del usuario | revisión de captación |
| Inbox | WP-03/WP-04 | aislamiento, locks e idempotencia | fallo cerrado por cuenta |
| Conversión | WP-03 | email y captura a oportunidad | replay sin duplicados |
| Oportunidades | WP-03/WP-08/WP-09 | ownership y siguiente acción | próxima acción visible |
| Seguimiento, tareas y agenda | WP-08/WP-09 | tarea fechada y deduplicación | recorrido canónico completo |
| Demo y notificaciones | WP-05/WP-07/WP-09 | persistencia antes de notificar | fallo SMTP no pierde solicitud |
| Bienvenida y activación | WP-08 | flag independiente y CTA único | sin JavaScript obligatorio |
| Métricas | WP-09 | agregación diaria y supresión | flag apagado sin escrituras |
| Privacidad | WP-04/WP-09/WP-10 | allowlist y fixtures sintéticas | rechazo de PII |
| Smoke | WP-04/WP-10 | health, readiness y comercial | salida determinista |
| Rollback | runbooks existentes y WP-10 | criterios de parada | sin producción automática |

## Resultado exigido

El gate integral debe finalizar con PostgreSQL 16, 125 pruebas correctas,
cero puertos publicados, cleanup verificado y evidencias con checksum.

## Criterios de aborto

Cualquier diferencia de alcance, dependencia externa, PII no clasificada,
migración pendiente, fallo de smoke o limpieza incompleta bloquea el gate.

## Límites

Sin staging, sin producción, sin promoción y sin actualización de ICGS Hub.
No modifica modelos, migraciones, lógica de negocio, flags ni métricas WP-09.
