# WP-10 · Contrato de fixtures sintéticas

## Regla

Las suites WP-01 a WP-10 se ejecutan exclusivamente con datos sintéticos.
No se permiten datos reales ni copias parciales de producción.

## Identificadores permitidos

- Emails: dominios reservados terminados en `.test` o `.invalid`.
- URLs: hosts reservados terminados en `.test` o `.invalid`.
- Secretos: cadenas explícitamente sintéticas y no reutilizables.
- Teléfonos: sólo valores deliberadamente ficticios incluidos en una allowlist
  por SHA-256, sin publicar el literal en la documentación.

El único literal telefónico detectado en las suites anteriores a WP-10 tiene
SHA-256:

`cb24629d1dbeb6ee24e7c20610896274e8102e67aa6efc2f3a1be2893c38008b`

Su uso está limitado a la prueba que demuestra el rechazo de PII en
observabilidad. No se interpreta como dato de contacto.

## Prohibiciones

No usar:

- nombres, emails o teléfonos reales;
- direcciones o URLs operativas;
- mensajes de usuarios;
- prompts;
- IP públicas;
- exports o backups;
- credenciales, tokens o cookies;
- excepciones copiadas de producción.

## Verificación

`tests_wp10/test_integral_contracts.py` escanea los ocho módulos canónicos
anteriores, exige dominios reservados y valida el hash allowlisted del
teléfono. Cualquier literal nuevo queda bloqueado hasta revisión humana.

## Dependencias

El runner fuerza correo local, Celery en memoria, caché local y Sentry vacío.
PostgreSQL 16 es efímero y sólo se accede mediante socket Unix.

## Criterios de aborto

Abortar ante cualquier dato no clasificado, dependencia externa obligatoria,
lectura de datos reales o intento de conexión operativa.

## Límites

Sin staging, sin producción y sin promoción. El gate no accede a `/opt/sooi`
ni a ICGS Hub.
