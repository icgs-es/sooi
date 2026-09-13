# WP-03/P0-C + WP-04/P0-D · Rollback

## Alcance y flags reversibles

Hay dos migraciones locales expand/data/contract para retirar la contraseña en
claro. La reversión de aplicación no reconstruye secretos eliminados y no debe
reintroducir `imap_password`. El
nivel de logs se controla con `SOOI_OBSERVABILITY_LOG_LEVEL`; no desactiva las
reglas de ownership.

No existe un flag que permita acceso cruzado ni que relaje la sanitización. Si la
observabilidad causa presión operativa, elevar el nivel por configuración es la
única mitigación reversible aceptada.

## Procedimiento futuro autorizado

1. Detener nuevas sincronizaciones y conversiones.
2. Confirmar que no quedan workers del artefacto nuevo ejecutando tareas.
3. Restaurar exactamente el artefacto anterior común a web y worker.
4. No revertir `0003` ni editar datos del inbox. Mantener los ficheros montados;
   un artefacto anterior incompatible no es un destino de rollback seguro.
5. Ejecutar health y el smoke público/autenticado sintético.
6. Reconciliar mensajes creados durante la ventana por cuenta y UID/Message-ID.

## Criterios de abortar

Abortar y escalar si aparecen propietarios incoherentes, mensajes duplicados,
captaciones perdidas, PII en logs, divergencia web/worker o si no puede
identificarse un artefacto compatible con `imap_secret_ref`. Este runbook no autoriza staging, promoción,
producción ni despliegue.
