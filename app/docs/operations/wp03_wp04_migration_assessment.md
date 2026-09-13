# WP-03/P0-C + WP-04/P0-D · Evaluación de migraciones

## Decisión

Este paquete crea, pero no autoriza ejecutar fuera de pruebas, dos migraciones:

- `0002` añade `imap_secret_ref` y asigna `imap-account-<id>` a cada fila;
- `0003` vacía irreversiblemente `imap_password`, retira la columna y hace
  obligatoria la referencia.

Se validan únicamente sobre la base SQLite efímera creada por Django.

## Precondición humana obligatoria

Antes de migrar cualquier entorno no efímero se debe inventariar cada ID y
aprovisionar, en el directorio montado configurado por `SOOI_IMAP_SECRET_DIR`,
el fichero `imap-account-<id>` correspondiente con permisos restrictivos. Debe
probarse lectura por el usuario del proceso sin imprimir contenido ni ruta. Sin
esa reconciliación, la sincronización queda bloqueada de forma intencionada.

## Colisiones que deben analizarse antes de una migración futura

- duplicados `(account_id, message_uid)` cuando UID no está vacío;
- duplicados `(account_id, message_id)` cuando Message-ID no está vacío;
- mensajes cuyo `owner_id` difiere de `account.owner_id`;
- búsquedas o captaciones vinculadas con propietario distinto;
- mensajes convertidos sin captación o varias referencias al mismo origen;
- cuentas sin propietario válido o sin fichero secreto aprovisionado.

No se dispone de copia autorizada de datos reales, por lo que no se afirma que
esas colisiones sean cero.

## Restricciones futuras no incluidas

Tras auditoría sobre copia efímera autorizada: reconciliar sin borrar historia,
añadir restricciones únicas condicionales para UID y Message-ID no vacíos. Una
restricción irreversible exige backup, restore probado y rollback humano.

Compatibilidad: la aplicación actual admite filas históricas sin claves nuevas y
falla cerrada al intentar operar relaciones incoherentes.
