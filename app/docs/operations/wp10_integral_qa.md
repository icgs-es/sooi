# WP-10 · Gate integral de QA

## Propósito

Consolidar en un único gate determinista la validación de SOOI Next Safe
Version V1 desde WP-01 hasta WP-10. El gate no promueve, no despliega y no
actualiza ICGS Hub.

## Base y alcance

La base mínima exigida es el commit
`dd0dfa834a34a54cecac3b025975130a02f5088e`. El runner acepta esa base con
los seis archivos WP-10 pendientes de commit o un descendiente limpio que
los contenga.

El gate ejecuta:

1. `manage.py check`;
2. `makemigrations --check --dry-run`;
3. migraciones completas;
4. suites WP-01 a WP-10;
5. comprobación de exactamente 125 pruebas;
6. verificación de PostgreSQL 16 sin puertos publicados;
7. limpieza, receipt, checksums y bundle.

## Entorno aislado

PostgreSQL 16 se inicia con `--network none`, sin puertos publicados y con
almacenamiento temporal. Django se conecta mediante un socket Unix temporal.
No se realiza `pull` de imágenes.

SMTP usa `locmem`, Celery usa memoria, la caché es local y Sentry queda
desactivado. Las variables proxy apuntan a un destino local inválido para
evitar dependencias HTTP accidentales.

## Datos

Todas las fixtures deben ser sintéticas. Está prohibido copiar datos reales,
exports de producción, correos personales, teléfonos reales, URLs operativas,
tokens, secretos, mensajes o texto libre procedente de usuarios.

## Evidencias

El runner genera bajo `/home/atlas-agent/atlas_backups`:

- log integral;
- resultados de check, migraciones y tests;
- inspección del contenedor PostgreSQL;
- receipt JSON;
- `SHA256SUMS`;
- bundle comprimido y checksum.

## Criterios de aborto

Abortar si:

- el commit base no pertenece a la cadena;
- existen cambios ajenos a los seis archivos WP-10;
- falta la imagen local `postgres:16`;
- aparece cualquier puerto publicado;
- check, migraciones o tests fallan;
- el total no es 125 pruebas;
- la limpieza del contenedor o del temporal no puede verificarse.

## Exclusiones

Sin staging, sin producción, sin promoción, sin push, sin migración real,
sin backfill real y sin actualización de Hub.
