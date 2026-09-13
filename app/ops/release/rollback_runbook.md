# Rollback aislado de SOOI

Este procedimiento diseña el rollback; no autoriza staging, promoción ni producción.

## Entradas obligatorias

- manifiesto y SHA-256 verificados del candidato desplegado;
- identidad inmutable (`sha256:…`) de la imagen anterior aprobada;
- snapshot de la configuración anterior, mantenido fuera de la imagen y sin secretos en evidencias;
- responsable humano, ventana y criterio de parada autorizados.

## Ensayo local

1. Inspeccionar la imagen anterior por identidad: `docker image inspect sha256:<id>`.
2. Configurar `IMAGE_TAG=sha256:<id-anterior>` y un `ENV_FILE` sintético.
3. Renderizar `compose.candidate.yml` y confirmar que web, worker y beat resuelven a esa misma identidad.
4. Ejecutar `verify_candidate.sh` contra la identidad anterior en aislamiento y sin red.
5. Registrar identidad, hash de configuración no secreta, tiempos y resultado. No ejecutar migraciones.

## Procedimiento autorizado futuro

Detener nuevas ejecuciones, sustituir simultáneamente web, worker y beat por la identidad anterior y restaurar la configuración anterior desde el gestor autorizado. Verificar salud, login, colas y versión. Si existe incompatibilidad de datos, detenerse: este runbook no autoriza rollback de datos ni migraciones.

## Criterios de aborto

- la imagen no está disponible por su identidad inmutable;
- web, worker y beat divergen;
- falta la configuración anterior o su hash no coincide;
- se requiere modificar datos o ejecutar una migración;
- falla una comprobación de seguridad o salud.
