# WP-03/P0-C · Aislamiento e idempotencia del inbox

## Invariantes

- Toda cuenta, mensaje, búsqueda y captación se resuelve por `owner`.
- Listado, detalle, descarte y conversión filtran por el usuario autenticado.
- La sincronización iniciada desde web sólo procesa cuentas del usuario.
- Un alias de búsqueda que resuelva otro propietario se rechaza antes de persistir.
- Staff no amplía el ámbito. Sólo superusuario tiene bypass explícito en admin.
- Una conversión repetida devuelve la captación ya vinculada; no la sobrescribe.
- La conversión bloquea el mensaje dentro de una transacción y falla cerrada si
  cuenta, mensaje o captación no tienen el mismo propietario.

Los registros históricos incoherentes siguen siendo legibles por consulta directa
de mantenimiento, pero no pueden convertirse ni reasignarse por estos flujos.
La reasignación no se ofrece como operación ordinaria. El servicio interno
`reassign_inbound_email` exige superusuario explícito, bloquea la fila, toma el
propietario de la cuenta destino, desacopla búsquedas incompatibles y rechaza
mensajes ya convertidos. No constituye autorización para ejecutarlo en producción.

## Credenciales IMAP

`EmailAccount` sólo conserva `imap_secret_ref`, un identificador opaco no
secreto. `SOOI_IMAP_SECRET_DIR` debe señalar explícitamente un directorio
absoluto montado; no tiene valor por defecto operativo. La referencia admite
únicamente minúsculas ASCII, dígitos, punto, guion y guion bajo, y rechaza rutas
absolutas y traversal. El resolvedor abre el fichero relativo al descriptor del
directorio, no sigue enlaces, exige fichero regular sin permisos de grupo/otros
y falla con `imap_secret_unavailable` sin registrar ruta ni contenido.

Antes de aplicar las migraciones fuera de pruebas, un humano debe aprovisionar
un fichero `imap-account-<id>` por cada cuenta histórica, con modo `0600` o más
restrictivo y el propietario efectivo del proceso. No existe fallback al campo
heredado ni criptografía propia.

## Comprobación

La suite prueba dos usuarios, staff sin bypass, acceso directo por identificador,
listado, sincronización acotada, incoherencia de propietario e idempotencia. Los
datos usados son sintéticos. También prueba referencia válida, traversal,
permisos, ausencia y eliminación del campo heredado sobre SQLite efímero.
