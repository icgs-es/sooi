# WP-09: backfill, retención y rollback

El comando exige `--start-date` y `--end-date`, ambos inclusivos, y sólo acepta
días cerrados. Cada día/version se reemplaza dentro de una transacción; la clave
única impide duplicados. Reejecutar puede cambiar resultados si hubo borrados.
Las cuentas eliminadas no se restan retroactivamente de filas ya calculadas.

El backfill de próximas acciones está limitado por tareas borradas y por
`next_review_at` sobrescrito: sólo puede demostrar el estado conservado. Los
snapshots `demo_request_pending` y `demo_request_delivery_failed` rechazan
backfill histórico y únicamente representan el último día cerrado.

Conservar agregados diarios 24 meses. Después, conservar como máximo 36 meses
adicionales sólo agregados mensuales sin dimensiones, o borrarlos. Los backups
deben estar cifrados y tener retención igual o menor. Producción, staging y test
deben permanecer completamente separados. V1 no conecta purga automática.

El rollback es no destructivo: desactivar y detener primero productor y lector,
verificar que no hay ejecuciones, y sólo entonces valorar revertir la migración.
La reversión elimina la tabla, por lo que requiere una decisión explícita de
retención/backup; normalmente se conserva mientras se valida el rollback.
