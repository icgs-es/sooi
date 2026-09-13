# WP-09: métricas de producto diarias

WP-09 V1 produce exclusivamente conteos agregados por día natural de
`Europe/Madrid`. Está desactivado por defecto mediante
`SOOI_WP09_AGGREGATION_ENABLED` y es independiente de WP-08. No contiene
telemetría frontend, identidad, contenido, integraciones de terceros, API,
dashboard, exportador ni scheduler.

El catálogo persistido es: registro completado; primera búsqueda, captación,
oportunidad y próxima acción fechada; activación canónica; trial expirado; y
solicitudes de demo pendientes, notificadas o fallidas. `all` se calcula para
todas. `signup_source` sólo se usa para registro, activación y trial; sus únicos
valores son `self_service` y `unknown`. `profile_type` sólo se usa para demos y
acepta los cinco valores cerrados del formulario y `unknown`. No hay cruces.

Las primeras fechas se obtienen con `EXISTS` por conjuntos. Captaciones y
oportunidades exigen propietario coherente; una relación de búsqueda presente
también debe ser propia. La primera acción combina tareas con `due_date` y
oportunidades con `next_review_at`; excluye la tarea automática `review`
vinculada para no duplicarla. La activación ocurre en el máximo de las cuatro
primeras fechas. El trial se atribuye a `trial_end` cuando `is_trial=True`, sin
inferir conversión. `notified_at` fecha la aceptación SMTP, no una entrega.

Pendientes y fallidas son snapshots del estado actual y sólo se admiten para el
último día cerrado; no son eventos históricos. Nunca se consulta ni copia
`last_notification_error`.

La lectura pública aplica k=10. De 0 a 9 devuelve `suppressed`, nunca cero ni el
valor real. Si una celda dimensional es pequeña se suprime el desglose completo,
impidiendo reconstruirla por resta frente al total. No se ofrecen cruces ni
granularidad menor al día.
